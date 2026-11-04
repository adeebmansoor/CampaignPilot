# Architecture

## Request flow

1. The user submits a goal (web app → `POST /campaigns`). The access code and hourly run cap are checked first.
2. `agents/service.py` creates a `thread_id` and runs the LangGraph graph in a background worker thread with a SQLite checkpointer. The API returns immediately with `status: running`; the web app polls `GET /campaigns/{id}` every ~1.2 s. Because the checkpointer saves state after every agent, each poll shows which agents have finished (`trace`) and which one is running (`next`). Add `?wait=true` to block instead (CLI, tests).
3. The graph runs **plan → segment → research → write → review**. If the review fails and revisions remain (`MAX_REVISIONS`, default 2), it loops back to **write** with the issues.
4. **human_approval** calls `interrupt()`. LangGraph saves the state and the run reports `status: awaiting_approval` with the draft.
5. The user decides (`POST /campaigns/{id}/decision`). The graph resumes from the checkpoint with `Command(resume=...)`:
   - `revise` → back to **write** with the feedback, then review and approval again
   - `approve` → **finalize** renders personalised messages for the sample customers and writes them to `data/outbox/`
   - `reject` (or anything unrecognised) → **finalize** records the rejection, nothing is written to the outbox
6. Finished campaigns are stored in the `generated_campaigns` table.

## State

`CampaignState` (in `agents/graph.py`) is a `TypedDict`. Each node returns only the keys it changes. `trace` uses an `operator.add` reducer so every agent appends a line describing what it did; the UI and CLI show this as the agent trace.

## Tools and MCP

```
agents/graph.py ──calls──► ToolClient.call(name, **args)
                                  │
              TOOLS_MODE=mcp ─────┼───── TOOLS_MODE=direct
                                  │
   MCPToolClient                  │        DirectToolClient
   (stdio subprocess,             │        (in-process)
    one long-lived session        │
    on a background event loop)   │
            │                     │
            ▼                     ▼
   mcp_server/server.py ──► tools/marketing_tools.py ──► PostgreSQL / Qdrant
```

`MCPToolClient` keeps one session open on a background asyncio loop because LangGraph nodes here are synchronous. Calls are serialised with a lock.

## SQL guard (`tools/marketing_tools.py`)

| Check | Why |
|---|---|
| Must start with `SELECT` or `WITH` | No writes or DDL |
| No `;` inside, no `--` or `/*` | Prevents stacked statements and comment tricks |
| Blocks `insert/update/delete/drop/alter/create/pragma/attach/...` | Defence in depth, including inside CTEs |
| Only `customers`, `campaigns`, `campaign_events`, `campaign_metrics` | No access to system tables |
| Must read from `customers` and return `id` | A segment is a set of customers |
| Runs in a transaction that is always rolled back; `READ ONLY` on Postgres | Even a missed case cannot persist changes |
| Relative dates via the bound `:today` parameter | No string-built dates from the LLM |

## Data model

```
customers ──< campaign_events >── campaigns ──1 campaign_metrics
generated_campaigns  (one row per finished agent run)
```

`campaign_metrics` is derived by the ETL from raw events:
`open_rate = opens / sent`, `ctr = clicks / sent`, `conversion_rate = conversions / clicks`, `roas = revenue / budget`.

## RAG

- Source: markdown files in `data/knowledge/`.
- Chunking: one chunk per `##` section (split further above ~900 chars), each prefixed with `Document > Section` so the heading's meaning is embedded too.
- IDs are deterministic hashes, and each index run rebuilds the collection, so removed content never lingers.
- Collections are suffixed with the embedder name (`marketing_knowledge_gemini` / `_hash`) because vectors from different models aren't comparable.
- The researcher runs three queries (brand + channel rules, the key message and audience, past learnings) and de-duplicates hits by section.

## Configuration

All settings live in `campaignpilot/config.py` and can be overridden with environment variables or `.env`. See `.env.example`.

## Deployment modes

| Mode | Database | Vector store | Processes |
|---|---|---|---|
| `start.bat` / `start.sh` (Docker Compose) | PostgreSQL container | Qdrant server container | `app` (API + web) and `pipeline` (hourly refresh) |
| Hugging Face Space / plain `docker run` | SQLite inside the container | Qdrant on-disk mode | `scripts/start-app.sh`: pipeline once, then the API |
| Local Python | SQLite | Qdrant on-disk mode | same as above |

In on-disk mode only one process may open the Qdrant folder at a time. That works because the pipeline finishes before the API starts, and in `TOOLS_MODE=mcp` only the MCP server subprocess opens Qdrant.
