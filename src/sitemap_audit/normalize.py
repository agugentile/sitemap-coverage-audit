"""URL normalisation and ignore patterns.

Two URLs that a human would call "the same page" should compare equal, so every URL is
normalised before it is compared or reported.
"""

import re
from collections.abc import Iterable, Sequence
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from .errors import InputError, InvalidURLError

DEFAULT_PORTS = {"http": 80, "https": 443}
TRACKING_PREFIXES = ("utm_",)
TRACKING_NAMES = frozenset({"gclid", "fbclid"})


def normalize_url(url: str) -> str:
    """Return the canonical form of an absolute http(s) URL.

    - scheme and host are lowercased
    - the fragment is dropped
    - trailing slashes are removed (so the site root has no path at all)
    - the port is dropped when it is the default for the scheme
    - tracking parameters (utm_*, gclid, fbclid) are removed
    - everything else, including the case of the path and the order of the remaining
      query parameters, is kept as it was

    Raises InvalidURLError when the value is not an absolute http(s) URL.
    """
    text = url.strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError(f"not a valid URL: {url!r} ({exc})") from exc

    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in DEFAULT_PORTS or not host:
        raise InvalidURLError(f"not an absolute http(s) URL: {url!r}")

    if ":" in host:  # IPv6 literal, urlsplit removed the brackets
        host = f"[{host}]"
    if port is not None and port != DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    userinfo, at, _ = parts.netloc.rpartition("@")
    netloc = f"{userinfo}{at}{host}"

    path = parts.path.rstrip("/")
    query = _drop_tracking(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def _drop_tracking(query: str) -> str:
    kept = [pair for pair in query.split("&") if pair and not _is_tracking(pair.partition("=")[0])]
    return "&".join(kept)


def _is_tracking(name: str) -> bool:
    key = unquote_plus(name).lower()
    return key.startswith(TRACKING_PREFIXES) or key in TRACKING_NAMES


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    """Compile ignore regexes, turning a bad pattern into a readable error."""
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise InputError(f"invalid --ignore regex {pattern!r}: {exc}") from exc
    return compiled


def is_ignored(url: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    """True when any pattern matches anywhere in the (normalised) URL."""
    return any(pattern.search(url) for pattern in patterns)
