import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="distribution builder requires Python 3.11+")


def test_bundle_is_deterministic_and_manifest_binds_artifact(tmp_path):
    path = Path(__file__).resolve().parents[1] / "tools" / "build_mcp_bundle.py"
    spec = importlib.util.spec_from_file_location("bundle_builder", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    one, metadata = builder.build(tmp_path / "one")
    two, _ = builder.build(tmp_path / "two")
    assert one.read_bytes() == two.read_bytes()
    assert metadata["packages"][0]["fileSha256"] == hashlib.sha256(one.read_bytes()).hexdigest()
    with zipfile.ZipFile(one) as archive:
        names = archive.namelist()
        assert "uv.lock" in names and "src/server.py" in names
        assert all(n in {"LICENSE", "pyproject.toml", "uv.lock", "manifest.json", "src/server.py"} or
                   (n.startswith("src/resimind/") and n.endswith(".py")) for n in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["server"]["type"] == "uv"
        assert manifest["version"] == metadata["version"]
        assert "--locked" in manifest["server"]["mcp_config"]["args"]
