"""Readable messages from pydantic's validation errors."""

from __future__ import annotations

from pydantic import ValidationError


def describe(error: ValidationError) -> str:
    """Name each bad field by its dotted location, with what's wrong with it.

    Args:
        error: The validation error.

    Returns:
        One line, such as ``fast.max_games: Input should be greater than or
        equal to 1``, with several problems separated by semicolons.
    """
    parts: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"])
        message = detail["msg"].removeprefix("Value error, ")
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)
