import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import respx

from sitemap_audit import __version__
from sitemap_audit.cli import main

from .helpers import SITE, fixture_path, serve

SITEMAP = f"{SITE}/sitemap.xml"

# What the `site` fixture serves, in normalised form.
LIVE = [
    "https://example.com",
    "https://example.com/about",
    "https://example.com/blog",
    "https://example.com/blog/hello-world",
    "https://example.com/contact",
    "https://example.com/docs/start",
    "https://example.com/pricing",
]


def run(capsys: pytest.CaptureFixture[str], *args: object) -> tuple[int, str, str]:
    code = main([str(arg) for arg in args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_catalog(tmp_path: Path, entries: list, name: str = "catalog.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def partial_catalog(tmp_path: Path) -> Path:
    """Covers 4 of the 7 live pages and has one stale entry."""
    return make_catalog(
        tmp_path,
        [
            "https://example.com",
            "https://example.com/pricing/",
            "https://example.com/about",
            "https://example.com/blog",
            "https://example.com/gone",
        ],
    )


@pytest.fixture
def full_catalog(tmp_path: Path) -> Path:
    return make_catalog(tmp_path, [*LIVE, "https://example.com/gone"])


# --- the report ----------------------------------------------------------------------------


def test_text_report_and_exit_zero(
    site: respx.MockRouter, partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog)
    assert code == 0
    assert err == ""
    assert "Coverage:        57.1% (4 of 7 live pages)" in out
    assert "https://example.com/blog/hello-world" in out
    assert "https://example.com/contact" in out
    assert "https://example.com/docs/start" in out
    assert "  https://example.com/gone" in out


def test_gaps_do_not_change_the_exit_code_by_default(
    site: respx.MockRouter, partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog)
    assert code == 0


def test_json_format(
    site: respx.MockRouter, partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--format", "json"
    )
    data = json.loads(out)
    assert code == 0
    assert data["coverage_percent"] == 57.1
    assert [item["url"] for item in data["unmonitored"]] == [
        "https://example.com/blog/hello-world",
        "https://example.com/contact",
        "https://example.com/docs/start",
    ]
    assert data["stale"] == ["https://example.com/gone"]


def test_markdown_format(
    site: respx.MockRouter, partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--format", "markdown"
    )
    assert code == 0
    assert out.startswith("# Sitemap coverage audit")
    assert "## Unmonitored pages (3)" in out


def test_unknown_format_is_a_usage_error(
    partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--format", "html"
    )
    assert code == 2
    assert "invalid choice" in err


def test_a_cycle_shows_up_as_a_warning_in_the_report(
    http: respx.MockRouter, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(http, "/cycle/a.xml", "index_cycle_a.xml")
    serve(http, "/cycle/b.xml", "index_cycle_b.xml")
    serve(http, "/sitemaps/pages.xml", "urlset_pages.xml")
    code, out, _ = run(
        capsys, "--sitemap", f"{SITE}/cycle/a.xml", "--catalog", make_catalog(tmp_path, [])
    )
    assert code == 0
    assert "Warnings:" in out
    assert "cycle" in out


# --- --fail-on-gaps ------------------------------------------------------------------------


def test_fail_on_gaps_exits_one_when_pages_are_unmonitored(
    site: respx.MockRouter, partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--fail-on-gaps")
    assert code == 1
    assert "Unmonitored:     3" in out  # the report is still printed


def test_fail_on_gaps_exits_zero_with_full_coverage_even_if_entries_are_stale(
    site: respx.MockRouter, full_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "--sitemap", SITEMAP, "--catalog", full_catalog, "--fail-on-gaps")
    assert code == 0
    assert "Coverage:        100.0%" in out
    assert "Stale entries:   1" in out


# --- extra sources and ignore patterns -----------------------------------------------------


def test_extra_sources_are_unioned_with_the_sitemap(
    http: respx.MockRouter, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(http, "/sitemap.xml", "urlset_pages.xml")
    catalog = make_catalog(tmp_path, ["https://example.com/pricing"])
    code, out, _ = run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        catalog,
        "--extra",
        fixture_path("extra_cms.json"),
        "--format",
        "json",
    )
    data = json.loads(out)
    assert code == 0
    assert data["counts"]["sitemap"] == 4
    assert data["counts"]["extra_only"] == 1
    sources = {item["url"]: item["source"] for item in data["unmonitored"]}
    assert sources["https://example.com/landing/spring-offer"] == "extra"
    assert sources["https://example.com/about"] == "sitemap"


@pytest.mark.parametrize("repeated", [False, True], ids=["one flag", "repeated flag"])
def test_several_extra_files(
    http: respx.MockRouter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    repeated: bool,
) -> None:
    serve(http, "/sitemap.xml", "urlset_blog.xml")
    json_file = fixture_path("extra_cms.json")
    txt_file = fixture_path("extra_cms.txt")
    extras = (
        ["--extra", json_file, "--extra", txt_file]
        if repeated
        else ["--extra", json_file, txt_file]
    )
    code, out, _ = run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        make_catalog(tmp_path, []),
        *extras,
        "--format",
        "json",
    )
    assert code == 0
    found = {item["url"] for item in json.loads(out)["unmonitored"]}
    assert found == {
        "https://example.com/blog",
        "https://example.com/blog/hello-world",
        "https://example.com/landing/spring-offer",  # in both extra files, counted once
        "https://example.com/pricing",
        "https://example.com/help/getting-started",
    }


@pytest.mark.parametrize("repeated", [False, True], ids=["one flag", "repeated flag"])
def test_ignore_patterns(
    site: respx.MockRouter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    repeated: bool,
) -> None:
    catalog = make_catalog(tmp_path, ["https://example.com"])
    patterns = (
        ["--ignore", "/blog", "--ignore", "/docs/"] if repeated else ["--ignore", "/blog", "/docs/"]
    )
    code, out, _ = run(
        capsys, "--sitemap", SITEMAP, "--catalog", catalog, *patterns, "--format", "json"
    )
    data = json.loads(out)
    assert code == 0
    assert [item["url"] for item in data["unmonitored"]] == [
        "https://example.com/about",
        "https://example.com/contact",
        "https://example.com/pricing",
    ]
    assert data["counts"]["ignored"] == 3


# --- input errors: exit 2 ------------------------------------------------------------------


def test_missing_required_flags_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys)
    assert code == 2
    assert "--sitemap" in err


def test_unknown_flag_is_a_usage_error(
    partial_catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--nope")
    assert code == 2
    assert "unrecognized arguments" in err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--timeout", "0"),
        ("--timeout", "soon"),
        ("--retries", "-1"),
        ("--max-depth", "-1"),
        ("--max-urls", "0"),
        ("--max-urls", "many"),
    ],
)
def test_bad_numbers_are_usage_errors(
    partial_catalog: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str
) -> None:
    code, _, err = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, flag, value)
    assert code == 2
    assert flag in err


def test_missing_catalog_file(http: respx.MockRouter, tmp_path: Path, capsys) -> None:
    code, out, err = run(capsys, "--sitemap", SITEMAP, "--catalog", tmp_path / "nope.json")
    assert code == 2
    assert out == ""
    assert err.startswith("error: catalog file not found")
    assert http.calls.call_count == 0  # failed before any request


def test_invalid_catalog(http: respx.MockRouter, tmp_path: Path, capsys) -> None:
    bad = tmp_path / "catalog.json"
    bad.write_text('[{"name": "no url here"}]', encoding="utf-8")
    code, out, err = run(capsys, "--sitemap", SITEMAP, "--catalog", bad)
    assert code == 2
    assert out == ""
    assert "entry 1, field 'url': Field required" in err
    assert http.calls.call_count == 0


def test_invalid_extra_file(
    http: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    extra = tmp_path / "extra.json"
    extra.write_text("not json", encoding="utf-8")
    code, _, err = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--extra", extra)
    assert code == 2
    assert "invalid JSON" in err
    assert http.calls.call_count == 0


def test_bad_ignore_regex(http: respx.MockRouter, partial_catalog: Path, capsys) -> None:
    code, _, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--ignore", "(oops"
    )
    assert code == 2
    assert "invalid --ignore regex" in err
    assert http.calls.call_count == 0


def test_sitemap_not_found(http: respx.MockRouter, partial_catalog: Path, capsys) -> None:
    http.get(SITEMAP).respond(404)
    code, out, err = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog)
    assert code == 2
    assert out == ""
    assert err == f"error: {SITEMAP}: HTTP 404\n"


def test_malformed_sitemap(http: respx.MockRouter, partial_catalog: Path, capsys) -> None:
    serve(http, "/sitemap.xml", "malformed.xml")
    code, _, err = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog)
    assert code == 2
    assert "not well-formed XML" in err


def test_sitemap_url_must_be_http(partial_catalog: Path, capsys) -> None:
    code, _, err = run(capsys, "--sitemap", "sitemap.xml", "--catalog", partial_catalog)
    assert code == 2
    assert "not an absolute http(s) URL" in err


def test_safety_limit_flags_reach_the_crawler(
    site: respx.MockRouter, partial_catalog: Path, capsys
) -> None:
    code, _, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--max-urls", "3"
    )
    assert code == 2
    assert "more than 3 page URLs" in err
    code, _, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--max-depth", "1"
    )
    assert code == 2
    assert "--max-depth" in err


def test_user_agent_flag(http: respx.MockRouter, partial_catalog: Path, capsys) -> None:
    route = serve(http, "/sitemap.xml", "urlset_blog.xml")
    code, _, _ = run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        partial_catalog,
        "--user-agent",
        "audit-bot/2 (ops@example.com)",
    )
    assert code == 0
    assert route.calls.last.request.headers["user-agent"] == "audit-bot/2 (ops@example.com)"


# --- --propose -----------------------------------------------------------------------------


def test_propose_writes_a_new_file_and_leaves_the_catalog_alone(
    site: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    before = partial_catalog.read_bytes()
    proposal = tmp_path / "proposed.json"

    code, out, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--propose", proposal
    )

    assert code == 0
    assert partial_catalog.read_bytes() == before
    assert json.loads(proposal.read_text(encoding="utf-8")) == [
        *json.loads(before),
        "https://example.com/blog/hello-world",
        "https://example.com/contact",
        "https://example.com/docs/start",
    ]
    assert "wrote 3 proposed addition(s)" in err
    assert "Unmonitored:     3" in out  # the report still goes to stdout


def test_propose_follows_the_catalog_style(site: respx.MockRouter, tmp_path: Path, capsys) -> None:
    catalog = make_catalog(
        tmp_path, [{"url": entry, "name": "x"} for entry in LIVE[:-1]], name="objects.json"
    )
    proposal = tmp_path / "proposed.json"
    code, _, _ = run(capsys, "--sitemap", SITEMAP, "--catalog", catalog, "--propose", proposal)
    assert code == 0
    written = json.loads(proposal.read_text(encoding="utf-8"))
    assert written[:-1] == json.loads(catalog.read_text(encoding="utf-8"))
    assert written[-1] == {"url": "https://example.com/pricing"}


def test_propose_never_overwrites_the_catalog(
    http: respx.MockRouter, partial_catalog: Path, capsys
) -> None:
    before = partial_catalog.read_bytes()
    code, out, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--propose", partial_catalog
    )
    assert code == 2
    assert out == ""
    assert "refusing to overwrite" in err
    assert partial_catalog.read_bytes() == before
    assert http.calls.call_count == 0


def test_propose_never_overwrites_an_extra_file(
    http: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    extra = tmp_path / "extra.json"
    extra.write_text('["https://example.com/x"]', encoding="utf-8")
    code, _, err = run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        partial_catalog,
        "--extra",
        extra,
        "--propose",
        extra,
    )
    assert code == 2
    assert "refusing to overwrite" in err
    assert extra.read_text(encoding="utf-8") == '["https://example.com/x"]'


def test_propose_never_overwrites_any_existing_file(
    http: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    existing = tmp_path / "notes.json"
    existing.write_text("keep me", encoding="utf-8")
    code, _, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--propose", existing
    )
    assert code == 2
    assert "refusing to overwrite" in err
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_propose_through_a_symlink_to_the_catalog_is_refused(
    http: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    link = tmp_path / "link.json"
    link.symlink_to(partial_catalog)
    before = partial_catalog.read_bytes()
    code, _, _ = run(capsys, "--sitemap", SITEMAP, "--catalog", partial_catalog, "--propose", link)
    assert code == 2
    assert partial_catalog.read_bytes() == before


def test_propose_into_a_missing_directory_is_an_error(
    http: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    code, _, err = run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        partial_catalog,
        "--propose",
        tmp_path / "missing" / "proposed.json",
    )
    assert code == 2
    assert "directory does not exist" in err


def test_no_proposal_file_when_nothing_is_missing(
    site: respx.MockRouter, full_catalog: Path, tmp_path: Path, capsys
) -> None:
    proposal = tmp_path / "proposed.json"
    code, _, err = run(
        capsys, "--sitemap", SITEMAP, "--catalog", full_catalog, "--propose", proposal
    )
    assert code == 0
    assert not proposal.exists()
    assert "no proposal file was written" in err


def test_propose_and_fail_on_gaps_together(
    site: respx.MockRouter, partial_catalog: Path, tmp_path: Path, capsys
) -> None:
    proposal = tmp_path / "proposed.json"
    code, _, _ = run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        partial_catalog,
        "--propose",
        proposal,
        "--fail-on-gaps",
    )
    assert code == 1
    assert proposal.exists()


def test_the_proposal_uses_normalised_urls(http: respx.MockRouter, tmp_path: Path, capsys) -> None:
    serve(http, "/sitemap.xml", "urlset_pages.xml")
    proposal = tmp_path / "proposed.json"
    run(
        capsys,
        "--sitemap",
        SITEMAP,
        "--catalog",
        make_catalog(tmp_path, []),
        "--propose",
        proposal,
    )
    assert json.loads(proposal.read_text(encoding="utf-8")) == [
        "https://example.com",
        "https://example.com/about",
        "https://example.com/contact",
        "https://example.com/pricing",
    ]


# --- misc ----------------------------------------------------------------------------------


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--version")
    assert code == 0
    assert out == f"sitemap-audit {__version__}\n"


def test_help_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--help")
    assert code == 0
    assert "--fail-on-gaps" in out
    assert "exit codes:" in out


def test_module_entry_point() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "sitemap_audit", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == f"sitemap-audit {__version__}"


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert declared == __version__
