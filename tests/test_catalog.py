import json
from pathlib import Path

import pytest

from sitemap_audit.catalog import (
    build_proposal,
    check_proposal_path,
    load_catalog,
    write_proposal,
)
from sitemap_audit.errors import InputError
from sitemap_audit.extras import load_extra

from .helpers import fixture_path


def write(tmp_path: Path, text: str, name: str = "catalog.json") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- loading the catalog -------------------------------------------------------------------


def test_loads_a_list_of_strings() -> None:
    catalog = load_catalog(fixture_path("catalog_strings.json"))
    assert catalog.urls == [
        "https://example.com",
        "https://example.com/pricing/",
        "https://example.com/old-page",
    ]


def test_loads_a_list_of_objects_and_keeps_extra_fields() -> None:
    catalog = load_catalog(fixture_path("catalog_objects.json"))
    assert catalog.urls == [
        "https://example.com/",
        "https://example.com/pricing",
        "https://example.com/old-page",
    ]
    assert catalog.raw[0] == {
        "url": "https://example.com/",
        "name": "Home",
        "interval_seconds": 60,
    }


def test_loads_a_mixed_list() -> None:
    catalog = load_catalog(fixture_path("catalog_mixed.json"))
    assert catalog.urls == ["https://example.com/pricing", "https://example.com/about"]


def test_empty_catalog_is_valid(tmp_path: Path) -> None:
    assert load_catalog(write(tmp_path, "[]")).urls == []


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="catalog file not found"):
        load_catalog(tmp_path / "nope.json")


def test_a_directory_is_not_a_catalog(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="cannot read catalog file"):
        load_catalog(tmp_path)


def test_invalid_json_reports_the_position(tmp_path: Path) -> None:
    path = write(tmp_path, '[\n  "https://example.com",\n]\n')
    with pytest.raises(InputError, match=r"invalid JSON at line 3, column 1"):
        load_catalog(path)


def test_top_level_object_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '{"urls": ["https://example.com"]}')
    with pytest.raises(InputError, match=r"expected a JSON list.*got dict"):
        load_catalog(path)


def test_object_without_url_names_the_entry(tmp_path: Path) -> None:
    path = write(tmp_path, '["https://example.com", {"name": "Home"}]')
    with pytest.raises(InputError) as caught:
        load_catalog(path)
    message = str(caught.value)
    assert "entry 2, field 'url'" in message
    assert "Field required" in message


def test_entry_of_the_wrong_type_is_explained(tmp_path: Path) -> None:
    path = write(tmp_path, '["https://example.com", 42]')
    with pytest.raises(InputError, match="entry 2: must be a URL string or an object"):
        load_catalog(path)


def test_url_that_is_not_a_string_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '[{"url": 7}]')
    with pytest.raises(InputError, match="entry 1, field 'url'"):
        load_catalog(path)


def test_relative_and_non_http_urls_are_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '["https://example.com", "/pricing", "ftp://example.com/x"]')
    with pytest.raises(InputError) as caught:
        load_catalog(path)
    message = str(caught.value)
    assert "entry 2" in message
    assert "'/pricing'" in message
    assert "entry 3" in message


def test_many_problems_are_capped(tmp_path: Path) -> None:
    path = write(tmp_path, json.dumps(["nope"] * 20))
    with pytest.raises(InputError, match="and 15 more"):
        load_catalog(path)


def test_a_byte_order_mark_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'["https://example.com"]')
    assert load_catalog(path).urls == ["https://example.com"]


# --- extra URL lists -----------------------------------------------------------------------


def test_extra_json_uses_the_catalog_format() -> None:
    assert load_extra(fixture_path("extra_cms.json")) == [
        "https://example.com/landing/spring-offer?utm_campaign=spring",
        "https://example.com/pricing",
    ]


def test_extra_txt_skips_comments_and_blank_lines() -> None:
    assert load_extra(fixture_path("extra_cms.txt")) == [
        "https://example.com/help/getting-started",
        "https://example.com/landing/spring-offer",
    ]


def test_extra_txt_error_names_the_line(tmp_path: Path) -> None:
    path = write(tmp_path, "https://example.com/a\nnot a url\n", name="urls.txt")
    with pytest.raises(InputError, match=r"urls.txt: line 2"):
        load_extra(path)


def test_extra_json_errors_name_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, '[{"slug": "pricing"}]', name="cms-export.json")
    with pytest.raises(InputError, match=r"cms-export.json: entry 1"):
        load_extra(path)


def test_extra_file_missing(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="extra URL file not found"):
        load_extra(tmp_path / "missing.txt")
    with pytest.raises(InputError, match="extra URL file not found"):
        load_extra(tmp_path / "missing.json")


# --- proposals -----------------------------------------------------------------------------


def test_proposal_for_a_list_of_strings_adds_strings() -> None:
    catalog = load_catalog(fixture_path("catalog_strings.json"))
    proposal = build_proposal(catalog, ["https://example.com/new"])
    assert proposal == [*catalog.raw, "https://example.com/new"]


def test_proposal_for_objects_adds_objects_and_keeps_existing_ones_intact() -> None:
    catalog = load_catalog(fixture_path("catalog_objects.json"))
    proposal = build_proposal(catalog, ["https://example.com/a", "https://example.com/b"])
    assert proposal[:3] == catalog.raw
    assert proposal[3:] == [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}]


def test_proposal_for_a_mixed_catalog_adds_objects() -> None:
    catalog = load_catalog(fixture_path("catalog_mixed.json"))
    assert build_proposal(catalog, ["https://example.com/a"])[-1] == {
        "url": "https://example.com/a"
    }


def test_proposal_for_an_empty_catalog_adds_strings(tmp_path: Path) -> None:
    catalog = load_catalog(write(tmp_path, "[]"))
    assert build_proposal(catalog, ["https://example.com/a"]) == ["https://example.com/a"]


def test_proposal_does_not_change_the_loaded_catalog() -> None:
    catalog = load_catalog(fixture_path("catalog_strings.json"))
    before = list(catalog.raw)
    build_proposal(catalog, ["https://example.com/new"])
    assert catalog.raw == before


def test_write_proposal_creates_readable_json(tmp_path: Path) -> None:
    target = tmp_path / "proposed.json"
    write_proposal(target, ["https://example.com/café"])
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == ["https://example.com/café"]


def test_write_proposal_refuses_to_overwrite(tmp_path: Path) -> None:
    target = write(tmp_path, "precious", name="proposed.json")
    with pytest.raises(InputError, match="refusing to overwrite"):
        write_proposal(target, ["https://example.com/a"])
    assert target.read_text(encoding="utf-8") == "precious"


def test_check_proposal_path(tmp_path: Path) -> None:
    check_proposal_path(tmp_path / "new.json")  # fine
    existing = write(tmp_path, "[]")
    with pytest.raises(InputError, match="refusing to overwrite"):
        check_proposal_path(existing)
    with pytest.raises(InputError, match="directory does not exist"):
        check_proposal_path(tmp_path / "no-such-dir" / "new.json")
