"""
Stats/analytics endpoints for the management panel.

Two scopes:
  - GET /stats/overview       — superuser: global view across all orgs
  - GET /orgs/{id}/usage      — org_admin: scoped to one org's workspaces
"""
from datetime import date, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import get_current_user_id, require_superuser

router = APIRouter()

# ─── helpers ──────────────────────────────────────────────────────────────────

MODEL_TYPE_LABELS = {
    "chat": "Chat LLM",
    "embedding": "Embedding",
    "rerank": "Rerank",
    "image2text": "Image→Text",
    "tts": "TTS",
    "speech2text": "Transcription",
    "asr": "Transcription",
    "ocr": "OCR",
}

def _date_range(days: int = 30) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def _load_workspace_map() -> dict:
    """Returns {tenant_id: {id, name, org_id}} for all active workspaces."""
    from api.db.db_models import DB, Workspace as WsModel
    result = {}
    with DB.connection_context():
        for ws in WsModel.select().where(WsModel.status == "1"):
            result[ws.tenant_id] = {
                "id": ws.id,
                "name": ws.name,
                "org_id": ws.org_id,
            }
    return result


def _indexed_tokens_by_tenant(tenant_ids: list[str] | None = None) -> dict[str, int]:
    """Sum knowledgebase.token_num per tenant_id."""
    from api.db.db_models import DB, Knowledgebase
    from peewee import fn
    result: dict[str, int] = defaultdict(int)
    with DB.connection_context():
        q = (Knowledgebase
             .select(Knowledgebase.tenant_id, fn.SUM(Knowledgebase.token_num).alias("total"))
             .where(Knowledgebase.status == "1")
             .group_by(Knowledgebase.tenant_id))
        if tenant_ids is not None:
            q = q.where(Knowledgebase.tenant_id.in_(tenant_ids))
        for row in q.tuples():
            result[row[0]] = row[1] or 0
    return result


def _load_org_map() -> dict:
    """Returns {org_id: org_name} for all active orgs."""
    from api.db.db_models import DB, Organisation
    result = {}
    with DB.connection_context():
        for org in Organisation.select().where(Organisation.status == "1"):
            result[org.id] = org.name
    return result


def _token_rows(start_date: str, tenant_ids: list[str] | None = None) -> list:
    """Fetch TokenUsageDaily rows from start_date."""
    from api.db.db_models import DB, TokenUsageDaily
    with DB.connection_context():
        q = TokenUsageDaily.select().where(TokenUsageDaily.date >= start_date)
        if tenant_ids is not None:
            q = q.where(TokenUsageDaily.tenant_id.in_(tenant_ids))
        return list(q.dicts())


def _aggregate(rows: list, ws_map: dict, org_map: dict, days: int = 30):
    """Aggregate raw TokenUsageDaily rows into shapes the frontend needs."""
    date_labels = _date_range(days)

    # Core accumulators
    daily: dict[str, int] = defaultdict(int)
    # (date, factory) → {model_type → tokens}  — real data, no approximation
    daily_by_factory_type: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_org: dict[str, int] = defaultdict(int)
    by_ws: dict[str, dict] = {}
    by_model: dict[tuple, dict] = {}   # (llm_name, model_type) → {model, type, factory, tokens}
    by_model_type: dict[str, int] = defaultdict(int)
    by_factory: dict[str, int] = defaultdict(int)
    daily_by_ws: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_factories: set[str] = set()

    for row in rows:
        tokens = row["tokens"] or 0
        if tokens == 0:
            continue
        tid = row["tenant_id"]
        d = row["date"]
        model_type = (row["model_type"] or "unknown").lower()
        llm_name = row["llm_name"] or ""
        llm_factory = row["llm_factory"] or ""
        model_display = llm_name or llm_factory or "unknown"

        daily[d] += tokens
        daily_by_factory_type[(d, llm_factory)][model_type] += tokens
        by_model_type[model_type] += tokens
        by_factory[llm_factory] += tokens
        all_factories.add(llm_factory)

        mk = (model_display, model_type)
        if mk not in by_model:
            by_model[mk] = {
                "model": model_display,
                "type": model_type,
                "type_label": MODEL_TYPE_LABELS.get(model_type, model_type),
                "factory": llm_factory,
                "tokens": 0,
            }
        by_model[mk]["tokens"] += tokens

        ws_info = ws_map.get(tid)
        if ws_info:
            org_id = ws_info["org_id"]
            by_org[org_id] += tokens

            ws_id = ws_info["id"]
            if ws_id not in by_ws:
                by_ws[ws_id] = {
                    "workspace_id": ws_id,
                    "workspace_name": ws_info["name"],
                    "tokens": 0,
                }
            by_ws[ws_id]["tokens"] += tokens
            daily_by_ws[d][ws_id] += tokens

    # All unique model types and factories present in the data
    all_types = sorted(by_model_type.keys())
    all_factory_list = sorted(all_factories)

    # daily_by_model_type_per_factory: {factory → [{date, type1: n, type2: n, ...}]}
    # "__all__" key is the unfiltered aggregate (all factories combined)
    def _build_daily_series(factory_filter: str | None) -> list[dict]:
        series = []
        for d in date_labels:
            entry: dict = {"date": d}
            for mt in all_types:
                if factory_filter is None:
                    # sum across all factories
                    entry[mt] = sum(
                        daily_by_factory_type[(d, f)].get(mt, 0) for f in all_factories
                    )
                else:
                    entry[mt] = daily_by_factory_type[(d, factory_filter)].get(mt, 0)
            series.append(entry)
        return series

    daily_by_model_type_per_factory: dict[str, list] = {"__all__": _build_daily_series(None)}
    for factory in all_factory_list:
        daily_by_model_type_per_factory[factory] = _build_daily_series(factory)

    # Daily stacked by workspace: [{date, ws_id: n, ...}]
    ws_id_list = list(by_ws.keys())
    ws_id_to_name: dict[str, str] = {ws_id: by_ws[ws_id]["workspace_name"] for ws_id in ws_id_list}
    daily_by_workspace = []
    for d in date_labels:
        entry = {"date": d}
        for ws_id in ws_id_list:
            entry[ws_id] = daily_by_ws[d].get(ws_id, 0)
        daily_by_workspace.append(entry)

    return {
        # Simple daily series
        "daily_tokens": [{"date": d, "tokens": daily.get(d, 0)} for d in date_labels],
        # Stacked series — keyed by factory ("__all__" = no filter)
        "daily_by_model_type_per_factory": daily_by_model_type_per_factory,
        "daily_by_workspace": daily_by_workspace,
        # Dimensions
        "model_types": all_types,
        "factories": all_factory_list,
        "workspace_ids": ws_id_list,
        "workspace_id_to_name": ws_id_to_name,
        # Aggregates
        "by_org": sorted(
            [{"org_id": oid, "org_name": org_map.get(oid, oid), "tokens": t} for oid, t in by_org.items()],
            key=lambda x: x["tokens"], reverse=True,
        ),
        "by_workspace": sorted(by_ws.values(), key=lambda x: x["tokens"], reverse=True),
        "by_model": sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True),
        "by_model_type": sorted(
            [{"type": t, "type_label": MODEL_TYPE_LABELS.get(t, t), "tokens": v}
             for t, v in by_model_type.items()],
            key=lambda x: x["tokens"], reverse=True,
        ),
        "by_factory": sorted(
            [{"factory": f, "tokens": t} for f, t in by_factory.items()],
            key=lambda x: x["tokens"], reverse=True,
        ),
        "total_tokens_30d": sum(daily.values()),
    }


# ─── global overview (superuser) ──────────────────────────────────────────────

@router.get("/stats/overview")
def global_overview(_user=Depends(require_superuser)):
    """Global stats: token usage + entity counts. Superuser only."""
    from api.db.db_models import DB, Organisation, Workspace as WsModel, User

    start_date = (date.today() - timedelta(days=29)).isoformat()

    ws_map = _load_workspace_map()
    org_map = _load_org_map()
    rows = _token_rows(start_date)
    agg = _aggregate(rows, ws_map, org_map)

    # Entity counts
    with DB.connection_context():
        total_orgs = Organisation.select().where(Organisation.status == "1").count()
        total_workspaces = (
            WsModel.select()
            .where(WsModel.status == "1")
            .where(~WsModel.name.startswith("ws-"))
            .count()
        )
        total_users = (
            User.select()
            .where(User.status == "1")
            .where(~User.email.endswith("@internal"))
            .count()
        )

    # Per-org: workspace + user counts
    org_ws_count: dict[str, int] = defaultdict(int)
    for ws_info in ws_map.values():
        org_ws_count[ws_info["org_id"]] += 1

    from api.db.db_models import OrgMember
    org_user_count: dict[str, int] = defaultdict(int)
    with DB.connection_context():
        for m in OrgMember.select().where(OrgMember.status == "1"):
            org_user_count[m.org_id] += 1

    # Indexed tokens per org
    idx_by_tenant = _indexed_tokens_by_tenant()
    idx_by_org: dict[str, int] = defaultdict(int)
    total_indexed = 0
    for tenant_id, ws_info in ws_map.items():
        t = idx_by_tenant.get(tenant_id, 0)
        idx_by_org[ws_info["org_id"]] += t
        total_indexed += t

    for item in agg["by_org"]:
        item["workspaces"] = org_ws_count.get(item["org_id"], 0)
        item["users"] = org_user_count.get(item["org_id"], 0)
        item["indexed_tokens"] = idx_by_org.get(item["org_id"], 0)

    # Fill orgs with zero token usage
    orgs_with_tokens = {x["org_id"] for x in agg["by_org"]}
    for org_id, org_name in org_map.items():
        if org_id not in orgs_with_tokens:
            agg["by_org"].append({
                "org_id": org_id,
                "org_name": org_name,
                "tokens": 0,
                "indexed_tokens": idx_by_org.get(org_id, 0),
                "workspaces": org_ws_count.get(org_id, 0),
                "users": org_user_count.get(org_id, 0),
            })

    return {
        "totals": {
            "orgs": total_orgs,
            "workspaces": total_workspaces,
            "users": total_users,
            "tokens_30d": agg["total_tokens_30d"],
            "indexed_tokens": total_indexed,
        },
        "daily_tokens": agg["daily_tokens"],
        "daily_by_model_type_per_factory": agg["daily_by_model_type_per_factory"],
        "factories": agg["factories"],
        "model_types": agg["model_types"],
        "by_org": agg["by_org"],
        "by_model": agg["by_model"],
        "by_model_type": agg["by_model_type"],
        "by_factory": agg["by_factory"],
    }


# ─── org-scoped usage (org_admin) ─────────────────────────────────────────────

@router.get("/orgs/{org_id}/usage")
def org_usage(org_id: str, user_id: str = Depends(get_current_user_id)):
    """Token usage + entity counts for one org. Accessible to org_admin and superuser."""
    from management.server.auth.dependencies import _load_user
    from api.db.services.org_service import OrgMemberService, OrgService
    from api.db.db_models import DB, Workspace as WsModel, OrgMember
    from api.db.services.workspace_service import WsMemberService

    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not user.is_superuser:
        membership = OrgMemberService.get_membership(org_id, user_id)
        if not membership or membership.role != "org_admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Org admin access required")

    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org or org.status != "1":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")

    start_date = (date.today() - timedelta(days=29)).isoformat()

    ws_map: dict = {}
    with DB.connection_context():
        for ws in WsModel.select().where(WsModel.org_id == org_id, WsModel.status == "1"):
            ws_map[ws.tenant_id] = {"id": ws.id, "name": ws.name, "org_id": ws.org_id}

    tenant_ids = list(ws_map.keys())
    rows = _token_rows(start_date, tenant_ids=tenant_ids) if tenant_ids else []

    org_map = {org_id: org.name}
    agg = _aggregate(rows, ws_map, org_map)

    # Per-workspace member counts + indexed tokens
    ws_user_count: dict[str, int] = {}
    for ws_info in ws_map.values():
        ws_user_count[ws_info["id"]] = len(WsMemberService.list_by_workspace(ws_info["id"]))

    idx_by_tenant = _indexed_tokens_by_tenant(tenant_ids=tenant_ids if tenant_ids else None)
    idx_by_ws: dict[str, int] = {}
    total_indexed = 0
    for tenant_id, ws_info in ws_map.items():
        t = idx_by_tenant.get(tenant_id, 0)
        idx_by_ws[ws_info["id"]] = t
        total_indexed += t

    for item in agg["by_workspace"]:
        item["users"] = ws_user_count.get(item["workspace_id"], 0)
        item["indexed_tokens"] = idx_by_ws.get(item["workspace_id"], 0)

    # Fill workspaces with zero tokens
    ws_with_tokens = {x["workspace_id"] for x in agg["by_workspace"]}
    for ws_info in ws_map.values():
        if ws_info["id"] not in ws_with_tokens:
            agg["by_workspace"].append({
                "workspace_id": ws_info["id"],
                "workspace_name": ws_info["name"],
                "tokens": 0,
                "indexed_tokens": idx_by_ws.get(ws_info["id"], 0),
                "users": ws_user_count.get(ws_info["id"], 0),
            })

    with DB.connection_context():
        total_workspaces = len(ws_map)
        total_users = OrgMember.select().where(
            OrgMember.org_id == org_id, OrgMember.status == "1"
        ).count()

    return {
        "totals": {
            "workspaces": total_workspaces,
            "users": total_users,
            "tokens_30d": agg["total_tokens_30d"],
            "indexed_tokens": total_indexed,
        },
        "daily_tokens": agg["daily_tokens"],
        "daily_by_model_type_per_factory": agg["daily_by_model_type_per_factory"],
        "factories": agg["factories"],
        "daily_by_workspace": agg["daily_by_workspace"],
        "model_types": agg["model_types"],
        "workspace_ids": agg["workspace_ids"],
        "workspace_id_to_name": agg["workspace_id_to_name"],
        "by_workspace": agg["by_workspace"],
        "by_model": agg["by_model"],
        "by_model_type": agg["by_model_type"],
        "by_factory": agg["by_factory"],
    }
