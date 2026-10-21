"""Exceptions raised for problems in a schematic description."""

from __future__ import annotations


class SchematicError(Exception):
    """Base class for every error caused by the input (not by a bug)."""


class ParseError(SchematicError):
    """The text is not a valid schematic description."""

    def __init__(self, message: str, line_num: int | None = None, line_content: str | None = None):
        self.message = message
        self.line_num = line_num
        self.line_content = line_content
        detail = f"Line {line_num}: {message}" if line_num is not None else message
        if line_content:
            detail += f"\n  > {line_content}"
        super().__init__(detail)


class ImageError(SchematicError):
    """A part image cannot be read."""
