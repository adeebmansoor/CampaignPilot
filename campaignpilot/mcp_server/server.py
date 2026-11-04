"""CampaignPilot MCP server.

Exposes the marketing database and knowledge base as Model Context Protocol
tools and resources, so ANY MCP client — the LangGraph agents here, Claude
Desktop, or another agent framework — gets the same consistent context.

Run (stdio, used by the agents):   python -m campaignpilot.mcp_server.server
Run (HTTP, for remote clients):    python -m campaignpilot.mcp_server.server --http --port 8765
"""
import argparse
import functools
import logging
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.exc import SQLAlchemyError

from campaignpilot.db.session import init_db
from campaignpilot.tools import marketing_tools as t

# stdio transport uses stdout for protocol messages -> logs must go to stderr
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

mcp = MCPServer(
    name="campaignpilot",
    instructions=(
        "Marketing data tools for the Brightloop demo company. Call get_database_schema "
        "before writing SQL. run_segment_query only accepts a single read-only SELECT."
    ),
)


def expected_errors(fn):
    """Turn errors the agent can fix (unsafe SQL, bad column names) into MCP
    ToolErrors so their message reaches the model and it can self-correct.
    Any other exception stays a generic error, so internals aren't leaked."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except t.UnsafeQueryError as e:
            raise ToolError(f"Rejected query: {e}") from e
        except SQLAlchemyError as e:
            raise ToolError(f"SQL error: {str(e.orig if hasattr(e, 'orig') else e)[:300]}") from e

    return wrapper


@mcp.tool()
def get_database_schema() -> str:
    """Describe the marketing database tables, columns and allowed values. Call this before writing SQL."""
    return t.get_database_schema()


@mcp.tool()
@expected_errors
def run_segment_query(sql: str, sample_size: int = 5) -> dict:
    """Run a read-only SELECT over the customers table and return the segment size plus a sample of rows."""
    return t.run_segment_query(sql, sample_size)


@mcp.tool()
def get_campaign_performance(channel: str = "", top: int = 5) -> list[dict]:
    """Past campaigns with open rate, CTR, conversion rate, revenue and ROAS. channel: email, social, ads or empty for all."""
    return t.get_campaign_performance(channel or None, top)


@mcp.tool()
def get_channel_benchmarks() -> list[dict]:
    """Average open rate, CTR, conversion rate and ROAS for each channel."""
    return t.get_channel_benchmarks()


@mcp.tool()
def search_marketing_knowledge(query: str, k: int = 4) -> list[dict]:
    """Semantic search (RAG) over brand guidelines, product and pricing docs, and past campaign learnings."""
    return t.search_marketing_knowledge(query, k)


@mcp.resource("campaignpilot://schema")
def schema_resource() -> str:
    """The database schema as a readable resource."""
    return t.get_database_schema()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    init_db()
    if args.http:
        mcp.run("streamable-http", host="0.0.0.0", port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
