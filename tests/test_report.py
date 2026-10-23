import json

import pytest

from sitemap_audit.diff import AuditResult, compare
from sitemap_audit.report import FORMATS, render

SITEMAP = "https://example.com/sitemap.xml"
EM_DASH = chr(0x2014)  # spelled out so this file itself stays free of the character


@pytest.fixture
def result() -> AuditResult:
    return compare(
        sitemap=SITEMAP,
        sitemap_urls=[
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ],
        extra_urls=["https://example.com/cms-only"],
        catalog_urls=["https://example.com/a", "https://example.com/gone"],
        warnings=["a sitemap was listed twice"],
    )


@pytest.fixture
def clean() -> AuditResult:
    return compare(
        sitemap=SITEMAP,
        sitemap_urls=["https://example.com/a"],
        catalog_urls=["https://example.com/a"],
    )


def test_text_report(result: AuditResult) -> None:
    text = render(result, "text")
    assert "Sitemap: https://example.com/sitemap.xml" in text
    assert "Coverage:        25.0% (1 of 4 live pages)" in text
    assert "Live pages:      4 (sitemap 3, only in extra sources 1)" in text
    assert "Catalog entries: 2" in text
    assert "Unmonitored:     3" in text
    assert "Stale entries:   1" in text
    assert "  https://example.com/b" in text
    assert "https://example.com/cms-only  [extra sources only]" in text
    assert "  https://example.com/gone" in text
    assert "Warnings:\n  a sitemap was listed twice" in text
    assert text.endswith("\n")


def test_text_report_for_a_clean_result(clean: AuditResult) -> None:
    text = render(clean, "text")
    assert "Coverage:        100.0% (1 of 1 live pages)" in text
    assert text.count("  none") == 2
    assert "Warnings" not in text
    assert "Ignored" not in text


def test_text_report_shows_ignored_count() -> None:
    import re

    result = compare(
        sitemap=SITEMAP,
        sitemap_urls=["https://example.com/a", "https://example.com/draft"],
        catalog_urls=[],
        ignore=[re.compile("draft")],
    )
    assert "Ignored:         1" in render(result, "text")


def test_markdown_report(result: AuditResult) -> None:
    md = render(result, "markdown")
    assert md.startswith("# Sitemap coverage audit\n")
    assert "Sitemap: `https://example.com/sitemap.xml`" in md
    assert "**Coverage: 25.0% (1 of 4 live pages)**" in md
    assert "| Unmonitored | 3 |" in md
    assert "## Unmonitored pages (3)" in md
    assert "| https://example.com/b | sitemap |" in md
    assert "| https://example.com/cms-only | extra sources only |" in md
    assert "## Stale catalog entries (1)" in md
    assert "- https://example.com/gone" in md
    assert "## Warnings\n\n- a sitemap was listed twice" in md


def test_markdown_report_for_a_clean_result(clean: AuditResult) -> None:
    md = render(clean, "markdown")
    assert md.count("None.") == 2
    assert "## Warnings" not in md


def test_markdown_table_cells_survive_a_pipe_in_a_url() -> None:
    result = compare(
        sitemap=SITEMAP,
        sitemap_urls=["https://example.com/a?x=1|2"],
        catalog_urls=[],
    )
    assert "| https://example.com/a?x=1%7C2 | sitemap |" in render(result, "markdown")


def test_json_report_round_trips(result: AuditResult) -> None:
    data = json.loads(render(result, "json"))
    assert data["sitemap"] == SITEMAP
    assert data["coverage_percent"] == 25.0
    assert data["counts"] == {
        "sitemap": 3,
        "extra": 1,
        "extra_only": 1,
        "live": 4,
        "catalog": 2,
        "covered": 1,
        "ignored": 0,
    }
    assert data["unmonitored"][0] == {"url": "https://example.com/b", "source": "sitemap"}
    assert data["stale"] == ["https://example.com/gone"]
    assert data["warnings"] == ["a sitemap was listed twice"]
    assert AuditResult.model_validate(data) == result


def test_no_live_pages_reads_as_not_applicable() -> None:
    empty = compare(sitemap=SITEMAP, sitemap_urls=[], catalog_urls=["https://example.com/a"])
    assert "n/a (no live pages found)" in render(empty, "text")
    assert "n/a (no live pages found)" in render(empty, "markdown")
    assert json.loads(render(empty, "json"))["coverage_percent"] is None


@pytest.mark.parametrize("fmt", FORMATS)
def test_reports_never_contain_an_em_dash(
    fmt: str, result: AuditResult, clean: AuditResult
) -> None:
    for item in (result, clean):
        assert EM_DASH not in render(item, fmt)
