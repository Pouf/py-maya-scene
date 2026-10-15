"""Text I/O layer for Maya ASCII (`.ma`) files."""

from .document import Document, Token, codeset_encoding

__all__ = ["Document", "Token", "codeset_encoding"]
