"""The ``lab`` command line: ``lab attempt``, ``lab anchor`` and ``lab report``.

Built with typer, with its rich formatting and pretty tracebacks turned off,
so every line is plain and stable for the researcher agent.

``lab attempt`` and ``lab anchor`` print exactly one line on stdout: a JSON
object with the outcome, the check that refused if one did, a message, and
the ledger entry recorded (or the one that couldn't be). Progress goes to
stderr. The exit code says what happened (see :mod:`lab.outcome`): 0
accepted or a new milestone, 2 bad flags, 3 refused, 4 rejected or flagged,
5 failed, and 1 only for a crash of the lab itself. ``lab report`` prints the
report, or a refusal's JSON line with exit code 3.

SIGTERM, SIGINT and SIGHUP stop a command cleanly: the runner is stopped,
and an attempt that had reached the SPRT is recorded as failed. A command
started ignoring SIGHUP, as under nohup, goes on ignoring it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from lab.anchor import run_anchor
from lab.attempt import AttemptRequest, run_attempt
from lab.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    CorrectnessSettings,
    parse_config,
    read_config,
)
from lab.correctness import CorrectnessResult, check_correctness
from lab.git import Git, GitError, Repo
from lab.lock import exclusive
from lab.outcome import Outcome
from lab.refusal import Refused
from lab.report import report_text
from lab.runs import RunnerDriver
from lab.services import Lab

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="lab",
    help=(
        "Test lab-engine candidates through the SPRT runner, and keep the ledger. "
        "Exit codes: 0 accepted or a new milestone, 2 bad flags, 3 refused, "
        "4 rejected or flagged, 5 failed, 1 a crash of the lab itself."
    ),
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

Command = Callable[[Lab, asyncio.Event], Awaitable[Outcome]]


@dataclass(frozen=True)
class Session:
    """What a command runs with, and where its lock lives.

    Attributes:
        lab: The repository, config, runner and clock.
        lock_dir: The repository's git directory, shared by its worktrees.
    """

    lab: Lab
    lock_dir: Path


def open_repo(path: Path) -> Repo:
    """Git in the repository at ``path``, which must be its top-level directory.

    Raises:
        Refused: If it isn't.
    """
    resolved = path.resolve()
    if not resolved.is_dir():
        raise Refused("repo", f"--repo {path} isn't a directory")
    git = Git(resolved)
    try:
        top = git.toplevel()
    except GitError as e:
        raise Refused("repo", f"--repo {path} isn't in a git repository: {e}") from e
    if top != resolved:
        raise Refused(
            "repo", f"--repo must be the repository's top-level directory, {top}, not {path}"
        )
    return git


def open_session(repo_path: Path, config_path: Path | None) -> Session:
    """Set up a command for the repository at ``repo_path``.

    Raises:
        Refused: If the repository or the config isn't usable.
    """
    git = open_repo(repo_path)
    assert isinstance(git, Git)
    repo = git.repo
    path = config_path or repo / DEFAULT_CONFIG_PATH
    try:
        data = read_config(path)
        config = parse_config(data, path)
    except ConfigError as e:
        raise Refused("config", str(e)) from e
    config_sha256 = hashlib.sha256(data).hexdigest()

    async def check(
        candidate: str, settings: CorrectnessSettings, stop: asyncio.Event
    ) -> CorrectnessResult:
        return await check_correctness(git, candidate, settings, stop)

    lab = Lab(
        repo=repo,
        git=git,
        config=config,
        runner=RunnerDriver(repo),
        check=check,
        config_sha256=config_sha256,
    )
    return Session(lab, git.common_dir())


async def with_signals(work: Callable[[asyncio.Event], Awaitable[Outcome]]) -> Outcome:
    """Run ``work`` with SIGTERM, SIGINT and SIGHUP setting its stop request.

    SIGHUP is left alone if it was ignored at the start, as under nohup.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled = [signal.SIGTERM, signal.SIGINT]
    if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
        handled.append(signal.SIGHUP)

    def on_signal(sig: signal.Signals) -> None:
        logger.warning("Received %s: stopping", sig.name)
        stop.set()

    for sig in handled:
        loop.add_signal_handler(sig, on_signal, sig)
    try:
        return await work(stop)
    finally:
        for sig in handled:
            loop.remove_signal_handler(sig)


def _run(repo: Path, config: Path | None, command: Command) -> Outcome:
    try:
        session = open_session(repo, config)
        with exclusive(session.lock_dir):
            return asyncio.run(with_signals(lambda stop: command(session.lab, stop)))
    except Refused as e:
        return Outcome("refused", e.message, e.check)


def _finish(outcome: Outcome) -> NoReturn:
    typer.echo(outcome.to_json())
    raise typer.Exit(outcome.exit_code)


def _hypothesis(value: str) -> str:
    if not value.strip():
        raise typer.BadParameter("must say what the change is meant to do")
    return value.strip()


RepoOption = Annotated[
    Path,
    typer.Option("--repo", help="The repository's top-level directory.", show_default=False),
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="The config file. Default: <repo>/lab/lab.toml."),
]


@app.command()
def attempt(
    repo: RepoOption,
    base: Annotated[
        str, typer.Option("--base", help="lab/main's tip, which the candidate builds on.")
    ],
    candidate: Annotated[
        str,
        typer.Option(
            "--candidate", help="One commit on --base that changes only engines/lab-engine/."
        ),
    ],
    hypothesis: Annotated[
        str,
        typer.Option("--hypothesis", help="What the change is meant to do.", callback=_hypothesis),
    ],
    config: ConfigOption = None,
) -> None:
    """Test a candidate: scope, no retries, harness, correctness, fast SPRT, confirmation SPRT.

    Accepted, it advances lab/main. Otherwise it's kept under
    refs/lab/rejected/<id>. Either way, once it reaches the SPRT, the attempt
    is recorded in the ledger on lab/main.
    """
    request = AttemptRequest(hypothesis=hypothesis, base=base, candidate=candidate)
    _finish(_run(repo, config, lambda lab, stop: run_attempt(lab, request, stop)))


@app.command()
def anchor(repo: RepoOption, config: ConfigOption = None) -> None:
    """Play lab/main's tip against random-engine and every milestone, in fixed-length matches.

    If every game was clean and it's clearly stronger than the newest
    milestone, it becomes the next one; otherwise it's flagged. Either way the
    run is recorded in the ledger on lab/main.
    """
    _finish(_run(repo, config, run_anchor))


@app.command()
def report(repo: RepoOption) -> None:
    """Sum up the ledger: attempts, acceptance rate, Elo against the milestones, machine hours."""
    try:
        text = report_text(open_repo(repo))
    except Refused as e:
        _finish(Outcome("refused", e.message, e.check))
    typer.echo(text, nl=False)


def main() -> None:
    """Run the command line, with progress logged to stderr."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("lab: %(message)s"))
    root = logging.getLogger("lab")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    app()
