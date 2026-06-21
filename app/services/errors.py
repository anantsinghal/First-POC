from __future__ import annotations


class NotFoundError(Exception):
    """Raised by services when a requested entity (scan/finding) is not found; mapped to 404 in API."""
    pass
