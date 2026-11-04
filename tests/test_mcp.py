"""Talks to the real MCP server over stdio, exactly like the agents do."""
import pytest

from campaignpilot.tools.client import MCPToolClient, ToolError


@pytest.fixture(scope="module")
def client():
    c = MCPToolClient()
    yield c
    c.close()


def test_server_exposes_tools(client):
    assert set(client.list_tools()) == {
        "get_database_schema", "run_segment_query", "get_campaign_performance",
        "get_channel_benchmarks", "search_marketing_knowledge",
    }


def test_segment_query_over_mcp(client):
    r = client.call("run_segment_query", sql="SELECT * FROM customers WHERE country = 'UAE'", sample_size=2)
    assert r["count"] > 0 and len(r["sample"]) == 2


def test_rag_over_mcp(client):
    hits = client.call("search_marketing_knowledge", query="channel rules for email subject length", k=2)
    assert hits and "source" in hits[0]


def test_errors_reach_the_agent(client):
    with pytest.raises(ToolError, match="Rejected query"):
        client.call("run_segment_query", sql="DROP TABLE customers")
    with pytest.raises(ToolError, match="no such column"):
        client.call("run_segment_query", sql="SELECT * FROM customers WHERE plann = 'free'")
