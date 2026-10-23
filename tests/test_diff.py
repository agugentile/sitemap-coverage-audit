import pytest

from sitemap_audit.diff import compare, coverage_percent
from sitemap_audit.errors import InvalidURLError
from sitemap_audit.normalize import compile_patterns

SITEMAP = "https://example.com/sitemap.xml"


def audit(
    sitemap: list[str],
    catalog: list[str],
    extras: list[str] | None = None,
    ignore: list[str] | None = None,
):
    return compare(
        sitemap=SITEMAP,
        sitemap_urls=sitemap,
        catalog_urls=catalog,
        extra_urls=extras or [],
        ignore=compile_patterns(ignore or []),
    )


def urls(result) -> list[str]:
    return [item.url for item in result.unmonitored]


def test_diff_maths() -> None:
    result = audit(
        sitemap=["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        catalog=["https://example.com/a", "https://example.com/gone"],
    )
    assert urls(result) == ["https://example.com/b", "https://example.com/c"]
    assert result.stale == ["https://example.com/gone"]
    counts = result.counts
    assert (counts.live, counts.catalog, counts.covered) == (3, 2, 1)
    assert counts.sitemap == 3
    assert counts.extra == 0
    assert result.coverage_percent == 33.3


def test_full_coverage() -> None:
    result = audit(
        ["https://example.com/a", "https://example.com/b"],
        ["https://example.com/b", "https://example.com/a"],
    )
    assert result.unmonitored == []
    assert result.stale == []
    assert result.coverage_percent == 100.0


def test_catalog_with_nothing_in_common_with_the_sitemap() -> None:
    result = audit(["https://example.com/a"], ["https://example.com/z"])
    assert result.coverage_percent == 0.0
    assert urls(result) == ["https://example.com/a"]
    assert result.stale == ["https://example.com/z"]


def test_no_live_pages_means_no_percentage() -> None:
    result = audit([], ["https://example.com/a"])
    assert result.coverage_percent is None
    assert result.stale == ["https://example.com/a"]


def test_nothing_at_all() -> None:
    result = audit([], [])
    assert result.coverage_percent is None
    assert result.unmonitored == []
    assert result.stale == []


def test_spelling_differences_do_not_count_as_gaps_or_stale_entries() -> None:
    result = audit(
        sitemap=["https://example.com/pricing/", "https://example.com/"],
        catalog=["HTTPS://Example.com/pricing", "https://example.com:443?utm_source=x#top"],
    )
    assert result.unmonitored == []
    assert result.stale == []
    assert result.coverage_percent == 100.0


def test_duplicates_are_counted_once() -> None:
    result = audit(
        sitemap=[
            "https://example.com/a",
            "https://example.com/a/",
            "https://example.com/a?utm_x=1",
        ],
        catalog=["https://example.com/a", "https://example.com/a/"],
    )
    assert result.counts.sitemap == 1
    assert result.counts.catalog == 1
    assert result.counts.live == 1
    assert result.coverage_percent == 100.0


def test_results_are_sorted_so_output_is_stable() -> None:
    result = audit(
        sitemap=["https://example.com/c", "https://example.com/a", "https://example.com/b"],
        catalog=["https://example.com/z", "https://example.com/y"],
    )
    assert urls(result) == sorted(urls(result))
    assert result.stale == ["https://example.com/y", "https://example.com/z"]


# --- extra sources -------------------------------------------------------------------------


def test_extra_sources_are_added_to_the_live_pages() -> None:
    result = audit(
        sitemap=["https://example.com/a"],
        extras=["https://example.com/b", "https://example.com/a"],
        catalog=["https://example.com/a"],
    )
    assert urls(result) == ["https://example.com/b"]
    assert result.counts.live == 2
    assert result.counts.extra == 2
    assert result.counts.extra_only == 1
    assert result.coverage_percent == 50.0


def test_unmonitored_pages_say_where_they_were_found() -> None:
    result = audit(
        sitemap=["https://example.com/both", "https://example.com/map"],
        extras=["https://example.com/both", "https://example.com/cms"],
        catalog=[],
    )
    sources = {item.url: item.source for item in result.unmonitored}
    assert sources == {
        "https://example.com/both": "both",
        "https://example.com/map": "sitemap",
        "https://example.com/cms": "extra",
    }


def test_a_catalog_entry_known_only_from_extras_is_not_stale() -> None:
    result = audit(
        sitemap=["https://example.com/a"],
        extras=["https://example.com/hidden"],
        catalog=["https://example.com/a", "https://example.com/hidden"],
    )
    assert result.stale == []
    assert result.coverage_percent == 100.0


def test_extras_alone_can_be_the_whole_live_set() -> None:
    result = audit(sitemap=[], extras=["https://example.com/a"], catalog=[])
    assert urls(result) == ["https://example.com/a"]
    assert result.coverage_percent == 0.0


# --- ignore patterns -----------------------------------------------------------------------


def test_ignore_applies_to_the_sitemap() -> None:
    result = audit(
        sitemap=["https://example.com/a", "https://example.com/draft/b"],
        catalog=["https://example.com/a"],
        ignore=[r"/draft/"],
    )
    assert result.unmonitored == []
    assert result.counts.live == 1
    assert result.counts.ignored == 1


def test_ignore_applies_to_the_catalog_so_nothing_goes_stale_by_it() -> None:
    result = audit(
        sitemap=["https://example.com/a"],
        catalog=["https://example.com/a", "https://example.com/draft/old"],
        ignore=[r"/draft/"],
    )
    assert result.stale == []
    assert result.counts.catalog == 1


def test_ignore_applies_to_extras() -> None:
    result = audit(
        sitemap=[],
        extras=["https://example.com/a.pdf", "https://example.com/b"],
        catalog=[],
        ignore=[r"\.pdf$"],
    )
    assert urls(result) == ["https://example.com/b"]


def test_several_patterns_and_a_url_matching_all_of_them() -> None:
    result = audit(
        sitemap=["https://example.com/x/draft", "https://example.com/y", "https://example.com/z"],
        catalog=[],
        ignore=[r"/draft", r"/x/", r"/y$"],
    )
    assert urls(result) == ["https://example.com/z"]
    assert result.counts.ignored == 2


def test_ignore_sees_the_normalised_url() -> None:
    # the tracking parameter and trailing slash are gone before the pattern runs
    result = audit(
        sitemap=["https://example.com/a/?utm_source=x"],
        catalog=[],
        ignore=[r"/a$"],
    )
    assert result.unmonitored == []


# --- percentage ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("covered", "live", "expected"),
    [
        (0, 10, 0.0),
        (1, 3, 33.3),
        (2, 3, 66.6),  # rounded down, not 66.7
        (5, 6, 83.3),
        (7, 10, 70.0),
        (10, 10, 100.0),
        (1999, 2000, 99.9),  # never 100.0 while a page is missing
        (99_999, 100_000, 99.9),
        (0, 0, None),
    ],
)
def test_coverage_percent(covered: int, live: int, expected: float | None) -> None:
    assert coverage_percent(covered, live) == expected


def test_warnings_are_passed_through() -> None:
    result = compare(
        sitemap=SITEMAP,
        sitemap_urls=[],
        catalog_urls=[],
        warnings=["something odd"],
    )
    assert result.warnings == ["something odd"]


def test_an_invalid_url_is_an_error_not_a_silent_skip() -> None:
    with pytest.raises(InvalidURLError):
        audit(["not a url"], [])
