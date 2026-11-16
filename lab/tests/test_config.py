"""Tests for loading and checking the lab's config file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from lab.config import ConfigError, LabConfig, load_config

from tests.helpers import config_data, toml_dumps

SHIPPED_CONFIG = Path(__file__).resolve().parents[1] / "lab.toml"
NO_WORDS: list[list[str]] = [[]]


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "lab.toml"
    path.write_text(toml_dumps(data))
    return path


class TestLoad:
    def test_reads_every_setting(self, tmp_path: Path) -> None:
        config = load_config(_write(tmp_path, config_data()))

        assert isinstance(config, LabConfig)
        assert config.concurrency == 2
        assert config.book == "books/test.epd"
        assert config.correctness.commands == [["python3", "-c", "pass"]]
        assert config.correctness.timeout_minutes == 5
        assert config.fast.tc == "wtime=1000,btime=1000,winc=10,binc=10"
        assert (config.fast.elo0, config.fast.elo1) == (0.0, 10.0)
        assert (config.fast.alpha, config.fast.beta) == (0.05, 0.05)
        assert config.fast.max_games == 100
        assert config.fast.max_time_losses == 1
        assert config.fast.timeout_minutes == 30
        assert config.confirm.tc == "wtime=4000,btime=4000,winc=40,binc=40"
        assert config.confirm.max_games == 200
        assert config.anchor.games == 20
        assert config.anchor.max_time_losses == 0
        assert config.anchor.timeout_minutes == 15

    def test_an_integer_is_a_valid_float(self, tmp_path: Path) -> None:
        config = load_config(_write(tmp_path, config_data(**{"fast.elo1": 20})))

        assert config.fast.elo1 == 20.0

    def test_the_shipped_config_is_valid(self) -> None:
        config = load_config(SHIPPED_CONFIG)

        assert config.book == "books/2moves_v1.epd"
        assert config.fast.max_games >= 1

    def test_a_missing_file_is_named(self, tmp_path: Path) -> None:
        missing = tmp_path / "nowhere.toml"

        with pytest.raises(ConfigError, match=r"nowhere\.toml"):
            load_config(missing)

    def test_bytes_that_are_not_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "lab.toml"
        path.write_bytes(b'book = "\xff"\n')

        with pytest.raises(ConfigError, match="isn't UTF-8"):
            load_config(path)

    def test_text_that_is_not_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "lab.toml"
        path.write_text("concurrency = \n")

        with pytest.raises(ConfigError, match="isn't valid TOML"):
            load_config(path)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param({"concurrency": 0}, "concurrency", id="no-workers"),
        pytest.param({"concurrency": True}, "concurrency", id="bool-for-int"),
        pytest.param({"concurrency": "9"}, "concurrency", id="string-for-int"),
        pytest.param({"book": ""}, "book", id="empty-book"),
        pytest.param({"fast.max_games": 0}, "fast.max_games", id="no-games"),
        pytest.param({"fast.max_time_losses": -1}, "fast.max_time_losses", id="negative-losses"),
        pytest.param({"fast.timeout_minutes": 0}, "fast.timeout_minutes", id="no-time"),
        pytest.param({"fast.alpha": 0.0}, "fast.alpha", id="alpha-zero"),
        pytest.param({"fast.alpha": 1.0}, "fast.alpha", id="alpha-one"),
        pytest.param({"fast.beta": 0.0}, "fast.beta", id="beta-zero"),
        pytest.param({"fast.beta": 1.0}, "fast.beta", id="beta-one"),
        pytest.param({"fast.alpha": 0.5, "fast.beta": 0.5}, "alpha + beta", id="alpha-beta"),
        pytest.param({"fast.elo0": 10.0}, "elo0 must be below elo1", id="equal-bounds"),
        pytest.param({"fast.elo0": 20.0}, "elo0 must be below elo1", id="crossed-bounds"),
        pytest.param({"fast.elo1": 1000.5}, "fast.elo1", id="elo1-too-high"),
        pytest.param({"fast.elo0": -1000.5}, "fast.elo0", id="elo0-too-low"),
        pytest.param({"fast.tc": "garbage"}, "fast.tc", id="tc-garbage"),
        pytest.param({"fast.tc": "movetime=soon"}, "fast.tc", id="tc-not-a-number"),
        pytest.param({"fast.tc": "movetime=0"}, "fast.tc", id="tc-zero"),
        pytest.param({"fast.tc": ...}, "fast.tc", id="tc-missing"),
        pytest.param({"fast.elo_1": 10.0}, "fast.elo_1", id="unknown-key"),
        pytest.param({"workers": 9}, "workers", id="unknown-top-level-key"),
        pytest.param({"anchor": ...}, "anchor", id="table-missing"),
        pytest.param({"confirm.max_games": 0}, "confirm.max_games", id="confirm-checked"),
        pytest.param({"confirm.tc": "nodes=x"}, "confirm.tc", id="confirm-tc-checked"),
        pytest.param({"correctness.commands": []}, "correctness.commands", id="no-commands"),
        pytest.param(
            {"correctness.commands": NO_WORDS}, "correctness.commands", id="empty-command"
        ),
        pytest.param({"correctness.commands": [[""]]}, "correctness.commands", id="empty-word"),
        pytest.param({"correctness.timeout_minutes": 0}, "correctness.timeout_minutes", id="ct"),
        pytest.param({"anchor.games": 0}, "anchor.games", id="no-anchor-games"),
        pytest.param({"anchor.tc": "depth"}, "anchor.tc", id="anchor-tc-checked"),
        pytest.param({"anchor.max_time_losses": -1}, "anchor.max_time_losses", id="anchor-losses"),
        pytest.param({"anchor.timeout_minutes": 0}, "anchor.timeout_minutes", id="anchor-time"),
    ],
)
def test_refuses_a_bad_setting_by_name(
    tmp_path: Path, changes: dict[str, Any], expected: str
) -> None:
    path = _write(tmp_path, config_data(**changes))

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    message = str(caught.value)
    assert expected in message
    assert message.startswith(f"{path}: ")
