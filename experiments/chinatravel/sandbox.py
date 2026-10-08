"""Fail-closed macOS seatbelt launch profiles for oracle-blind workers.

The supervisor must also scrub the environment and close inherited descriptors.
Profiles cannot remove secrets already inherited in environment variables/FDs.
There is deliberately no unsandboxed fallback when sandbox-exec is unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=True)


def _quoted(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def profile_for_worker(python: str | Path, upstream_root: str | Path,
                       public_repo: str | Path, output_dir: str | Path,
                       public_query_path: str | Path, database_dir: str | Path,
                       port: int) -> str:
    """Return a default-deny profile; all supplied paths must already exist.

    Only ``upstream_root/chinatravel`` is code-readable, never the checkout's
    sibling data/evaluation-label directories. Inputs must be curated code and
    database roots without gold labels. Query access is limited to one file.
    ``output_dir`` must be a fresh per-job directory containing no secrets.
    """
    if sys.platform != "darwin" or not Path(SANDBOX_EXEC).is_file():
        raise RuntimeError("macOS sandbox-exec is required; refusing unsandboxed worker")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("IPC port must be between 1 and 65535")
    # Preserve the venv invocation path as well as its real binary target.
    supplied = Path(python).expanduser().absolute()
    invocation = _path(supplied.parent) / supplied.name
    executable = _path(invocation)
    upstream, repo, output, query, database = map(
        _path, (upstream_root, public_repo, output_dir, public_query_path, database_dir))
    if not query.is_file() or not all(p.is_dir() for p in (upstream, repo, output, database)):
        raise ValueError("query must be a file; supplied roots must be directories")
    details = subprocess.run(
        [str(invocation), "-I", "-S", "-c",
         "import json,sys;print(json.dumps({'base':sys.base_prefix,'version':list(sys.version_info[:2])}))"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    runtime = json.loads(details.stdout)
    base = _path(runtime["base"])
    venv = _path(invocation.parent.parent)
    # Base lib supplies stdlib/native libraries; framework Python also loads
    # its versioned Python binary. Do not grant the base prefix indiscriminately.
    trees = {_path(upstream / "chinatravel"), _path(repo / "src"),
             _path(repo / "experiments"), database, output, venv / "lib", venv / "bin", base / "lib",
             Path("/System/Library"), Path("/usr/lib"), Path("/usr/share/locale"),
             Path("/usr/share/zoneinfo"), Path("/private/var/db/timezone"),
             Path("/System/Volumes/Preboot/Cryptexes/OS/usr/lib"),
             Path("/System/Volumes/Preboot/Cryptexes/OS/System/Library")}
    literals = {Path("/"), invocation, executable, venv, base, repo, upstream, query,
                venv / "pyvenv.cfg", venv / "bin", base / "Python",
                Path("/dev/null"), Path("/dev/random"), Path("/dev/urandom")}
    # macOS /tmp and /var are symlinks: resolving an allowed path also needs
    # permission to inspect these links, without granting their whole trees.
    for supplied_path in (python, upstream_root, public_repo, output_dir, public_query_path, database_dir):
        for ancestor in Path(supplied_path).expanduser().absolute().parents:
            if ancestor.is_symlink():
                literals.add(ancestor)
    # Ancestor metadata permits traversal without exposing their file contents.
    ancestors = {parent for path in trees | literals for parent in path.parents}
    rules = ["(version 1)", "(deny default)", "(allow sysctl-read)",
             "(allow process-fork)",
             f"(allow process-exec (literal {_quoted(invocation)}) (literal {_quoted(executable)}))"]
    rules.append("(allow file-read-metadata " + " ".join(
        f"(literal {_quoted(path)})" for path in sorted(ancestors)) + ")")
    rules.append("(allow file-read* " + " ".join(
        [f"(literal {_quoted(path)})" for path in sorted(literals)] +
        [f"(subpath {_quoted(path)})" for path in sorted(trees)]) + ")")
    rules.extend((f"(allow file-write* (subpath {_quoted(output)}))",
                  f'(allow network-outbound (remote ip "localhost:{port}"))'))
    return "\n".join(rules)


def build_worker_command(python: str | Path, worker_args: list[str],
                         upstream_root: str | Path, public_repo: str | Path,
                         output_dir: str | Path, public_query_path: str | Path,
                         database_dir: str | Path, port: int) -> list[str]:
    """Build an isolated module invocation; startup failure must stop the job."""
    if type(worker_args) is not list or any(type(arg) is not str for arg in worker_args):
        raise TypeError("worker_args must be a list of strings")
    profile = profile_for_worker(python, upstream_root, public_repo, output_dir,
                                 public_query_path, database_dir, port)
    supplied = Path(python).expanduser().absolute()
    return [SANDBOX_EXEC, "-p", profile, str(_path(supplied.parent) / supplied.name),
            "-m", "experiments.chinatravel.upstream_worker", *worker_args]
