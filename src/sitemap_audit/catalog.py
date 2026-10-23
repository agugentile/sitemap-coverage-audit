"""Reading the monitoring catalog, and writing a separate proposal file.

A catalog is a JSON list. Each item is either a URL string or an object with a "url" field.
Other fields on an object (a name, a check interval, and so on) are allowed and are kept
untouched in the proposal.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import InputError
from .normalize import normalize_url

MAX_REPORTED_PROBLEMS = 5


class CatalogEntry(BaseModel):
    """One catalog item. A bare string is accepted and treated as {"url": string}."""

    model_config = ConfigDict(extra="allow")

    url: str

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"url": value}
        if not isinstance(value, dict):
            raise ValueError("must be a URL string or an object with a 'url' field")
        return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        normalize_url(value)  # raises InvalidURLError, which pydantic reports as a value error
        return value.strip()


_ENTRIES = TypeAdapter(list[CatalogEntry])


@dataclass(frozen=True)
class Catalog:
    """A loaded catalog: the JSON as it was written, plus the URLs found in it."""

    path: Path
    raw: list[Any]
    urls: list[str]


def read_json(path: Path, kind: str) -> Any:
    """Read and decode a JSON file, with errors a person can act on."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise InputError(f"{kind} file not found: {path}") from None
    except UnicodeDecodeError:
        raise InputError(f"{kind} file is not valid UTF-8 text: {path}") from None
    except OSError as exc:
        raise InputError(f"cannot read {kind} file {path}: {exc.strerror or exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def parse_entries(data: Any, source: str) -> list[str]:
    """Validate decoded JSON as a list of catalog entries and return the URLs."""
    if not isinstance(data, list):
        raise InputError(
            f"{source}: expected a JSON list of URLs or of objects with a 'url' field, "
            f"got {type(data).__name__}"
        )
    try:
        entries = _ENTRIES.validate_python(data)
    except ValidationError as exc:
        problems = [_describe(error) for error in exc.errors()]
        shown = problems[:MAX_REPORTED_PROBLEMS]
        if len(problems) > len(shown):
            shown.append(f"and {len(problems) - len(shown)} more")
        raise InputError(f"{source}: " + "; ".join(shown)) from exc
    return [entry.url for entry in entries]


def _describe(error: Any) -> str:
    message = error["msg"].removeprefix("Value error, ")
    location = error["loc"]
    if not location or not isinstance(location[0], int):
        return message
    where = f"entry {location[0] + 1}"  # people count from 1
    if len(location) > 1:
        where += f", field '{location[1]}'"
    return f"{where}: {message}"


def load_catalog(path: Path) -> Catalog:
    data = read_json(path, "catalog")
    return Catalog(path=path, raw=data, urls=parse_entries(data, str(path)))


def build_proposal(catalog: Catalog, new_urls: list[str]) -> list[Any]:
    """Return a copy of the catalog with new_urls appended, in the catalog's own style.

    If every existing item is a plain string, the additions are strings. Otherwise they are
    {"url": ...} objects. Existing items are never reordered or modified.
    """
    use_objects = any(not isinstance(item, str) for item in catalog.raw)
    additions: list[Any] = [{"url": url} if use_objects else url for url in new_urls]
    return [*catalog.raw, *additions]


def write_proposal(path: Path, entries: list[Any]) -> None:
    """Write the proposal to a new file. An existing file is never overwritten."""
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except FileExistsError:
        raise InputError(f"refusing to overwrite existing file: {path}") from None
    except OSError as exc:
        raise InputError(f"cannot write proposal file {path}: {exc.strerror or exc}") from exc


def check_proposal_path(path: Path) -> None:
    """Fail early, before any network work, if the proposal cannot be written."""
    if path.exists():
        raise InputError(
            f"refusing to overwrite existing file: {path} "
            "(choose a new path for --propose, the catalog is never modified)"
        )
    if not path.parent.is_dir():
        raise InputError(f"cannot write proposal file {path}: directory does not exist")
