import gzip
import time

import httpx
import pytest
import respx

from sitemap_audit import __version__
from sitemap_audit.errors import SitemapError
from sitemap_audit.sitemap import FetchSettings, collect_urls, parse_sitemap

from .helpers import SITE, fixture_path, serve

ROOT = f"{SITE}/sitemap.xml"
PAGES = [
    "https://example.com/",
    "https://example.com/pricing",
    "https://example.com/about/",
    "https://example.com/contact?utm_source=newsletter",
]


def urlset(*locs: str) -> bytes:
    entries = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return (
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'.encode()
    )


def index(*locs: str) -> bytes:
    entries = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return (
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}'
        "</sitemapindex>"
    ).encode()


# --- parsing -------------------------------------------------------------------------------


def test_parse_urlset_reads_only_page_locations() -> None:
    kind, locs = parse_sitemap(fixture_path("urlset_pages.xml").read_bytes(), "pages.xml")
    assert kind == "urlset"
    # image:loc and xhtml:link alternates are not pages of their own
    assert locs == PAGES


def test_parse_sitemap_index() -> None:
    kind, locs = parse_sitemap(fixture_path("index_root.xml").read_bytes(), "index.xml")
    assert kind == "sitemapindex"
    assert locs == [
        "https://example.com/sitemaps/pages.xml",
        "https://example.com/sitemaps/blog.xml.gz",
        "https://example.com/sitemaps/more.xml",
    ]


def test_parse_urlset_without_a_namespace() -> None:
    _, locs = parse_sitemap(fixture_path("urlset_no_namespace.xml").read_bytes(), "x.xml")
    assert locs == ["https://example.com/legacy-page"]


def test_malformed_xml_is_a_sitemap_error() -> None:
    with pytest.raises(SitemapError, match=r"broken.xml: not well-formed XML"):
        parse_sitemap(fixture_path("malformed.xml").read_bytes(), "broken.xml")


def test_empty_document_is_a_sitemap_error() -> None:
    with pytest.raises(SitemapError, match="not well-formed XML"):
        parse_sitemap(b"", "empty.xml")


def test_html_page_is_not_a_sitemap() -> None:
    with pytest.raises(SitemapError, match=r"expected <urlset> or <sitemapindex> but found <html>"):
        parse_sitemap(fixture_path("not_a_sitemap.html").read_bytes(), "soft-404")


def test_entities_are_not_resolved() -> None:
    _, locs = parse_sitemap(fixture_path("xxe.xml").read_bytes(), "xxe.xml")
    assert "https://example.com/safe" in locs
    joined = "\n".join(locs)
    assert "root:" not in joined  # nothing was read from /etc/passwd
    assert "injected" not in joined  # internal entities are left alone too


# --- fetching: trees of sitemaps -----------------------------------------------------------


def test_single_sitemap(http: respx.MockRouter) -> None:
    serve(http, "/sitemap.xml", "urlset_pages.xml")
    result = collect_urls(ROOT)
    assert result.urls == PAGES
    assert result.fetched == 1
    assert result.warnings == []


def test_sitemap_index_is_followed_recursively(site: respx.MockRouter) -> None:
    result = collect_urls(ROOT)
    assert result.urls == [
        *PAGES,
        "https://example.com/blog",
        "https://example.com/blog/hello-world",
        "https://example.com/docs/start",  # index -> index -> urlset
    ]
    assert result.fetched == 5
    assert result.warnings == []


def test_gzip_is_detected_from_the_content_not_the_headers(http: respx.MockRouter) -> None:
    serve(
        http,
        "/sitemap.xml",
        "urlset_blog.xml",
        gzipped=True,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert collect_urls(ROOT).urls == [
        "https://example.com/blog",
        "https://example.com/blog/hello-world",
    ]


def test_content_encoding_gzip_is_handled_too(http: respx.MockRouter) -> None:
    serve(
        http,
        "/sitemap.xml",
        "urlset_blog.xml",
        gzipped=True,
        headers={"Content-Encoding": "gzip", "Content-Type": "application/xml"},
    )
    assert len(collect_urls(ROOT).urls) == 2


def test_truncated_gzip_is_an_error(http: respx.MockRouter) -> None:
    body = gzip.compress(fixture_path("urlset_blog.xml").read_bytes())
    http.get(ROOT).respond(200, content=body[: len(body) // 2])
    with pytest.raises(SitemapError, match="truncated"):
        collect_urls(ROOT)


def test_corrupt_gzip_is_an_error(http: respx.MockRouter) -> None:
    http.get(ROOT).respond(200, content=b"\x1f\x8b" + b"this is not deflate data")
    with pytest.raises(SitemapError, match="could not decompress"):
        collect_urls(ROOT)


def test_gzip_bomb_is_stopped(http: respx.MockRouter) -> None:
    bomb = gzip.compress(b"<urlset>" + b"a" * 5_000_000)
    assert len(bomb) < 100_000
    http.get(ROOT).respond(200, content=bomb)
    with pytest.raises(SitemapError, match="after decompression"):
        collect_urls(ROOT, FetchSettings(max_bytes=100_000))


def test_oversized_response_is_stopped(http: respx.MockRouter) -> None:
    http.get(ROOT).respond(200, content=b"x" * 5_000)
    with pytest.raises(SitemapError, match="larger than 1000 bytes"):
        collect_urls(ROOT, FetchSettings(max_bytes=1000))


def test_cycle_in_a_sitemap_index_ends_with_a_warning(http: respx.MockRouter) -> None:
    a = serve(http, "/cycle/a.xml", "index_cycle_a.xml")
    b = serve(http, "/cycle/b.xml", "index_cycle_b.xml")
    serve(http, "/sitemaps/pages.xml", "urlset_pages.xml")

    result = collect_urls(f"{SITE}/cycle/a.xml")

    assert result.urls == PAGES
    assert result.fetched == 3
    assert a.call_count == 1
    assert b.call_count == 1
    assert len(result.warnings) == 2
    assert all("cycle" in warning for warning in result.warnings)


def test_a_sitemap_listed_twice_is_read_once(http: respx.MockRouter) -> None:
    http.get(ROOT).respond(
        200, content=index(f"{SITE}/p.xml", f"{SITE}/p.xml/", f"{SITE}/p.xml?utm_source=x")
    )
    page = http.get(f"{SITE}/p.xml").respond(200, content=urlset(f"{SITE}/a"))

    result = collect_urls(ROOT)

    assert result.urls == [f"{SITE}/a"]
    assert page.call_count == 1
    assert len(result.warnings) == 2
    assert all("more than once" in warning for warning in result.warnings)


def test_index_nested_too_deeply_is_an_error(site: respx.MockRouter) -> None:
    # root index (depth 0) -> more.xml, an index (depth 1) -> deep.xml (depth 2)
    assert len(collect_urls(ROOT, FetchSettings(max_depth=2)).urls) == 7
    with pytest.raises(SitemapError, match=r"nested deeper than --max-depth \(1\)"):
        collect_urls(ROOT, FetchSettings(max_depth=1))
    with pytest.raises(SitemapError, match=r"nested deeper than --max-depth \(0\)"):
        collect_urls(ROOT, FetchSettings(max_depth=0))


def test_a_plain_sitemap_works_with_depth_zero(http: respx.MockRouter) -> None:
    serve(http, "/sitemap.xml", "urlset_pages.xml")
    assert len(collect_urls(ROOT, FetchSettings(max_depth=0)).urls) == 4


def test_too_many_urls_is_an_error_not_a_partial_result(http: respx.MockRouter) -> None:
    serve(http, "/sitemap.xml", "urlset_pages.xml")
    assert len(collect_urls(ROOT, FetchSettings(max_urls=4)).urls) == 4
    with pytest.raises(SitemapError, match=r"more than 3 page URLs"):
        collect_urls(ROOT, FetchSettings(max_urls=3))


def test_max_urls_counts_across_the_whole_tree(site: respx.MockRouter) -> None:
    with pytest.raises(SitemapError, match="more than 5 page URLs"):
        collect_urls(ROOT, FetchSettings(max_urls=5))


def test_entries_with_a_missing_or_invalid_loc_are_skipped_with_a_warning(
    http: respx.MockRouter,
) -> None:
    serve(http, "/sitemap.xml", "urlset_bad_locs.xml")
    result = collect_urls(ROOT)
    assert result.urls == ["https://example.com/ok"]
    assert result.warnings == [f"{ROOT}: skipped 4 page entries with an empty or invalid <loc>"]


def test_invalid_child_sitemap_locations_are_skipped_with_a_warning(http: respx.MockRouter) -> None:
    http.get(ROOT).respond(200, content=index("not-a-url", f"{SITE}/p.xml"))
    serve_page = http.get(f"{SITE}/p.xml").respond(200, content=urlset(f"{SITE}/a"))
    result = collect_urls(ROOT)
    assert result.urls == [f"{SITE}/a"]
    assert serve_page.call_count == 1
    assert "skipped 1 sitemap entries" in result.warnings[0]


def test_a_broken_child_stops_the_run(site: respx.MockRouter) -> None:
    site.get(f"{SITE}/sitemaps/blog.xml.gz").respond(404)
    with pytest.raises(SitemapError, match=r"blog.xml.gz: HTTP 404"):
        collect_urls(ROOT)


def test_redirects_are_followed(http: respx.MockRouter) -> None:
    http.get(ROOT).respond(301, headers={"Location": f"{SITE}/moved.xml"})
    serve(http, "/moved.xml", "urlset_blog.xml")
    assert len(collect_urls(ROOT).urls) == 2


def test_the_root_must_be_an_http_url() -> None:
    with pytest.raises(SitemapError, match="not an absolute http"):
        collect_urls("ftp://example.com/sitemap.xml")
    with pytest.raises(SitemapError, match="not an absolute http"):
        collect_urls("example.com/sitemap.xml")


# --- fetching: errors and retries ----------------------------------------------------------


def test_not_found_is_not_retried(http: respx.MockRouter) -> None:
    route = http.get(ROOT).respond(404)
    with pytest.raises(SitemapError, match="HTTP 404"):
        collect_urls(ROOT)
    assert route.call_count == 1


def test_server_errors_are_retried_until_they_clear(http: respx.MockRouter) -> None:
    route = http.get(ROOT)
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(429),
        httpx.Response(200, content=urlset(f"{SITE}/a")),
    ]
    result = collect_urls(ROOT, FetchSettings(retries=2))
    assert result.urls == [f"{SITE}/a"]
    assert route.call_count == 3


def test_retries_are_limited(http: respx.MockRouter) -> None:
    route = http.get(ROOT).respond(503)
    with pytest.raises(SitemapError, match=r"gave up after 3 attempt\(s\).*HTTP 503"):
        collect_urls(ROOT, FetchSettings(retries=2))
    assert route.call_count == 3


def test_retries_can_be_turned_off(http: respx.MockRouter) -> None:
    route = http.get(ROOT).respond(500)
    with pytest.raises(SitemapError, match=r"gave up after 1 attempt\(s\)"):
        collect_urls(ROOT, FetchSettings(retries=0))
    assert route.call_count == 1


def test_timeouts_and_connection_errors_are_retried(http: respx.MockRouter) -> None:
    route = http.get(ROOT)
    route.side_effect = [
        httpx.ReadTimeout("too slow"),
        httpx.ConnectError("refused"),
        httpx.Response(200, content=urlset(f"{SITE}/a")),
    ]
    assert collect_urls(ROOT, FetchSettings(retries=2)).urls == [f"{SITE}/a"]
    assert route.call_count == 3


def test_a_dead_server_is_a_readable_error(http: respx.MockRouter) -> None:
    http.get(ROOT).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(SitemapError, match=r"gave up after 2 attempt\(s\).*ConnectError"):
        collect_urls(ROOT, FetchSettings(retries=1))


def test_backoff_doubles_between_attempts(
    http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(time, "sleep", delays.append)
    http.get(ROOT).respond(503)
    with pytest.raises(SitemapError):
        collect_urls(ROOT, FetchSettings(retries=3, backoff=0.5))
    assert delays == [0.5, 1.0, 2.0]


def test_requests_carry_a_polite_user_agent(http: respx.MockRouter) -> None:
    route = serve(http, "/sitemap.xml", "urlset_blog.xml")
    collect_urls(ROOT)
    agent = route.calls.last.request.headers["user-agent"]
    assert agent.startswith(f"sitemap-audit/{__version__}")


def test_user_agent_can_be_replaced(http: respx.MockRouter) -> None:
    route = serve(http, "/sitemap.xml", "urlset_blog.xml")
    collect_urls(ROOT, FetchSettings(user_agent="my-bot/1.0 (ops@example.com)"))
    assert route.calls.last.request.headers["user-agent"] == "my-bot/1.0 (ops@example.com)"


def test_a_client_can_be_passed_in(http: respx.MockRouter) -> None:
    serve(http, "/sitemap.xml", "urlset_blog.xml")
    with httpx.Client() as client:
        assert len(collect_urls(ROOT, client=client).urls) == 2
