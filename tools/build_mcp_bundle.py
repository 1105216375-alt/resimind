"""Build a deterministic allow-listed MCPB archive and Registry manifest.

Run with Python 3.11+. The UV lock is committed; this builder does not resolve
or download dependencies. Host support for MCPB 0.4 UV runtime is required.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    bundle_config = ROOT / "distribution" / "mcpb"
    if tomllib.loads((bundle_config / "pyproject.toml").read_text())["project"]["version"] != version:
        raise ValueError("bundle/core version mismatch")
    manifest = {
        "manifest_version": "0.4", "name": "resimind", "display_name": "ResiMind — Verified Math & Rules",
        "version": version, "description": "Check polynomial identities, expand exactly, and learn reusable verified rules locally.",
        "author": {"name": "1105216375-alt", "url": "https://github.com/1105216375-alt"},
        "homepage": "https://github.com/1105216375-alt/resimind",
        "repository": {"type": "git", "url": "https://github.com/1105216375-alt/resimind"},
        "documentation": f"https://github.com/1105216375-alt/resimind/blob/v{version}/docs/agent-tools.md",
        "server": {"type": "uv", "entry_point": "src/server.py",
                   "mcp_config": {"command": "uv", "args": ["run", "--directory", "${__dirname}", "--locked", "src/server.py"]}},
        "compatibility": {"platforms": ["darwin", "linux", "win32"], "runtimes": {"python": ">=3.11"}},
        "keywords": ["math", "verification", "polynomial", "agent-memory"], "license": "MIT",
        "long_description": "Bounded rational polynomial tools with exact coefficient verification. "
            "Runs locally without a model key. Learning changes process-local memory only, cleared on restart. "
            "Requires an MCPB 0.4 UV-capable host, or the uv command on PATH. First setup downloads Python/dependencies. "
            "Does not expose Lean, general mathematical proofs or engineering certification."
    }
    files = {"manifest.json": (json.dumps(manifest, indent=2) + "\n").encode(),
             "LICENSE": (ROOT / "LICENSE").read_bytes(),
             "pyproject.toml": (bundle_config / "pyproject.toml").read_bytes(),
             "uv.lock": (bundle_config / "uv.lock").read_bytes(),
             "src/server.py": (bundle_config / "server.py").read_bytes()}
    for path in sorted((ROOT / "src" / "resimind").rglob("*.py")):
        if path.is_symlink():
            raise ValueError("bundle source must not be a symlink")
        files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    bundle = destination / f"resimind-{version}.mcpb"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    registry = {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        "name": "io.github.1105216375-alt/resimind", "title": "ResiMind",
        "description": "Exact polynomial checks, sparse expansion and verified rule memory. Local MCP tools, no model key.",
        "repository": {"url": "https://github.com/1105216375-alt/resimind", "source": "github"},
        "version": version,
        "packages": [{"registryType": "mcpb",
            "identifier": f"https://github.com/1105216375-alt/resimind/releases/download/v{version}/{bundle.name}",
            "fileSha256": digest, "transport": {"type": "stdio"}}]}
    (destination / "server.json").write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return bundle, registry


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bundle, _ = build(args.output)
    print(bundle)
