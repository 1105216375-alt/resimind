"""Run two development tasks with the enhanced proposer, never a formal score.

Reuse the v1 budget broker, supervisor, data checks and OS isolation unchanged.
Only the worker module, arm and source inventory are substituted within this
command's context. The historical v1 module and its on-disk sources stay intact.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

from experiments.chinatravel import run as original
from experiments.chinatravel import sandbox
from .replay import source_hashes


@contextmanager
def enhanced_runner():
    original_builder = sandbox.build_worker_command

    def command(*args, **kwargs):
        result = original_builder(*args, **kwargs)
        module = result.index("-m") + 1
        if result[module] != "experiments.chinatravel.upstream_worker":
            raise ValueError("unexpected_worker_command")
        result[module] = "experiments.chinatravel_repair.live_worker"
        return result

    with patch.object(original, "ARMS", ("resimind_repair",)), \
            patch.object(original, "source_hashes", source_hashes), \
            patch.object(sandbox, "build_worker_command", command):
        yield original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "verify", "run", "score"))
    for key in ("output", "data", "upstream"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    output, data, upstream = (getattr(args, name).resolve() for name in ("output", "data", "upstream"))
    with enhanced_runner() as runner:
        if args.command == "freeze":
            with redirect_stdout(io.StringIO()):
                runner.freeze(output, data, upstream, "development")
            manifest = runner.read(output / "manifest.json")
            manifest.update(experiment="task_anchored_local_repair_with_overnight_guard",
                            selection="first two lexicographically sorted UIDs; development only",
                            worker_module="experiments.chinatravel_repair.live_worker")
            runner.save(output / "manifest.json", manifest)
            (output / "manifest.sha256").write_text(runner.sha(output / "manifest.json") + "\n")
            print(json.dumps({"phase": "development", "tasks": 2, "runs": 2,
                              "manifest_sha256": runner.sha(output / "manifest.json")}))
        elif args.command == "verify":
            runner.verify(output, data, upstream)
            print("Enhanced development sources, runtime and data verified.")
        elif args.command == "score":
            runner.summarize(output, data, upstream)
        else:
            if not args.live:
                parser.error("run requires --live")
            if runner.read(output / "manifest.json")["phase"] != "development":
                raise ValueError("enhanced_runner_development_only")
            runner.run(output, data, upstream, args.credential_file)


if __name__ == "__main__":
    main()
