"""Encode raw resource names as single Management API path segments."""

from urllib.parse import quote


def quote_path_segment(value: str) -> str:
    """Encode a raw name without allowing URL delimiters or dot traversal."""
    if value in {".", ".."}:
        return value.replace(".", "%2E")
    return quote(value, safe="")
