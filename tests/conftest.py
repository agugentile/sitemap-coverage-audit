import time
from collections.abc import Iterator

import pytest
import respx

from .helpers import serve


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries back off with time.sleep. Tests never wait."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


@pytest.fixture
def http() -> Iterator[respx.MockRouter]:
    """A mock HTTP router. A request without a matching route fails the test."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def site(http: respx.MockRouter) -> respx.MockRouter:
    """A small site: an index with a plain sitemap, a gzip sitemap and a nested index."""
    serve(http, "/sitemap.xml", "index_root.xml")
    serve(http, "/sitemaps/pages.xml", "urlset_pages.xml")
    serve(http, "/sitemaps/blog.xml.gz", "urlset_blog.xml", gzipped=True)
    serve(http, "/sitemaps/more.xml", "index_more.xml")
    serve(http, "/sitemaps/deep.xml", "urlset_deep.xml")
    return http
