"""Run scripted proposals against a real local Lean kernel."""
from resimind.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["demo", "--domain", "lean"]))
