"""Entry point for ``python -m random_engine``.

Starts the UCI protocol loop so the engine can be launched as a subprocess
by a chess GUI or the shared UCI client::

    python -m random_engine
"""

from random_engine.uci import run_uci_loop

run_uci_loop()
