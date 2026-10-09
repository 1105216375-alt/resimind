"""Derive, persist, and reuse a checked identity; no API key required."""
import sys
from resimind.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["demo", "--domain", "knowledge-growth", *sys.argv[1:]]))
