"""Extra URL lists, for pages a sitemap does not list (for example a CMS export)."""

from pathlib import Path

from .catalog import parse_entries, read_json
from .errors import InputError, InvalidURLError
from .normalize import normalize_url


def load_extra(path: Path) -> list[str]:
    """Load one extra URL list.

    A .txt file holds one URL per line (blank lines and lines starting with # are skipped).
    Anything else is read as JSON in the same format as the catalog: a list of URL strings
    or of objects with a "url" field.
    """
    if path.suffix.lower() == ".txt":
        return _load_text(path)
    return parse_entries(read_json(path, "extra URL"), str(path))


def _load_text(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise InputError(f"extra URL file not found: {path}") from None
    except UnicodeDecodeError:
        raise InputError(f"extra URL file is not valid UTF-8 text: {path}") from None
    except OSError as exc:
        raise InputError(f"cannot read extra URL file {path}: {exc.strerror or exc}") from exc

    urls = []
    for number, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            normalize_url(value)
        except InvalidURLError as exc:
            raise InputError(f"{path}: line {number}: {exc}") from exc
        urls.append(value)
    return urls
