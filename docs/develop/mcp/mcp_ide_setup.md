---
sidebar_position: 4
slug: /mcp_ide_setup
sidebar_custom_props: {
  categoryIcon: LucidePlugZap
}
---
# Connect an IDE assistant to RAGFlow

Wire Claude Code, Cursor, or OpenCode to your workspace KBs and agents in under 5 minutes.

---

This guide is for end-users who already have:

- A workspace on a running RAGFlow instance (self-hosted or Cyllene-managed)
- A user-level RAGFlow API key with the right RBAC scopes (see [Acquire a RAGFlow API key](../acquire_ragflow_api_key.md))
- An IDE assistant that supports MCP (Claude Code, Cursor, OpenCode, Continue, ...)

If your administrator has not yet enabled the MCP server on your RAGFlow deployment, point them at [Launch MCP server](./launch_mcp_server.md).

---

## What you get

Once connected, your IDE assistant can:

| Capability | MCP tools used |
|---|---|
| Search the workspace KBs by natural language | `ragflow_retrieval` |
| List datasets, browse documents, read full chunks | `list_datasets`, `list_documents`, `get_document_chunks` |
| Create datasets, upload notes/RFCs/specs, delete obsolete docs | `create_dataset`, `index_document`, `delete_documents` |
| List & invoke business agents (HR procedures, contract review, ...) | `list_agents`, `run_agent`, `continue_agent_session` |

See [MCP tools](./mcp_tools.md) for the full reference.

---

## Pick a transport

The RAGFlow MCP server speaks two transports out of the box:

- **Streamable HTTP** (`/mcp`) — recommended. One persistent HTTP connection, JSON request/response, easier to proxy through corporate firewalls and TLS terminators.
- **Legacy SSE** (`/sse`) — kept for backwards compatibility with older MCP clients.

Most modern IDE assistants support either. Prefer `/mcp` unless your client only knows `sse`.

---

## Connection parameters

You will reuse the same three values across every IDE:

| Parameter | Where to find it |
|---|---|
| MCP server URL | Provided by your administrator. Examples: `https://ragflow.cyllene.com/mcp` (streamable) or `https://ragflow.cyllene.com/sse` (legacy). |
| API key | Generated from the RAGFlow UI → **Profile → API keys**. Starts with `ragflow-`. |
| Header name | `api_key` (RAGFlow native) or `Authorization` (OAuth-style — value passed verbatim, no `Bearer` prefix). |

The MCP server runs in **host mode** for multi-tenant deployments, which means the API key is required in every connection header. The token automatically scopes calls to the workspace it was issued for.

---

## Claude Code

### Option 1 — CLI (recommended)

```bash
# Streamable HTTP
claude mcp add ragflow \
  --transport http \
  --url https://ragflow.cyllene.com/mcp \
  --header "api_key: ragflow-XXXXXXXXXXXX"

# Legacy SSE
claude mcp add ragflow \
  --transport sse \
  --url https://ragflow.cyllene.com/sse \
  --header "api_key: ragflow-XXXXXXXXXXXX"
```

Run `claude mcp list` to confirm the server is registered, then start a new session — the tools appear under the `ragflow` namespace.

### Option 2 — `.mcp.json`

Add a per-project file at the repository root:

```json
{
  "mcpServers": {
    "ragflow": {
      "type": "http",
      "url": "https://ragflow.cyllene.com/mcp",
      "headers": {
        "api_key": "ragflow-XXXXXXXXXXXX"
      }
    }
  }
}
```

Reload the workspace. Claude Code prompts once for permission, then exposes the tools.

---

## Cursor

Edit `~/.cursor/mcp.json` (or `.cursor/mcp.json` at the repository root for a per-project config):

```json
{
  "mcpServers": {
    "ragflow": {
      "url": "https://ragflow.cyllene.com/mcp",
      "headers": {
        "api_key": "ragflow-XXXXXXXXXXXX"
      }
    }
  }
}
```

Restart Cursor. The tools show up in the MCP panel of Composer / Chat.

For SSE transport, replace the URL with the `/sse` endpoint — Cursor auto-detects the transport from the path.

---

## OpenCode

Add a remote MCP block to `opencode.json` (project root) or `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "ragflow": {
      "type": "remote",
      "url": "https://ragflow.cyllene.com/mcp",
      "headers": {
        "api_key": "ragflow-XXXXXXXXXXXX"
      },
      "enabled": true
    }
  }
}
```

Restart the OpenCode TUI. Tools become available with the `ragflow_` prefix.

---

## Continue.dev

In `~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: ragflow
    url: https://ragflow.cyllene.com/mcp
    headers:
      api_key: ragflow-XXXXXXXXXXXX
```

---

## Sanity check

From any of the IDEs above, ask the assistant:

> List the datasets I have access to.

The assistant should call `list_datasets` and return your KBs. If you instead see a permission error, the API key is missing the `dataset.read` scope — ask your administrator to widen the role bound to the key.

For a deeper smoke test, run:

> Find the latest internal RFC about authentication and summarise the open questions.

The assistant should chain `list_datasets` → `ragflow_retrieval` (or `list_documents` + `get_document_chunks`) and produce a citation-grounded answer.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Missing or invalid authorization header` | Header name not recognised by the proxy in front of RAGFlow | Try `Authorization` instead of `api_key`. Value stays the raw token (no `Bearer ` prefix). |
| `403 Permission denied: dataset.read` | API key bound to a role that lacks the scope | Administrator must add the scope, or rotate the key with the right role. |
| Tools list contains only `ragflow_retrieval` | You're hitting an upstream RAGFlow MCP server, not the Cyllene fork | The 9 extra tools are fork-specific. Confirm the server URL points at the Cyllene deployment. |
| `Permission denied: agent.run` when calling `run_agent` | Role lacks `agent.run` | Add the scope, or use `ragflow_retrieval` instead if you only need search. |
| Streaming tool call hangs > 5 min | Long-running agent canvas | Pass `max_seconds` explicitly on `run_agent` (max 1800). |
| `Permission denied: dataset.write` on `create_dataset` | Most users only have `dataset.read` | Write tools (V2) are typically restricted to admins/builders. Ask your administrator. |

---

## Security notes

- Every API key is workspace-scoped. Sharing a key gives access to that workspace's data only.
- All write tools (`create_dataset`, `index_document`, `delete_documents`) are RBAC-gated — `delete_documents` removes both the file rows **and** the indexed chunks (RGPD-compliant).
- API keys can be revoked at any time from the RAGFlow UI. Once revoked, ongoing MCP sessions fail at the next tool call.
- For local development against a self-host MCP server, bind the server to `127.0.0.1` rather than `0.0.0.0` (see [Launch MCP server § Security considerations](./launch_mcp_server.md#security-considerations)).
