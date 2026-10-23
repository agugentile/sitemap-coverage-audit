"""The comparison itself: live pages (sitemap plus extras) against the monitoring catalog."""

import re
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel

from .normalize import is_ignored, normalize_url


class Counts(BaseModel):
    """All counts are of distinct normalised URLs, after ignore patterns are applied."""

    sitemap: int  # pages listed by the sitemap
    extra: int  # pages listed by extra sources
    extra_only: int  # pages listed by extra sources and missing from the sitemap
    live: int  # sitemap plus extra sources, without duplicates
    catalog: int  # entries in the monitoring catalog
    covered: int  # live pages that are in the catalog
    ignored: int  # URLs dropped because they matched an ignore pattern


class Unmonitored(BaseModel):
    url: str
    source: Literal["sitemap", "extra", "both"]


class AuditResult(BaseModel):
    sitemap: str
    counts: Counts
    coverage_percent: float | None  # None when there are no live pages
    unmonitored: list[Unmonitored]  # live pages that are not in the catalog
    stale: list[str]  # catalog entries that are not live pages
    warnings: list[str]


def compare(
    *,
    sitemap: str,
    sitemap_urls: Iterable[str],
    catalog_urls: Iterable[str],
    extra_urls: Iterable[str] = (),
    ignore: Sequence[re.Pattern[str]] = (),
    warnings: Sequence[str] = (),
) -> AuditResult:
    """Normalise every URL, drop the ignored ones, and diff the sets.

    Ignore patterns apply to all three inputs, so an ignored page is neither reported as
    unmonitored nor as stale.
    """
    from_sitemap = {normalize_url(url) for url in sitemap_urls}
    from_extras = {normalize_url(url) for url in extra_urls}
    in_catalog = {normalize_url(url) for url in catalog_urls}

    seen = from_sitemap | from_extras | in_catalog
    dropped = {url for url in seen if is_ignored(url, ignore)}
    from_sitemap -= dropped
    from_extras -= dropped
    in_catalog -= dropped

    live = from_sitemap | from_extras
    covered = live & in_catalog

    unmonitored = [
        Unmonitored(url=url, source=_source(url, from_sitemap, from_extras))
        for url in sorted(live - in_catalog)
    ]
    return AuditResult(
        sitemap=sitemap,
        counts=Counts(
            sitemap=len(from_sitemap),
            extra=len(from_extras),
            extra_only=len(from_extras - from_sitemap),
            live=len(live),
            catalog=len(in_catalog),
            covered=len(covered),
            ignored=len(dropped),
        ),
        coverage_percent=coverage_percent(len(covered), len(live)),
        unmonitored=unmonitored,
        stale=sorted(in_catalog - live),
        warnings=list(warnings),
    )


def coverage_percent(covered: int, live: int) -> float | None:
    """Percentage of live pages that are monitored, to one decimal, rounded down.

    Rounding down means 100.0 is only ever shown when every live page really is covered.
    """
    if live == 0:
        return None
    return (covered * 1000 // live) / 10


def _source(
    url: str, from_sitemap: set[str], from_extras: set[str]
) -> Literal["sitemap", "extra", "both"]:
    if url in from_sitemap and url in from_extras:
        return "both"
    return "sitemap" if url in from_sitemap else "extra"
