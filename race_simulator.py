"""CLI entrypoint — delegates to sim.race_simulator (see docs/SIMULATIONS.md)."""

from __future__ import annotations

import sys

from sim.race_simulator import main

if __name__ == "__main__":
    sys.exit(main())
