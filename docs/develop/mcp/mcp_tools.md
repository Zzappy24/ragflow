---
sidebar_position: 2
slug: /mcp_tools
sidebar_custom_props: {
  categoryIcon: LucideToolCase
}
---
# RAGFlow MCP tools

Reference of every tool exposed by the RAGFlow MCP server.

---

The Cyllene fork extends the upstream `ragflow_retrieval` tool with three families of capabilities:

- **Exploration (V1)** — discover datasets, list documents, fetch full document content
- **Write (V2)** — create datasets, upload documents, delete documents (RGPD-compliant)
- **Agents-as-tools (V4)** — list canvases, run an agent synchronously, continue a session

All tools require the same authentication as `ragflow_retrieval`. See [Launch MCP server](./launch_mcp_server.md) for `self-host` vs `host` mode and how API keys are passed.

When the MCP server runs in **host mode**, every call is scoped to the workspace bound to the API key. The fork enforces the same RBAC as the REST API, so a tool will return a permission error if the caller's role is missing the matching permission (`dataset.read`, `dataset.write`, `document.read`, `document.write`, `agent.read`, `agent.run`).

---

## Catalog

| Tool | Family | Required permission | What it does |
|---|---|---|---|
| `ragflow_retrieval` | Retrieval | `dataset.read` | Hybrid search across one or more datasets and returns ranked chunks |
| `list_datasets` | Exploration | `dataset.read` | Paginated list of datasets visible to the caller |
| `list_documents` | Exploration | `document.read` | Paginated list of documents inside a dataset |
| `get_document_chunks` | Exploration | `document.read` | Full chunk content of a document, in natural order |
| `create_dataset` | Write | `dataset.write` | Create a new dataset (knowledge base) in the workspace |
| `index_document` | Write | `document.write` | Upload a document and (optionally) trigger parsing/embedding |
| `delete_documents` | Write | `document.write` | Delete documents and their indexed chunks |
| `list_agents` | Agents | `agent.read` | List agent canvases (workflows) accessible in the workspace |
| `run_agent` | Agents | `agent.run` | Invoke an agent canvas synchronously and return the final output |
| `continue_agent_session` | Agents | `agent.run` | Send a follow-up message in an existing agent session |

---

## Retrieval

### `ragflow_retrieval`

Hybrid (vector + term) retrieval. When `dataset_ids` is omitted or empty, the search ranges across every dataset visible to the caller.

| Field | Type | Required | Description |
|---|---|---|---|
| `question` | string | yes | Query text. |
| `dataset_ids` | string[] | no | Restrict to specific datasets. |
| `document_ids` | string[] | no | Restrict to specific documents. |
| `page` | integer | no | Page (default 1). |
| `page_size` | integer | no | Page size, max 100 (default 10). |
| `similarity_threshold` | number | no | Minimum similarity (default 0.2). |
| `vector_similarity_weight` | number | no | Vector vs term weight (default 0.3). |
| `keyword` | boolean | no | Enable keyword-based scoring (default false). |
| `top_k` | integer | no | Pool size before ranking (default 1024). |
| `rerank_id` | string | no | Reranking model id. |
| `force_refresh` | boolean | no | Bypass dataset metadata cache (default false). |

GraphRAG note: when a dataset has `use_kg=true`, retrieval automatically benefits from the knowledge-graph layer — no MCP-level toggle is needed.

---

## Exploration

### `list_datasets`

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | no | Case-insensitive name filter. |
| `page` | integer | no | Default 1. |
| `page_size` | integer | no | Default 100, max 1000. |

Returns `id`, `name`, `description`, `document_count`, `chunk_count`, `language`, `embedding_model`, `create_date`, `update_date`.

### `list_documents`

| Field | Type | Required | Description |
|---|---|---|---|
| `dataset_id` | string | yes | Dataset to inspect. |
| `keywords` | string | no | Keyword filter on document name. |
| `page` | integer | no | Default 1. |
| `page_size` | integer | no | Default 30, max 100. |

Returns `id`, `name`, `type`, `size`, `chunk_count`, `run` (parse status), `progress`, plus metadata fields.

### `get_document_chunks`

Returns the **full** chunk content of a document, in natural reading order. Useful when an LLM agent needs the entire RFC / ADR / procedure rather than only the top-N relevant chunks.

| Field | Type | Required | Description |
|---|---|---|---|
| `document_id` | string | yes | Document to fetch. |
| `max_chunks` | integer | no | Cap (default 100, max 500). |

Internally uses keyword retrieval with the document name as the query, since the upstream `/retrieval` endpoint refuses an empty question.

---

## Write

### `create_dataset`

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Must be unique inside the workspace. |
| `description` | string | no | Free text. |
| `embedding_model` | string | no | Defaults to the workspace default. |
| `chunk_method` | string | no | `naive` (default) / `book` / `qa` / `manual` / `paper` / `one` / etc. |
| `permission` | string | no | `me` (private, default) or `team` (workspace-wide). |

### `index_document`

Uploads UTF-8 content as a file. The extension drives RAGFlow's parser selection — pass `.md`, `.txt`, `.pdf`, `.docx`, `.json` etc. depending on the source.

| Field | Type | Required | Description |
|---|---|---|---|
| `dataset_id` | string | yes | Target dataset. |
| `content` | string | yes | UTF-8 text content. |
| `filename` | string | yes | Filename including extension. |
| `auto_parse` | boolean | no | Trigger parse + embed after upload (default true). |

Returns the new `document_id`. Poll `list_documents` to watch parse progress.

### `delete_documents`

Deletes the file rows **and** the indexed chunks in Infinity/Elasticsearch — RGPD-compliant.

| Field | Type | Required | Description |
|---|---|---|---|
| `dataset_id` | string | yes | Source dataset. |
| `document_ids` | string[] | yes | At least one id. |

---

## Agents

### `list_agents`

| Field | Type | Required | Description |
|---|---|---|---|
| `keywords` | string | no | Title keyword filter. |
| `canvas_category` | string | no | Filter by category (`Agent`, `DataFlow`, ...). |
| `page` | integer | no | Default 1. |
| `page_size` | integer | no | Default 30, max 100. |

Returns `id`, `title`, `description`, `canvas_category`, `update_time`.

### `run_agent`

Invokes an agent canvas synchronously and blocks until the workflow completes (or `max_seconds` elapses). Behind the scenes the server bootstraps the canvas replica via `GET /agents/<id>` (custom B2B SaaS bootstrap pattern) before posting to `/agents/chat/completion`, then aggregates the SSE event stream.

| Field | Type | Required | Description |
|---|---|---|---|
| `agent_id` | string | yes | Canvas id from `list_agents`. |
| `query` | string | yes | Natural-language instruction. |
| `inputs` | object | no | Structured inputs for canvas Begin form fields. |
| `session_id` | string | no | Continue an existing session. |
| `max_seconds` | integer | no | Hard timeout (default 300, max 1800). |

Returns:
- `content` — final agent text
- `session_id` — for follow-up calls
- `downloads` — generated files (file ids in the workspace)
- `events_seen` — debug log of SSE events

### `continue_agent_session`

Sends another message in an existing session. Same return shape as `run_agent`.

| Field | Type | Required | Description |
|---|---|---|---|
| `agent_id` | string | yes | Canvas id. |
| `session_id` | string | yes | Existing session id. |
| `message` | string | yes | Next user message. |
| `max_seconds` | integer | no | Hard timeout (default 300, max 1800). |

---

For the Python and curl examples that wire these tools end-to-end, see [MCP client examples](./mcp_client_example.md). For the user-facing IDE setup (Claude Code, Cursor, OpenCode), see [Connect an IDE assistant](./mcp_ide_setup.md).
