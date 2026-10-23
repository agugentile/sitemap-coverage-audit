import pytest

from sitemap_audit.errors import InputError, InvalidURLError
from sitemap_audit.normalize import compile_patterns, is_ignored, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # scheme and host are lowercased, the path keeps its case
        ("HTTPS://Example.COM/About", "https://example.com/About"),
        # fragment
        ("https://example.com/docs#install", "https://example.com/docs"),
        # trailing slash, including the site root
        ("https://example.com/", "https://example.com"),
        ("https://example.com/about/", "https://example.com/about"),
        ("https://example.com/about//", "https://example.com/about"),
        # default ports go, other ports stay
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("http://example.com:443/a", "http://example.com:443/a"),
        # surrounding whitespace
        ("  https://example.com/a  ", "https://example.com/a"),
        # tracking parameters
        ("https://example.com/a?utm_source=x&utm_medium=y", "https://example.com/a"),
        ("https://example.com/a?gclid=abc", "https://example.com/a"),
        ("https://example.com/a?fbclid=abc&page=2", "https://example.com/a?page=2"),
        (
            "https://example.com/a?page=2&UTM_Campaign=x&sort=new",
            "https://example.com/a?page=2&sort=new",
        ),
        ("https://example.com/a?utm_source", "https://example.com/a"),
        # real parameters stay, in their original order
        ("https://example.com/a?b=2&a=1", "https://example.com/a?b=2&a=1"),
        ("https://example.com/a?ref=utm_source", "https://example.com/a?ref=utm_source"),
        # everything at once
        (
            "HTTP://Example.com:80/Blog/?utm_source=x&id=7#top",
            "http://example.com/Blog?id=7",
        ),
        # userinfo and IPv6 hosts survive
        ("https://user:pw@Example.com/a", "https://user:pw@example.com/a"),
        ("http://[::1]:8080/a/", "http://[::1]:8080/a"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/a?utm_source=x#frag",
        "HTTPS://EXAMPLE.COM:443/A/",
        "https://example.com",
    ],
)
def test_normalize_is_idempotent(raw: str) -> None:
    once = normalize_url(raw)
    assert normalize_url(once) == once


def test_spellings_of_one_page_compare_equal() -> None:
    spellings = [
        "https://example.com/pricing",
        "https://example.com/pricing/",
        "HTTPS://EXAMPLE.COM/pricing",
        "https://example.com:443/pricing#plans",
        "https://example.com/pricing?utm_source=ad&gclid=1",
    ]
    assert len({normalize_url(url) for url in spellings}) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "example.com/page",
        "/relative/path",
        "ftp://example.com/file",
        "https://",
        "mailto:a@b.c",
        "https://example.com:notaport/",
    ],
)
def test_normalize_rejects_what_is_not_an_http_url(raw: str) -> None:
    with pytest.raises(InvalidURLError):
        normalize_url(raw)


def test_invalid_url_error_names_the_value() -> None:
    with pytest.raises(InvalidURLError, match=r"example\.com/page"):
        normalize_url("example.com/page")


def test_ignore_patterns_search_anywhere_in_the_url() -> None:
    patterns = compile_patterns([r"/draft/", r"\.pdf$"])
    assert is_ignored("https://example.com/draft/post", patterns)
    assert is_ignored("https://example.com/files/report.pdf", patterns)
    assert not is_ignored("https://example.com/blog/post", patterns)
    assert not is_ignored("https://example.com/pdf-guide", patterns)


def test_no_patterns_ignores_nothing() -> None:
    assert not is_ignored("https://example.com/anything", [])


def test_bad_regex_is_a_readable_error() -> None:
    with pytest.raises(InputError, match=r"invalid --ignore regex '\(unclosed'"):
        compile_patterns(["(unclosed"])
