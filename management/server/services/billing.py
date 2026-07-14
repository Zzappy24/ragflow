"""Relevé de facturation mensuel par organisation — les DEUX produits.

Source unique pour la carte Facturation du panel (JSON) et le relevé CSV :
- Code : € réellement consommés DANS le mois (delta des snapshots quotidiens,
  mêmes règles que la courbe du dashboard : seed pré-fenêtre + delta négatif
  = reset de cycle -> valeur du jour), par team + total.
- RAG : tokens du mois par workspace (TokenUsageDaily), tag BU inclus.
"""
import datetime
import logging

logger = logging.getLogger(__name__)


def month_bounds(month: str) -> tuple[datetime.date, datetime.date]:
    first = datetime.date(int(month[:4]), int(month[5:7]), 1)
    nxt = (first + datetime.timedelta(days=32)).replace(day=1)
    return first, nxt


def _code_month_spend(org_id: str, first: datetime.date, nxt: datetime.date) -> dict:
    """Par team : € consommés dans [first, nxt) + tokens du mois."""
    from api.db.db_models import DB, CodeTeam, CodeSpendSnapshot
    with DB.connection_context():
        teams_by_id = {t.id: t for t in CodeTeam.select().where(CodeTeam.org_id == org_id)}
        # seed : dernier snapshot STRICTEMENT avant le mois, par team —
        # sans lui, le 1er snapshot du mois émettrait tout le cumul du cycle.
        prev_rows = list(CodeSpendSnapshot.select().where(
            (CodeSpendSnapshot.org_id == org_id) & (CodeSpendSnapshot.snap_date < first)
        ).order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))
        rows = list(CodeSpendSnapshot.select().where(
            (CodeSpendSnapshot.org_id == org_id)
            & (CodeSpendSnapshot.snap_date >= first) & (CodeSpendSnapshot.snap_date < nxt)
        ).order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))

    prev_by_team: dict[str, float] = {}
    for r in prev_rows:
        prev_by_team[r.code_team_id] = r.spend

    spend_by_team: dict[str, float] = {}
    tokens_by_team: dict[str, int | None] = {}
    for r in rows:
        prev = prev_by_team.get(r.code_team_id)
        delta = r.spend if prev is None or r.spend < prev else r.spend - prev
        prev_by_team[r.code_team_id] = r.spend
        spend_by_team[r.code_team_id] = spend_by_team.get(r.code_team_id, 0.0) + delta
        if r.tokens is not None:
            tokens_by_team[r.code_team_id] = (tokens_by_team.get(r.code_team_id) or 0) + r.tokens
        elif r.code_team_id not in tokens_by_team:
            tokens_by_team[r.code_team_id] = None

    # Les teams archivées restent facturables (leur conso du mois est réelle)
    # mais sont marquées pour la lisibilité du relevé.
    def _row(tid, spend):
        t = teams_by_id.get(tid)
        return {"team_id": tid,
                "name": t.name if t else tid,
                "bu": (t.bu or "") if t else "",
                "archived": bool(t and t.status != "active"),
                "spend_eur": round(spend, 4), "tokens": tokens_by_team.get(tid)}
    teams = [_row(tid, s)
             for tid, s in sorted(spend_by_team.items(), key=lambda kv: -kv[1])]
    known_tokens = [t["tokens"] for t in teams if t["tokens"] is not None]
    return {
        "teams": teams,
        "total_eur": round(sum(spend_by_team.values()), 4),
        "total_tokens": sum(known_tokens) if known_tokens else None,
    }


def _rag_month_tokens(org_id: str, first: datetime.date, nxt: datetime.date) -> dict:
    from peewee import fn
    from api.db.db_models import DB, Workspace, TokenUsageDaily
    with DB.connection_context():
        # tous statuts : la conso passée d'un workspace archivé se facture aussi
        workspaces = list(Workspace.select().where(Workspace.org_id == org_id))
        ws_by_tenant = {w.tenant_id: w for w in workspaces}
        agg = {}
        if ws_by_tenant:
            for d in (TokenUsageDaily
                      .select(TokenUsageDaily.tenant_id,
                              fn.COALESCE(fn.SUM(TokenUsageDaily.tokens), 0).alias("tok"))
                      .where((TokenUsageDaily.tenant_id.in_(list(ws_by_tenant.keys())))
                             & (TokenUsageDaily.date >= str(first))
                             & (TokenUsageDaily.date < str(nxt)))
                      .group_by(TokenUsageDaily.tenant_id).dicts()):
                agg[d["tenant_id"]] = int(d["tok"])
    out = []
    for tenant_id, tokens in sorted(agg.items(), key=lambda kv: -kv[1]):
        ws = ws_by_tenant[tenant_id]
        out.append({"workspace_id": ws.id, "name": ws.name,
                    "bu": (ws.settings_json or {}).get("bu", "") or "", "tokens": tokens})
    return {"workspaces": out, "total_tokens": sum(agg.values())}


def billing_summary(org_id: str, month: str) -> dict:
    """Récap consolidé du mois — source de la carte Facturation ET du relevé CSV.

    RAG = FORFAIT (les tokens sont du fair-use informatif, jamais valorisés) ;
    Code = consommation réelle du mois. total_eur = forfait RAG + conso Code."""
    from api.db.db_models import DB, Organisation
    first, nxt = month_bounds(month)
    with DB.connection_context():
        org = Organisation.get_or_none(Organisation.id == org_id)
    fee = org.rag_monthly_fee_eur if org else None
    code = _code_month_spend(org_id, first, nxt)
    rag = _rag_month_tokens(org_id, first, nxt)
    rag["monthly_fee_eur"] = fee  # None = non contractualisé
    return {
        "org_id": org_id,
        "month": month,
        "code": code,
        "rag": rag,
        "total_eur": round(code["total_eur"] + (fee or 0.0), 4),
    }


def billing_statement_csv(org_id: str, org_name: str, month: str) -> str:
    """Le relevé compta : lignes par entité + TOTAL, les deux produits."""
    import csv
    import io
    s = billing_summary(org_id, month)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["RELEVE MENSUEL", org_name, month,
                f"genere le {datetime.date.today().isoformat()}"])
    w.writerow([])
    w.writerow(["produit", "entite", "bu", "tokens", "montant_eur"])
    for t in s["code"]["teams"]:
        label = t["name"] + (" (archivée)" if t.get("archived") else "")
        w.writerow(["code", label, t.get("bu", ""),
                    "" if t["tokens"] is None else t["tokens"], t["spend_eur"]])
    w.writerow(["code", "TOTAL", "",
                "" if s["code"]["total_tokens"] is None else s["code"]["total_tokens"],
                s["code"]["total_eur"]])
    if s["rag"]["monthly_fee_eur"] is not None:
        w.writerow(["rag", "forfait mensuel", "", "", s["rag"]["monthly_fee_eur"]])
    for ws in s["rag"]["workspaces"]:
        w.writerow(["rag", f"conso {ws['name']} (fair-use, incluse)", ws["bu"], ws["tokens"], ""])
    w.writerow(["rag", "TOTAL", "", s["rag"]["total_tokens"],
                "" if s["rag"]["monthly_fee_eur"] is None else s["rag"]["monthly_fee_eur"]])
    w.writerow([])
    w.writerow(["TOTAL GENERAL", "", "", "", s["total_eur"]])
    return "\ufeff" + buf.getvalue()  # BOM : Excel FR ouvre l'UTF-8 proprement
