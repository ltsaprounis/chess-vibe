"""Run the engine: ``python -m lab_engine``.

Importing ``lab_engine.uci`` imports every compiled module, which loads the
compiled code from the numba cache (or compiles it, if the cache is cold)
before the first command is read.
"""

from lab_engine.uci import main

main()
