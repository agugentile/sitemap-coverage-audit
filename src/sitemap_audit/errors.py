"""Exceptions. Anything derived from AuditError ends the run with exit code 2."""


class AuditError(Exception):
    """Base class for problems the user can fix: bad input, bad flags, unreachable sitemap."""


class InvalidURLError(AuditError, ValueError):
    """A string is not an absolute http(s) URL."""


class InputError(AuditError):
    """A catalog, extra URL list, ignore pattern or output path is not usable."""


class SitemapError(AuditError):
    """A sitemap could not be fetched or parsed, or a safety limit was hit."""
