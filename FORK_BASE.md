# Fork Base

This repository is a fork of [RAGFlow](https://github.com/infiniflow/ragflow) with multi-tenant RBAC extensions.

## Base Commit

- **Commit**: `60ec5880e`
- **Date**: 2026-04-04
- **Message**: "Feat: mysql data migrate script (#13927)"
- **Branch**: `main`
- **Git Tag**: `rbac/fork-point`

## Why This Commit

Since v0.24.0, 395+ commits landed including a major refactoring of SDK routes
(`api/apps/sdk/chat.py` → `api/apps/restful_apis/chat_api.py`). Starting from
v0.24.0 would force a full rebase on first upstream merge.

## Upstream Merge Strategy

- Merge upstream every ~3 months or on security alerts
- See `MODIFIED_FILES.md` for the list of files touched by the RBAC layer
- Conflicts should be minimal: most changes are import + decorator additions (2-5 lines per file)
