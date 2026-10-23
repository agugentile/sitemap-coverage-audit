"""Command line entry point: sitemap-audit."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .catalog import build_proposal, check_proposal_path, load_catalog, write_proposal
from .diff import compare
from .errors import AuditError
from .extras import load_extra
from .normalize import compile_patterns
from .report import FORMATS, render
from .sitemap import DEFAULT_USER_AGENT, FetchSettings, collect_urls

EXIT_OK = 0
EXIT_GAPS = 1
EXIT_ERROR = 2

EPILOG = """\
exit codes:
  0  the audit ran (and, with --fail-on-gaps, found no unmonitored pages)
  1  --fail-on-gaps was given and at least one live page is unmonitored
  2  usage or input error: bad flags, unreadable file, invalid catalog, sitemap not reachable

The catalog is never modified. --propose writes a separate, new file.
"""


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sitemap-audit",
        description=(
            "Find pages that a monitoring catalog does not cover, by diffing a sitemap "
            "(and optional extra URL lists) against the catalog."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    source = parser.add_argument_group("inputs")
    source.add_argument(
        "--sitemap", required=True, metavar="URL", help="sitemap or sitemap index URL"
    )
    source.add_argument(
        "--catalog",
        required=True,
        type=Path,
        metavar="FILE",
        help="monitoring catalog: JSON list of URL strings or of {'url': ...} objects",
    )
    source.add_argument(
        "--extra",
        action="extend",
        nargs="+",
        type=Path,
        default=[],
        metavar="FILE",
        help="extra list of live URLs (JSON like the catalog, or .txt with one URL per line); "
        "can be repeated",
    )
    source.add_argument(
        "--ignore",
        action="extend",
        nargs="+",
        default=[],
        metavar="REGEX",
        help="skip URLs matching this regex (searched in the normalised URL); can be repeated",
    )

    output = parser.add_argument_group("output")
    output.add_argument("--format", choices=FORMATS, default="text", help="report format")
    output.add_argument(
        "--propose",
        type=Path,
        metavar="FILE",
        help="write a new JSON file: a copy of the catalog plus the unmonitored URLs. "
        "Refuses to overwrite an existing file",
    )
    output.add_argument(
        "--fail-on-gaps",
        action="store_true",
        help="exit with code 1 if any live page is unmonitored",
    )

    network = parser.add_argument_group("network and safety limits")
    network.add_argument(
        "--timeout", type=_positive_float, default=15.0, metavar="SECONDS", help="per request"
    )
    network.add_argument(
        "--retries",
        type=_non_negative_int,
        default=2,
        metavar="N",
        help="extra attempts after a timeout, connection error, 429 or 5xx (default: 2)",
    )
    network.add_argument(
        "--max-depth",
        type=_non_negative_int,
        default=5,
        metavar="N",
        help="how deeply sitemap index files may be nested (default: 5)",
    )
    network.add_argument(
        "--max-urls",
        type=_positive_int,
        default=100_000,
        metavar="N",
        help="stop if a sitemap tree lists more page URLs than this (default: 100000)",
    )
    network.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        metavar="TEXT",
        help="User-Agent header. Add contact details when you audit a site you do not run",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:  # argparse exits on --help and on usage errors
        code = exit_request.code
        return code if isinstance(code, int) else EXIT_ERROR

    try:
        return _run(args)
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _run(args: argparse.Namespace) -> int:
    # Read and check everything local first, so mistakes fail before any network request.
    ignore = compile_patterns(args.ignore)
    catalog = load_catalog(args.catalog)
    extra_urls = [url for path in args.extra for url in load_extra(path)]
    if args.propose is not None:
        check_proposal_path(args.propose)

    settings = FetchSettings(
        timeout=args.timeout,
        retries=args.retries,
        max_depth=args.max_depth,
        max_urls=args.max_urls,
        user_agent=args.user_agent,
    )
    fetched = collect_urls(args.sitemap, settings)

    result = compare(
        sitemap=args.sitemap,
        sitemap_urls=fetched.urls,
        catalog_urls=catalog.urls,
        extra_urls=extra_urls,
        ignore=ignore,
        warnings=fetched.warnings,
    )
    sys.stdout.write(render(result, args.format))

    if args.propose is not None:
        new_urls = [item.url for item in result.unmonitored]
        if new_urls:
            write_proposal(args.propose, build_proposal(catalog, new_urls))
            print(f"wrote {len(new_urls)} proposed addition(s) to {args.propose}", file=sys.stderr)
        else:
            print("no unmonitored pages, so no proposal file was written", file=sys.stderr)

    if args.fail_on_gaps and result.unmonitored:
        return EXIT_GAPS
    return EXIT_OK
