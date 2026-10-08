"""Prepare the pinned complete Chinese Human-Val release without network access.

Requires only Python's standard library. Input URLs and example commands are in
PROTOCOL.md. No queries or sandbox payloads are bundled in this repository.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile


UPSTREAM_COMMIT = "f445e0011f42594fcc29b8c752ece06ded9a8218"
QUERY_REVISION = "a7d893afb628af6ca181625b6c7cca2d60a6bccb"
SANDBOX_REVISION = "885c7dad143bf040238ddca263f4788d582ee0f4"
HUMAN_SHA256 = "2f0d61e37665a345bd428732ace6e1ce9d6de2af8d292047bcf5cc1c4f9cfc78"
ZIP_SHA256 = "42d95ff7b8f96574d0ee972ca77599258e4854a219617868caeb462b79c715bd"
CHECKSUMS_SHA256 = "43fe164ea607708e1d3bf1663a73b321ef00eff429a04d5a2edb03bea1d949e3"
PUBLIC_FIELDS = ("uid", "nature_language")
QUERY_FIELDS = {
    "uid", "tag", "start_city", "target_city", "days", "people_number",
    "limit_rooms", "limits_room_type", "hard_logic_py", "nature_language",
    "nature_language_en",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_source(path: Path, expected: str) -> None:
    require(sha(path) == expected, f"Pinned source SHA-256 mismatch: {path.name}")


def load_queries(human_csv: Path, upstream: Path) -> list[dict]:
    verify_source(human_csv, HUMAN_SHA256)
    with human_csv.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        require(set(reader.fieldnames or ()) == QUERY_FIELDS, "Unexpected query schema")
        rows = list(reader)
    for row in rows:
        for key in ("days", "people_number"):
            row[key] = int(row[key])
        for key in ("limit_rooms", "limits_room_type"):
            require(row[key] in ("True", "False"), f"Invalid boolean: {key}")
            row[key] = row[key] == "True"
        constraints = ast.literal_eval(row["hard_logic_py"])
        require(
            isinstance(constraints, list) and bool(constraints)
            and all(isinstance(item, str) and bool(item) for item in constraints),
            "hard_logic_py must decode to a nonempty list of strings",
        )
        row["hard_logic_py"] = constraints
    ids = [row["uid"] for row in rows]
    require(len(ids) == len(set(ids)) == 154, "Expected 154 distinct Human-Val UIDs")
    split = (upstream / "chinatravel/evaluation/default_splits/human.txt").read_text().splitlines()
    require(len(split) == 154 and set(ids) == set(split), "Official Human-Val split mismatch")
    require(sum(len(row["hard_logic_py"]) for row in rows) == 683, "Constraint count mismatch")
    return rows


def load_sandbox(archive: Path, checksum_file: Path) -> dict[str, bytes]:
    verify_source(archive, ZIP_SHA256)
    verify_source(checksum_file, CHECKSUMS_SHA256)
    expected = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        name = PurePosixPath(relative)
        require(not name.is_absolute() and ".." not in name.parts, "Unsafe checksum path")
        require(relative not in expected, "Duplicate checksum entry")
        expected[relative] = digest
    require(len(expected) == 132, "Expected 132 official sandbox checksums")
    contents = {}
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            name = PurePosixPath(entry.filename)
            require(
                not name.is_absolute() and ".." not in name.parts
                and len(name.parts) > 1 and name.parts[0] == "database",
                "Unsafe archive path",
            )
            require(not stat.S_ISLNK(entry.external_attr >> 16), "Archive symlink is not allowed")
            require(not entry.is_dir(), "Unexpected archive directory entry")
            relative = str(PurePosixPath(*name.parts[1:]))
            require(relative not in contents, "Duplicate archive entry")
            content = zipped.read(entry)  # Also validates each ZIP CRC.
            require(
                hashlib.sha256(content).hexdigest() == expected.get(relative),
                f"Official sandbox file checksum mismatch: {relative}",
            )
            contents[relative] = content
    require(set(contents) == set(expected), "Sandbox file set mismatch")
    require(sum(map(len, contents.values())) == 5125256, "Sandbox byte count mismatch")
    return contents


def prepare(human_csv: Path, archive: Path, checksum_file: Path, upstream: Path,
            output: Path, *, link_sandbox: bool = False) -> dict:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
    require(revision == UPSTREAM_COMMIT, "Official code revision mismatch")
    rows = load_queries(human_csv, upstream)
    sandbox = load_sandbox(archive, checksum_file)
    link = upstream / "chinatravel/environment/database"
    if link_sandbox and (link.exists() or link.is_symlink()):
        require(link.resolve() == output / "database", "Existing upstream database points elsewhere")
    # Avoid overwriting an earlier prepared release or mixing its metadata.
    output.mkdir(parents=True, exist_ok=False)
    for source, relative in (
        (human_csv, "source/query/human.csv"),
        (archive, "source/sandbox/raw/ChinaTravel_sandbox_zh.zip"),
        (checksum_file, "source/sandbox/manifests/SHA256SUMS.zh"),
    ):
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for relative, content in sandbox.items():
        path = output / "database" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        require(sha(path) == hashlib.sha256(content).hexdigest(), "Extracted file checksum mismatch")
    public = []
    for row in rows:
        public_row = {key: row[key] for key in PUBLIC_FIELDS}
        public.append(public_row)
        save(output / "gold" / f"{row['uid']}.json", row)
        save(output / "public" / f"{row['uid']}.json", public_row)
    save(output / "human_gold.json", rows)
    save(output / "human_public.json", public)
    ids = sorted(row["uid"] for row in rows)
    for name, selected in (("all", ids), ("dev", ids[:2]), ("heldout", ids[2:])):
        (output / f"{name}_uids.txt").write_text("\n".join(selected) + "\n", encoding="utf-8")
    if link_sandbox and not (link.exists() or link.is_symlink()):
        link.symlink_to(output / "database", target_is_directory=True)
    manifest = {
        "schema_version": 1, "benchmark": "ChinaTravel", "language": "zh",
        "query_split": "human", "query_split_paper_name": "Human-Val", "query_count": 154,
        "licenses": {"code": "MIT", "query": "CC BY 4.0", "sandbox": "CC BY 4.0"},
        "upstream": {"url": "https://github.com/LAMDA-NeSy/ChinaTravel", "commit": revision},
        "query": {"url": "https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel",
                  "revision": QUERY_REVISION, "raw_file": "source/query/human.csv", "sha256": HUMAN_SHA256},
        "sandbox": {
            "url": "https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox",
            "revision": SANDBOX_REVISION, "release_version": "2026.08.2",
            "archive": "source/sandbox/raw/ChinaTravel_sandbox_zh.zip",
            "archive_sha256": ZIP_SHA256, "archive_bytes": archive.stat().st_size,
            "uncompressed_bytes": sum(map(len, sandbox.values())), "file_count": 132,
            "every_file_verified": True, "path": str(output / "database"),
            "upstream_database_symlink": str(link) if link_sandbox else None,
            "table_row_counts": {"attractions": 3413, "restaurants": 4655,
                                 "accommodations": 3866, "trains": 5345, "flights": 720,
                                 "poi": 12172, "subway_stations": 3635},
        },
        "normalization": {"ints": ["days", "people_number"],
                          "booleans": ["limit_rooms", "limits_room_type"],
                          "python_list_literal_to_list": ["hard_logic_py"],
                          "text_and_constraints_changed": False},
        "split_policy": "All 154 retained. Lexicographically first two UIDs reserved for development before model results; other 152 form the heldout pool.",
        "dev_uids": ids[:2], "heldout_uids": ids[2:], "all_uids": ids,
        "solver_public_fields": list(PUBLIC_FIELDS),
        "oracle_fields_removed_from_public": sorted(QUERY_FIELDS - set(PUBLIC_FIELDS)),
        "hard_logic_constraint_count": 683, "query_uids_match_official_split": True,
        "source_files": {
            str(path.relative_to(output)): {"sha256": sha(path), "bytes": path.stat().st_size}
            for path in sorted((output / "source").rglob("*")) if path.is_file()
        },
        "normalized_files": {
            str(path.relative_to(output)): sha(path)
            for category in ("gold", "public") for path in sorted((output / category).glob("*.json"))
        },
        "sandbox_file_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(sandbox.items())},
        "preparation": {"script": "experiments/chinatravel/prepare_data.py",
                        "script_sha256": sha(Path(__file__)), "network_access": False},
        "caveats": [
            "Maintained official data/evaluator includes 2026.08 repairs; not an exact historical-paper score reproduction.",
            "Manifest audit fields and local paths can differ from the initial preparation snapshot; all normalized query bytes and sandbox hashes are reproducible.",
        ],
    }
    save(output / "dataset_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--human-csv", required=True, type=Path)
    parser.add_argument("--sandbox-zip", required=True, type=Path)
    parser.add_argument("--sandbox-checksums", required=True, type=Path)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--link-sandbox", action="store_true",
                        help="Install a non-overwriting upstream database symlink to the prepared sandbox")
    args = parser.parse_args()
    output = args.output.resolve()
    manifest = prepare(args.human_csv.resolve(), args.sandbox_zip.resolve(),
                       args.sandbox_checksums.resolve(), args.upstream.resolve(), output,
                       link_sandbox=args.link_sandbox)
    print(json.dumps({"output": str(output), "query_count": manifest["query_count"],
                      "sandbox_files": manifest["sandbox"]["file_count"],
                      "dev_uids": manifest["dev_uids"],
                      "manifest_sha256": sha(output / "dataset_manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
