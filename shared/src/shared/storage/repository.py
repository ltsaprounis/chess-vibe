"""Abstract repository interfaces for the chess-vibe persistence layer.

Defines the contracts that every storage backend must implement.
Methods accept and return domain models only — no raw dicts, file paths,
or SQL leak through these interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from shared.storage.models import (
    Game,
    GameFilter,
    OpeningBook,
    SPRTTest,
    SPRTTestFilter,
)


class GameRepository(ABC):
    """Persistence interface for chess game records.

    Implementations may store games as flat files, in SQLite, or any
    other backend.  Callers depend only on this ABC.
    """

    @abstractmethod
    def save_game(self, game: Game) -> None:
        """Persist a game record.

        If a game with the same ``id`` already exists it is overwritten.

        Args:
            game: The game to save.
        """

    @abstractmethod
    def get_game(self, game_id: str) -> Game | None:
        """Retrieve a game by its unique identifier.

        Args:
            game_id: The UUID of the game.

        Returns:
            The matching game, or ``None`` if not found.
        """

    @abstractmethod
    def list_games(self, game_filter: GameFilter | None = None) -> list[Game]:
        """List games, optionally filtered.

        Args:
            game_filter: Optional filter criteria.  When ``None``, all games
                are returned.

        Returns:
            A list of matching games (may be empty).
        """


class SPRTTestRepository(ABC):
    """Persistence interface for SPRT test metadata."""

    @abstractmethod
    def save_sprt_test(self, test: SPRTTest) -> None:
        """Persist an SPRT test record.

        If a test with the same ``id`` already exists it is overwritten.

        Args:
            test: The SPRT test to save.
        """

    @abstractmethod
    def get_sprt_test(self, test_id: str) -> SPRTTest | None:
        """Retrieve an SPRT test by its unique identifier.

        Args:
            test_id: The UUID of the SPRT test.

        Returns:
            The matching test, or ``None`` if not found.
        """

    @abstractmethod
    def list_sprt_tests(self, test_filter: SPRTTestFilter | None = None) -> list[SPRTTest]:
        """List SPRT tests, optionally filtered.

        Args:
            test_filter: Optional filter criteria.  When ``None``, all tests
                are returned.

        Returns:
            A list of matching tests (may be empty).
        """

    @abstractmethod
    def update_sprt_results(self, test: SPRTTest) -> None:
        """Update the running tallies and status of an SPRT test.

        The test must already exist.  The full ``SPRTTest`` object is saved,
        effectively replacing the previous version.

        Args:
            test: The updated SPRT test.

        Raises:
            KeyError: If no test with the given ``id`` exists.
        """


class OpeningBookRepository(ABC):
    """Persistence interface for opening book files.

    Implementations may store books on the filesystem, in a database,
    or any other backend.  Callers depend only on this ABC.
    """

    @abstractmethod
    def list_books(self) -> list[OpeningBook]:
        """List all available opening books.

        Returns:
            A list of opening book descriptors (may be empty).
        """

    @abstractmethod
    def save_book(self, name: str, content: bytes, format: str) -> OpeningBook:
        """Persist an opening book.

        Args:
            name: Original filename of the book.
            content: Raw file content.
            format: Book format (e.g. ``"pgn"``, ``"epd"``).

        Returns:
            Descriptor of the saved book.
        """

    @abstractmethod
    def get_book_path(self, book_id: str) -> Path | None:
        """Retrieve the filesystem path for a book by its ID.

        Args:
            book_id: The unique identifier of the book.

        Returns:
            The path to the book file, or ``None`` if not found.
        """
