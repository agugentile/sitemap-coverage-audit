"""Fetching and parsing sitemaps, including sitemap index files and gzip files.

The crawler is strict on purpose. If a sitemap in the tree cannot be read, or a safety limit is
hit, the run stops with an error instead of returning a partial list, because a partial list
would show up as false "stale" catalog entries.
"""

import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from lxml import etree

from . import __version__
from .errors import InvalidURLError, SitemapError
from .normalize import normalize_url

DEFAULT_USER_AGENT = f"sitemap-audit/{__version__} (read-only sitemap check)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
GZIP_MAGIC = b"\x1f\x8b"
# root element -> name of the entries inside it
_ENTRY_NAMES = {"urlset": "url", "sitemapindex": "sitemap"}


@dataclass(frozen=True)
class FetchSettings:
    """Network behaviour and safety limits."""

    timeout: float = 15.0  # seconds, per request
    retries: int = 2  # extra attempts after a timeout, connection error, 429 or 5xx
    backoff: float = 0.5  # seconds before the first retry, doubled each time
    max_depth: int = 5  # how many levels of sitemap index files may be nested
    max_urls: int = 100_000  # stop if more page URLs than this are found
    max_bytes: int = 50 * 1024 * 1024  # per sitemap, after decompression
    user_agent: str = DEFAULT_USER_AGENT


@dataclass
class SitemapResult:
    urls: list[str] = field(default_factory=list)
    fetched: int = 0
    warnings: list[str] = field(default_factory=list)


def collect_urls(
    sitemap_url: str,
    settings: FetchSettings | None = None,
    client: httpx.Client | None = None,
) -> SitemapResult:
    """Return every page URL listed by a sitemap or, recursively, by a sitemap index."""
    settings = settings or FetchSettings()
    root = sitemap_url.strip()
    try:
        normalize_url(root)
    except InvalidURLError as exc:
        raise SitemapError(str(exc)) from exc

    if client is not None:
        return _Crawler(settings, client).crawl(root)
    with httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.timeout,
        follow_redirects=True,
    ) as owned_client:
        return _Crawler(settings, owned_client).crawl(root)


def parse_sitemap(content: bytes, source: str) -> tuple[str, list[str]]:
    """Parse sitemap XML and return its root name and the text of every entry's <loc>.

    The root name is "urlset" for a list of pages and "sitemapindex" for a list of sitemaps.
    Entities are not resolved and nothing is fetched while parsing, so a hostile document
    cannot read local files or make network requests.
    """
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError as exc:
        raise SitemapError(f"{source}: not well-formed XML ({exc})") from exc

    name = etree.QName(root).localname
    if name not in _ENTRY_NAMES:
        raise SitemapError(
            f"{source}: not a sitemap, expected <urlset> or <sitemapindex> but found <{name}>"
        )

    locs = []
    for entry in root:
        if not isinstance(entry.tag, str) or etree.QName(entry).localname != _ENTRY_NAMES[name]:
            continue
        for child in entry:
            if isinstance(child.tag, str) and etree.QName(child).localname == "loc":
                locs.append((child.text or "").strip())
                break
        else:  # an entry with no <loc> at all is counted as an empty one
            locs.append("")
    return name, locs


class _Crawler:
    def __init__(self, settings: FetchSettings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client
        self.result = SitemapResult()
        self._visited: set[str] = set()
        self._sleep: Callable[[float], None] = time.sleep

    def crawl(self, root_url: str) -> SitemapResult:
        self._walk(root_url, depth=0, ancestors=())
        return self.result

    def _walk(self, url: str, depth: int, ancestors: tuple[str, ...]) -> None:
        key = normalize_url(url)
        if key in ancestors:
            self.result.warnings.append(
                f"sitemap index cycle: {url} is listed again below itself, skipped"
            )
            return
        if key in self._visited:
            self.result.warnings.append(f"{url} is listed more than once, read only once")
            return
        self._visited.add(key)

        kind, locs = parse_sitemap(self._download(url), url)
        self.result.fetched += 1

        if kind == "sitemapindex":
            if depth >= self.settings.max_depth:
                raise SitemapError(
                    f"{url}: sitemap indexes are nested deeper than --max-depth "
                    f"({self.settings.max_depth})"
                )
            for loc in self._valid(locs, url, "sitemap"):
                self._walk(loc, depth + 1, (*ancestors, key))
        else:
            for loc in self._valid(locs, url, "page"):
                if len(self.result.urls) >= self.settings.max_urls:
                    raise SitemapError(
                        f"more than {self.settings.max_urls} page URLs found, stopping "
                        "(raise --max-urls if this is expected)"
                    )
                self.result.urls.append(loc)

    def _valid(self, locs: list[str], source: str, what: str) -> list[str]:
        good = []
        for loc in locs:
            try:
                normalize_url(loc)
            except InvalidURLError:
                continue
            good.append(loc)
        skipped = len(locs) - len(good)
        if skipped:
            self.result.warnings.append(
                f"{source}: skipped {skipped} {what} entries with an empty or invalid <loc>"
            )
        return good

    def _download(self, url: str) -> bytes:
        data = self._fetch(url)
        if data[:2] != GZIP_MAGIC:
            return data
        decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        limit = self.settings.max_bytes
        try:
            out = decompressor.decompress(data, limit + 1)
        except zlib.error as exc:
            raise SitemapError(f"{url}: could not decompress gzip data ({exc})") from exc
        if len(out) > limit:
            raise SitemapError(f"{url}: larger than {limit} bytes after decompression")
        if not decompressor.eof:
            raise SitemapError(f"{url}: gzip data is truncated")
        return out

    def _fetch(self, url: str) -> bytes:
        attempts = self.settings.retries + 1
        reason = "unknown error"
        for attempt in range(attempts):
            if attempt:
                self._sleep(self.settings.backoff * 2 ** (attempt - 1))
            try:
                with self.client.stream("GET", url) as response:
                    status = response.status_code
                    if status in RETRY_STATUSES:
                        reason = f"HTTP {status}"
                        continue
                    if status >= 400:
                        raise SitemapError(f"{url}: HTTP {status}")
                    return self._read_body(response, url)
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__} {exc}".strip()
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                raise SitemapError(f"{url}: {type(exc).__name__} {exc}".strip()) from exc
        raise SitemapError(f"{url}: gave up after {attempts} attempt(s), last error: {reason}")

    def _read_body(self, response: httpx.Response, url: str) -> bytes:
        limit = self.settings.max_bytes
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise SitemapError(f"{url}: response is larger than {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
