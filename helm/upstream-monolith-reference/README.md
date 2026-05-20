# RAGFlow Helm Chart

A Helm chart to deploy RAGFlow and its dependencies on Kubernetes.

- Components: RAGFlow (web/api) and optional dependencies (Infinity/Elasticsearch/OpenSearch, MySQL, MinIO, Redis)
- Requirements: Kubernetes >= 1.24, Helm >= 3.10

## Install

```bash
helm upgrade --install ragflow ./ \
  --namespace ragflow --create-namespace
```

Uninstall:
```bash
helm uninstall ragflow -n ragflow
```

## Global Settings

- `global.repo`: Prepend a global image registry prefix for all images.
  - Behavior: Replaces the registry part and keeps the image path (e.g., `quay.io/minio/minio` -> `registry.example.com/myproj/minio/minio`).
  - Example: `global.repo: "registry.example.com/myproj"`
- `global.imagePullSecrets`: List of image pull secrets applied to all Pods.
  - Example:
    ```yaml
    global:
      imagePullSecrets:
        - name: regcred
    ```

## External Services (MySQL / MinIO / Redis)

The chart can deploy in-cluster services or connect to external ones. Toggle with `*.enabled`. When disabled, provide host/port via `env.*`.

- MySQL
  - `mysql.enabled`: default `true`
  - If `false`, set:
    - `env.MYSQL_HOST` (required), `env.MYSQL_PORT` (default `3306`)
    - `env.MYSQL_DBNAME` (default `rag_flow`), `env.MYSQL_PASSWORD` (required)
    - `env.MYSQL_USER` (default `root` if omitted)
- MinIO
  - `minio.enabled`: default `true`
  - Configure:
    - `env.MINIO_HOST` (optional external host), `env.MINIO_PORT` (default `9000`)
    - `env.MINIO_ROOT_USER` (default `rag_flow`), `env.MINIO_PASSWORD` (optional)
- Redis (Valkey)
  - `redis.enabled`: default `true`
  - If `false`, set:
    - `env.REDIS_HOST` (required), `env.REDIS_PORT` (default `6379`)
    - `env.REDIS_PASSWORD` (optional; empty disables auth if server allows)

Notes:
- When `*.enabled=true`, the chart renders in-cluster resources and injects corresponding `*_HOST`/`*_PORT` automatically.
- Sensitive variables like `MYSQL_PASSWORD` are required; `MINIO_PASSWORD` and `REDIS_PASSWORD` are optional. All secrets are stored in a Secret.

### Example: use external MySQL, MinIO, and Redis

```yaml
# values.override.yaml
mysql:
  enabled: false  # use external MySQL
minio:
  enabled: false  # use external MinIO (S3 compatible)
redis:
  enabled: false  # use external Redis/Valkey

env:
  # MySQL
  MYSQL_HOST: mydb.example.com
  MYSQL_PORT: "3306"
  MYSQL_USER: root
  MYSQL_DBNAME: rag_flow
  MYSQL_PASSWORD: "<your-mysql-password>"

  # MinIO
  MINIO_HOST: s3.example.com
  MINIO_PORT: "9000"
  MINIO_ROOT_USER: rag_flow
  MINIO_PASSWORD: "<your-minio-secret>"

  # Redis
  REDIS_HOST: redis.example.com
  REDIS_PORT: "6379"
  REDIS_PASSWORD: "<your-redis-pass>"
```

Apply:
```bash
helm upgrade --install ragflow ./helm -n ragflow -f values.override.yaml
```

## Document Engine Selection

Choose one of `infinity` (default), `elasticsearch`, or `opensearch` via `env.DOC_ENGINE`. The chart renders only the selected engine and sets the appropriate host variables.

```yaml
env:
  DOC_ENGINE: infinity   # or: elasticsearch | opensearch
  # For elasticsearch
  ELASTIC_PASSWORD: "<es-pass>"
  # For opensearch
  OPENSEARCH_PASSWORD: "<os-pass>"
```

## MCP Server

Expose the RAGFlow MCP server alongside the main RAGFlow process. This is what
IDE assistants (Claude Code, Cursor, OpenCode, ...) connect to in order to
search workspace KBs, list documents, and run agent canvases. See
[docs/develop/mcp/](../docs/develop/mcp/) for the full guide.

```yaml
ragflow:
  mcp:
    enabled: true
    mode: host         # "host" for multi-tenant, "self-host" for single-tenant
    port: 9382
    baseUrl: http://127.0.0.1:9380
    transport:
      sse: true
      streamableHttp: true
      jsonResponse: true
    service:
      type: ClusterIP   # or LoadBalancer / NodePort if exposed directly
```

Mode summary:

- **`host`** — recommended for B2B SaaS. Each MCP client must include an
  `api_key` (or `Authorization`) header bound to the workspace it should access.
  The fork's RBAC layer scopes every tool call to that workspace.
- **`self-host`** — single-tenant. The MCP server itself authenticates with
  the RAGFlow REST API using a fixed token. All clients see one tenant.
  Provide the token via either:

  ```yaml
  ragflow:
    mcp:
      mode: self-host
      hostApiKey: ragflow-XXXXXXXXXXXX   # inline (dev/test only)
  ```

  …or, for production, reference an existing Secret containing the key
  `host-api-key`:

  ```yaml
  ragflow:
    mcp:
      mode: self-host
      existingSecret: ragflow-mcp-host-key
  ```

To expose the MCP server through Ingress with TLS:

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: ragflow-mcp.example.com
      paths:
        - path: /sse
          pathType: Prefix
          backend:
            service:
              name: ragflow-mcp
              port: { name: mcp }
        - path: /mcp
          pathType: Prefix
          backend:
            service:
              name: ragflow-mcp
              port: { name: mcp }
        - path: /messages
          pathType: Prefix
          backend:
            service:
              name: ragflow-mcp
              port: { name: mcp }
```

> The legacy SSE transport requires `/messages/` for tool POSTs (see
> [docs/develop/mcp/launch_mcp_server.md](../docs/develop/mcp/launch_mcp_server.md)).
> If you only want the streamable-HTTP transport, set
> `ragflow.mcp.transport.sse: false` and route only `/mcp`.

## Ingress

Expose the web UI via Ingress:

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: ragflow.example.com
      paths:
        - path: /
          pathType: Prefix
```

## Validate the Chart

```bash
helm lint ./helm
helm template ragflow ./helm > rendered.yaml
```

## Notes

- By default, the chart uses `DOC_ENGINE: infinity` and deploys in-cluster MySQL, MinIO, and Redis.
- The chart injects derived `*_HOST`/`*_PORT` and required secrets into a single Secret (`<release>-ragflow-env-config`).
- `global.repo` and `global.imagePullSecrets` apply to all Pods; per-component `*.image.pullSecrets` still work and are merged with global settings.
