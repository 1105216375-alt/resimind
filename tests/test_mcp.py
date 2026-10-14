"""Real optional SDK v2 transport checks, including a separate stdio process."""
import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="optional MCP SDK not installed")


def test_real_stdio_discovery_verification_learning_and_recall():
    import anyio
    from mcp import Client, StdioServerParameters

    async def exercise():
        process = StdioServerParameters(command=sys.executable,
            args=["-m", "resimind.integrations.mcp"],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        with anyio.fail_after(30):
            async with Client(process) as client:
                tools = {t.name: t for t in (await client.list_tools()).tools}
                assert len(tools) == 5
                assert tools["learn_polynomial"].annotations.read_only_hint is False
                assert tools["verify_identity"].annotations.read_only_hint is True
                assert tools["verify_identity"].input_schema["properties"]["lhs"]["maxLength"] == 8192
                for rhs, status in [("x**2+2*x+1", "verified"), ("x**2+1", "rejected"), ("1/x", "unresolved")]:
                    result = await client.call_tool("verify_identity", {"lhs": "(x+1)**2", "rhs": rhs, "variables": ["x"]})
                    assert not result.is_error and result.structured_content["status"] == status
                result = await client.call_tool("expand_polynomial", {"expression": "(x+1)**4", "variables": ["x"]})
                assert len(result.structured_content["certificate"]["terms"]) == 5
                for expression, variables in [("(a+b)**3", ["a", "b"]), ("(u+v)**3", ["u", "v"])]:
                    result = await client.call_tool("learn_polynomial", {"expression": expression, "variables": variables})
                    assert result.structured_content["status"] == "verified"
                assert result.structured_content["learning"]["new_rule_ids"]
                result = await client.call_tool("recall_polynomial", {"expression": "(2*x+3*y)**3", "variables": ["x", "y"]})
                assert result.structured_content["status"] == "verified"
                assert result.structured_content["route"] == "recall"
                result = await client.call_tool("list_rules", {})
                assert len(result.structured_content["rules"]) == 1
                bad = await client.call_tool("expand_polynomial", {"expression": "x"})
                assert bad.is_error
                bad = await client.call_tool("expand_polynomial", {"expression": "x" * 8193, "variables": ["x"]})
                assert bad.is_error
    anyio.run(exercise)


def test_fresh_server_has_no_shared_memory():
    import anyio
    from mcp import Client
    from resimind.integrations.mcp import create_server

    async def exercise():
        async with Client(create_server()) as client:
            result = await client.call_tool("list_rules", {})
            assert result.structured_content["rules"] == []
    anyio.run(exercise)
