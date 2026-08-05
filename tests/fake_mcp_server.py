"""Tiny stdio MCP server used as a test fixture.

Exposes one tool per response class the health check must distinguish:
a healthy verbose tool, an empty tool, a raising tool (FastMCP converts the
exception into an isError result), and an oversized tool that exceeds the
25k-token host output cap. No network, no keys.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")


@mcp.tool()
def healthy(query: str) -> str:
    """Returns a normal, verbose answer."""
    return f"answer to {query!r}: " + "useful content " * 10


@mcp.tool()
def empty() -> str:
    """Returns nothing of substance."""
    return ""


@mcp.tool()
def boom(query: str) -> str:
    """Always raises."""
    raise RuntimeError("kaput")


@mcp.tool()
def oversized(query: str) -> str:
    """Returns more than 25k tokens (~100k chars)."""
    return "x" * 120_000


@mcp.tool()
def delete_everything(path: str) -> str:
    """Mutating tool that a pre-flight must never invoke."""
    raise AssertionError("pre-flight called a mutating tool")


if __name__ == "__main__":
    mcp.run(transport="stdio")
