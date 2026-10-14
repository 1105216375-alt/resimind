"""Exercise an extracted MCPB's actual manifest command via the official SDK."""
import argparse
import asyncio
import json
import os
from pathlib import Path


async def check(directory):
    from mcp import Client, StdioServerParameters
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    config = manifest["server"]["mcp_config"]
    params = StdioServerParameters(command=config["command"],
        args=[a.replace("${__dirname}", str(directory)) for a in config["args"]],
        env={k: os.environ[k] for k in ("PATH", "UV_CACHE_DIR", "UV_PYTHON", "UV_PYTHON_DOWNLOADS", "UV_NATIVE_TLS") if k in os.environ})
    async with Client(params) as client:
        assert len((await client.list_tools()).tools) == 5
        for rhs, expected in (("x**2+2*x+1", "verified"), ("x**2+1", "rejected")):
            result = await client.call_tool("verify_identity", {"lhs": "(x+1)**2", "rhs": rhs, "variables": ["x"]})
            assert result.structured_content["status"] == expected
        for expression, variables in (("(a+b)**3", ["a", "b"]), ("(u+v)**3", ["u", "v"])):
            result = await client.call_tool("learn_polynomial", {"expression": expression, "variables": variables})
            assert result.structured_content["status"] == "verified"
        assert result.structured_content["learning"]["new_rule_ids"]
        result = await client.call_tool("recall_polynomial", {"expression": "(2*x+3*y)**3", "variables": ["x", "y"]})
        assert result.structured_content["status"] == "verified"
        assert result.structured_content["search_actions"] == 1
    print("MCPB manifest command: discovery, verification, learning and recall passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(check(args.directory), timeout=180))
