"""Rendering an AuditResult as text, JSON or Markdown."""

from collections.abc import Callable

from .diff import AuditResult

FORMATS = ("text", "json", "markdown")


def render(result: AuditResult, fmt: str) -> str:
    """Return the report as a string that ends with a newline."""
    return _RENDERERS[fmt](result)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _headline(result: AuditResult) -> str:
    counts = result.counts
    if counts.live == 0:
        return "n/a (no live pages found)"
    return f"{_percent(result.coverage_percent)} ({counts.covered} of {counts.live} live pages)"


def _source_note(source: str) -> str:
    return {"sitemap": "sitemap", "extra": "extra sources only", "both": "sitemap and extras"}[
        source
    ]


def render_text(result: AuditResult) -> str:
    counts = result.counts
    lines = [
        "Sitemap coverage audit",
        f"Sitemap: {result.sitemap}",
        "",
        f"Coverage:        {_headline(result)}",
        f"Live pages:      {counts.live} (sitemap {counts.sitemap}, "
        f"only in extra sources {counts.extra_only})",
        f"Catalog entries: {counts.catalog}",
        f"Unmonitored:     {len(result.unmonitored)}",
        f"Stale entries:   {len(result.stale)}",
    ]
    if counts.ignored:
        lines.append(f"Ignored:         {counts.ignored}")

    lines += ["", "Unmonitored pages (live, but not in the catalog):"]
    if result.unmonitored:
        width = max(len(item.url) for item in result.unmonitored)
        lines += [
            f"  {item.url.ljust(width)}  [{_source_note(item.source)}]"
            for item in result.unmonitored
        ]
    else:
        lines.append("  none")

    lines += ["", "Stale catalog entries (in the catalog, but not live):"]
    lines += [f"  {url}" for url in result.stale] or ["  none"]

    if result.warnings:
        lines += ["", "Warnings:"]
        lines += [f"  {warning}" for warning in result.warnings]
    return "\n".join(lines) + "\n"


def render_json(result: AuditResult) -> str:
    return result.model_dump_json(indent=2) + "\n"


def _cell(url: str) -> str:
    return url.replace("|", "%7C")


def render_markdown(result: AuditResult) -> str:
    counts = result.counts
    lines = [
        "# Sitemap coverage audit",
        "",
        f"Sitemap: `{result.sitemap}`",
        "",
        f"**Coverage: {_headline(result)}**",
        "",
        "| | Count |",
        "|---|---:|",
        f"| Live pages | {counts.live} |",
        f"| Listed by the sitemap | {counts.sitemap} |",
        f"| Only in extra sources | {counts.extra_only} |",
        f"| Catalog entries | {counts.catalog} |",
        f"| Unmonitored | {len(result.unmonitored)} |",
        f"| Stale | {len(result.stale)} |",
    ]
    if counts.ignored:
        lines.append(f"| Ignored | {counts.ignored} |")

    lines += ["", f"## Unmonitored pages ({len(result.unmonitored)})", ""]
    if result.unmonitored:
        lines += ["Live pages that are not in the catalog.", "", "| URL | Found in |", "|---|---|"]
        lines += [
            f"| {_cell(item.url)} | {_source_note(item.source)} |" for item in result.unmonitored
        ]
    else:
        lines.append("None.")

    lines += ["", f"## Stale catalog entries ({len(result.stale)})", ""]
    if result.stale:
        lines += [
            "Catalog entries that are not in the sitemap or extra sources. "
            "Check each one before removing it: the page may have moved or been "
            "left out of the sitemap by mistake.",
            "",
        ]
        lines += [f"- {_cell(url)}" for url in result.stale]
    else:
        lines.append("None.")

    if result.warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {warning}" for warning in result.warnings]
    return "\n".join(lines) + "\n"


_RENDERERS: dict[str, Callable[[AuditResult], str]] = {
    "text": render_text,
    "json": render_json,
    "markdown": render_markdown,
}
