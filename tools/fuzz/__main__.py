"""``python -m tools.fuzz``; see ``tools/fuzz/README.md``."""

import sys

# Spawned workers import this module as ``__mp_main__``; the guard keeps them from importing the CLI, and grus with it.
if __name__ == "__main__":
    from tools.fuzz import cli

    sys.exit(cli.main())
