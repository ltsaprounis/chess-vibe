"""Tests for running one lab command at a time in a repository."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from lab.lock import LOCK_NAME, exclusive
from lab.refusal import Refused


def test_a_second_command_is_refused_while_the_first_runs(tmp_path: Path) -> None:
    with exclusive(tmp_path), pytest.raises(Refused) as caught, exclusive(tmp_path):
        pass

    assert caught.value.check == "busy"
    assert f"pid {os.getpid()}" in caught.value.message
    assert (tmp_path / LOCK_NAME).exists()


def test_the_lock_is_released_at_the_end(tmp_path: Path) -> None:
    with exclusive(tmp_path):
        pass

    with exclusive(tmp_path):
        pass


def test_the_lock_is_released_by_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), exclusive(tmp_path):
        raise RuntimeError("boom")

    with exclusive(tmp_path):
        pass


def test_records_the_holder(tmp_path: Path) -> None:
    with exclusive(tmp_path):
        assert (tmp_path / LOCK_NAME).read_text() == f"{os.getpid()}\n"
