"""Optional MCP SDK v2 stdio adapter. No dependency import in core paths."""
from typing import Annotated, Any

from ..tool_api import DESCRIPTIONS, ToolSession


def create_server():
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    from pydantic import Field
    from .. import __version__

    session = ToolSession()
    Expression = Annotated[str, Field(min_length=1, max_length=8192, strict=True)]
    Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$", strict=True)]
    Variables = Annotated[list[Name], Field(max_length=16, strict=True)]
    server = MCPServer("ResiMind", version=__version__,
        website_url="https://github.com/1105216375-alt/resimind",
        instructions="Bounded exact rational polynomial tools. Only status=verified certifies the returned claim. "
        "unresolved is not false. Learning explicitly changes process-local memory, lost on restart. "
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


def main() -> int:
    import sys
    try:
        server = create_server()
    except ImportError:
        print("MCP requires Python 3.11+ and the optional dependency: pip install '.[mcp]' from the checkout", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
