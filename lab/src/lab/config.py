"""The lab's config file: how ``lab attempt`` and ``lab anchor`` run the SPRT runner.

One TOML file holds every setting the lab needs: the time controls, the SPRT
bounds, the worker count, the book path and the limits. Every key is
required and unknown keys are refused, so a misspelt key can't quietly fall
back to a default. Values are checked as the runner checks its flags, so the
runner doesn't refuse a run the lab starts after an hour of other work.

The values in the repository's ``lab/lab.toml`` are provisional until they're
calibrated on this machine. Every ledger entry records the settings that
decide its verdict, and the config file's SHA-256.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Self

import tomllib
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from shared.time_control import parse_time_control

from lab.validation import describe

# Where the config lives, relative to the repository root.
DEFAULT_CONFIG_PATH = Path("lab/lab.toml")

# The largest SPRT bound accepted, either side of 0. The backend caps its
# requests at the same value: the runner's logistic model overflows far above
# it, and no lab change is worth more.
ELO_LIMIT = 1000.0


class ConfigError(Exception):
    """The config file is missing, unreadable or invalid.

    The message starts with the file's path and names each bad setting.
    """


def _check_time_control(text: str) -> str:
    """Accept a time control only if the runner's own parser does."""
    try:
        parse_time_control(text)
    except ValueError as e:
        raise ValueError(f"isn't a time control the runner accepts ({e})") from e
    return text


TimeControlText = Annotated[str, AfterValidator(_check_time_control)]
NonEmptyText = Annotated[str, Field(min_length=1)]
Command = Annotated[list[NonEmptyText], Field(min_length=1)]
Elo = Annotated[float, Field(ge=-ELO_LIMIT, le=ELO_LIMIT)]
Probability = Annotated[float, Field(gt=0.0, lt=1.0)]
PositiveInt = Annotated[int, Field(ge=1)]
Count = Annotated[int, Field(ge=0)]
Minutes = Annotated[float, Field(gt=0.0)]


class _Settings(BaseModel):
    """Strict, closed and immutable: no coercion, and no unknown keys."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class CorrectnessSettings(_Settings):
    """Step 4 of ``lab attempt``: the candidate's own checks.

    Attributes:
        commands: Commands run in turn, without a shell, in the engine's
            directory of a worktree at the candidate commit. Each must exit 0.
        timeout_minutes: Time allowed for all the commands together.
    """

    commands: Annotated[list[Command], Field(min_length=1)]
    timeout_minutes: Minutes


class StageSettings(_Settings):
    """One SPRT of ``lab attempt``: step 5 (fast) or step 6 (confirmation).

    Attributes:
        tc: Time control, as the runner's ``--tc`` takes it.
        elo0: Elo bound of the null hypothesis.
        elo1: Elo bound of the alternative hypothesis.
        alpha: Type-I error rate.
        beta: Type-II error rate.
        max_games: The runner's ``--max-games``: with no decision by then, the
            result is inconclusive, which rejects the candidate.
        max_time_losses: Most games either side may lose on time before the
            candidate is rejected.
        timeout_minutes: Wall-clock limit on the run, after which the lab stops
            it and the attempt fails.
    """

    tc: TimeControlText
    elo0: Elo
    elo1: Elo
    alpha: Probability
    beta: Probability
    max_games: PositiveInt
    max_time_losses: Count
    timeout_minutes: Minutes

    @model_validator(mode="after")
    def _check_together(self) -> Self:
        if self.elo0 >= self.elo1:
            raise ValueError(f"elo0 must be below elo1, got {self.elo0} and {self.elo1}")
        if self.alpha + self.beta >= 1.0:
            raise ValueError(f"alpha + beta must be below 1, got {self.alpha + self.beta}")
        return self


class AnchorSettings(_Settings):
    """The fixed-length matches of ``lab anchor``.

    Attributes:
        tc: Time control, as the runner's ``--tc`` takes it.
        games: Games in each match.
        max_time_losses: Most games either side may lose on time in a match
            before the version can't become a milestone.
        timeout_minutes: Wall-clock limit on each match.
    """

    tc: TimeControlText
    games: PositiveInt
    max_time_losses: Count
    timeout_minutes: Minutes


class LabConfig(_Settings):
    """Every setting of the lab.

    Attributes:
        concurrency: Games the runner plays at once.
        book: Opening book, relative to the repository root.
        correctness: Step 4 of ``lab attempt``.
        fast: Step 5 of ``lab attempt``, the fast SPRT.
        confirm: Step 6 of ``lab attempt``, the confirmation SPRT.
        anchor: The matches of ``lab anchor``.
    """

    concurrency: PositiveInt
    book: NonEmptyText
    correctness: CorrectnessSettings
    fast: StageSettings
    confirm: StageSettings
    anchor: AnchorSettings


def load_config(path: Path) -> LabConfig:
    """Read and check the config file.

    Args:
        path: The TOML file.

    Returns:
        The settings.

    Raises:
        ConfigError: If the file can't be read, isn't TOML, or has a missing,
            unknown or invalid setting.
    """
    return parse_config(read_config(path), path)


def read_config(path: Path) -> bytes:
    """Read the config file's bytes, to check and to hash the same ones.

    Raises:
        ConfigError: If it can't be read.
    """
    try:
        return path.read_bytes()
    except OSError as e:
        raise ConfigError(f"{path}: can't read the config file ({e.strerror or e})") from e


def parse_config(data: bytes, path: Path) -> LabConfig:
    """Check a config file's bytes.

    Args:
        data: The file's contents.
        path: Where they came from, for messages.

    Returns:
        The settings.

    Raises:
        ConfigError: If they aren't UTF-8 TOML, or have a missing, unknown or
            invalid setting.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path}: isn't UTF-8 ({e})") from e
    try:
        data_tables = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: isn't valid TOML ({e})") from e
    try:
        return LabConfig.model_validate(data_tables)
    except ValidationError as e:
        raise ConfigError(f"{path}: {describe(e)}") from e
