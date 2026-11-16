"""Names in the repository that the lab relies on."""

from __future__ import annotations

# The engine the lab improves, as engines.json registers it, and its directory.
# A candidate may change nothing outside that directory.
ENGINE_ID = "lab-engine"
ENGINE_DIR = "engines/lab-engine"

# The engine every anchor run plays first: the floor any version must clear.
RANDOM_ENGINE_ID = "random-engine"

# The harness: what plays, scores and judges the games, from the working
# tree: the runner, the shared library, and the lab itself, its config
# included. It must be clean while the lab runs, and its commit is recorded.
HARNESS_PATHS = ("sprt-runner", "shared", "lab")

# The branch of accepted engine changes, which carries the ledger. It's local:
# the lab never pushes, and never creates it.
LAB_MAIN = "refs/heads/lab/main"
LAB_MAIN_BRANCH = "lab/main"

# Where a candidate that wasn't accepted is kept, by its attempt's id.
REJECTED_PREFIX = "refs/lab/rejected/"
