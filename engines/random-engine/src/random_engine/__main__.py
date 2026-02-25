"""Entry point for the random chess engine.

Run with::

    python -m random_engine

This module wires together the engine and the UCI handler, configures
logging (to stderr so it does not pollute UCI's stdout), and starts the
blocking I/O loop.
"""

from __future__ import annotations

import logging
import sys

from random_engine.engine import RandomEngine
from random_engine.uci import UCIHandler


def main() -> None:
    """Configure logging and start the UCI I/O loop."""
    # Log to stderr at WARNING level by default so debug noise doesn't
    # interfere with the UCI stdout protocol.  Set UCI_LOG_LEVEL=DEBUG in
    # the environment for verbose output during development.
    log_level = logging.WARNING
    logging.basicConfig(
        stream=sys.stderr,
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    engine = RandomEngine()
    handler = UCIHandler(engine)
    handler.run()


if __name__ == "__main__":
    main()
