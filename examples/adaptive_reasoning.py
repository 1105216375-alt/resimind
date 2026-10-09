"""Reject, change strategy, finish, learn and reuse; no API key required."""
import sys
from resimind.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["demo", "--domain", "adaptive", *sys.argv[1:]]))
