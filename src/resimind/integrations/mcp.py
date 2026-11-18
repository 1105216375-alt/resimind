"""Optional MCP SDK v2 stdio adapter. No dependency import in core paths."""
from typing import Annotated, Any
from contextlib import asynccontextmanager

from ..tool_api import DESCRIPTIONS, ToolSession


def create_server(memory_dir=None):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    from pydantic import Field
    from .. import __version__

    session = ToolSession(memory_dir)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            session.close()

    memory_note = ("Learning explicitly saves verified rule memory in the configured directory. "
        "Reloaded rules and examples are revalidated. " if memory_dir is not None else
        "Learning explicitly changes process-local memory, lost on restart. ")
    Expression = Annotated[str, Field(min_length=1, max_length=8192, strict=True)]
    Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$", strict=True)]
    Variables = Annotated[list[Name], Field(max_length=16, strict=True)]
    server = MCPServer("ResiMind", version=__version__,
        website_url="https://github.com/1105216375-alt/resimind",
        lifespan=lifespan,
        instructions="Bounded exact rational polynomial tools. Only status=verified certifies the returned claim. "
        "unresolved is not false. " + memory_note +
        "These tools make no model calls and do not produce Lean proofs.")

    def register(name):
        return server.tool(name=name, description=DESCRIPTIONS[name], structured_output=True,
            annotations=ToolAnnotations(read_only_hint=name != "learn_polynomial",
                destructive_hint=False, idempotent_hint=name != "learn_polynomial", open_world_hint=False))

    @register("verify_identity")
    def verify_identity(lhs: Expression, rhs: Expression, variables: Variables) -> dict[str, Any]:
        return session.call("verify_identity", {"lhs": lhs, "rhs": rhs, "variables": variables})

    @register("expand_polynomial")
    def expand_polynomial(expression: Expression, variables: Variables) -> dict[str, Any]:
        return session.call("expand_polynomial", {"expression": expression, "variables": variables})

    @register("learn_polynomial")
    def learn_polynomial(expression: Expression, variables: Variables) -> dict[str, Any]:
        return session.call("learn_polynomial", {"expression": expression, "variables": variables})

    @register("recall_polynomial")
    def recall_polynomial(expression: Expression, variables: Variables) -> dict[str, Any]:
        return session.call("recall_polynomial", {"expression": expression, "variables": variables})

    @register("list_rules")
    def list_rules() -> dict[str, Any]:
        return session.call("list_rules", {})

    return server


def main(argv=None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="resimind-mcp")
    parser.add_argument("--memory-dir", help="persist verified rules and examples in this directory")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.memory_dir)
    except ImportError:
        print("MCP requires Python 3.11+ and the optional dependency: pip install '.[mcp]' from the checkout", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Cannot open tool memory: {exc}", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
