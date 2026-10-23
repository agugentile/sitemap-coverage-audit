import gzip
from pathlib import Path

import respx

FIXTURES = Path(__file__).parent / "fixtures"
SITE = "https://example.com"


def fixture_path(name: str) -> Path:
    return FIXTURES / name


def serve(
    router: respx.MockRouter,
    path: str,
    fixture: str,
    *,
    gzipped: bool = False,
    headers: dict[str, str] | None = None,
) -> respx.Route:
    """Serve a fixture file at SITE + path."""
    body = fixture_path(fixture).read_bytes()
    if gzipped:
        body = gzip.compress(body)
    return router.get(f"{SITE}{path}").respond(200, content=body, headers=headers or {})
