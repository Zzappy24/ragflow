"""Alertes budget du produit Code — proactives, par email.

Appelé par le housekeeping (15 min). Deux niveaux surveillés :
- chaque team active vs son max_budget,
- chaque clé active AYANT son propre max_budget (limites par siège).

Seuils 80 % et 100 %. Anti-spam : une alerte par seuil et par cycle
(CodeBudgetAlert) ; l'alerte se ré-arme quand le spend retombe sous le
seuil (reset de cycle LiteLLM). Sans SMTP configuré, on ne fait RIEN
(pas de row posée) : le jour où le SMTP arrive, les alertes partent au
prochain passage au lieu d'avoir été silencieusement consommées.

Destinataires : org admins de l'org de la team + admins délégués de la team.
"""
import asyncio
import datetime
import logging

from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)

THRESHOLDS = (100, 80)  # évalués du plus haut au plus bas


def _recipients_for_team(team) -> list[str]:
    from api.db.db_models import DB, OrgMember, CodeTeamMember, User
    with DB.connection_context():
        org_admin_ids = [m.user_id for m in OrgMember.select().where(
            (OrgMember.org_id == team.org_id) & (OrgMember.role == "org_admin")
            & (OrgMember.status == "1"))]
        delegated_ids = [m.user_id for m in CodeTeamMember.select().where(
            (CodeTeamMember.code_team_id == team.id) & (CodeTeamMember.role == "admin"))]
        ids = list(dict.fromkeys(org_admin_ids + delegated_ids))
        if not ids:
            return []
        return [u.email for u in User.select(User.email).where(
            (User.id.in_(ids)) & (User.status == "1"))]


def _alert_body(*, kind: str, name: str, team_name: str, pct: int,
                spend: float, budget: float, org_id: str) -> str:
    from management.server.config import settings
    what = (f"la team « {team_name} »" if kind == "team"
            else f"le siège « {name} » (team « {team_name} »)")
    lines = [
        f"Le budget Code de {what} a atteint {pct}% :",
        f"  {round(spend, 2)} € consommés / {round(budget, 2)} € de budget.",
        "",
    ]
    if pct >= 100:
        lines.append("Les requêtes concernées sont désormais refusées par la gateway "
                     "jusqu'au prochain cycle (ou jusqu'à augmentation du budget).")
    else:
        lines.append("Au rythme actuel, le blocage automatique interviendra à 100%.")
    if settings.PANEL_PUBLIC_URL:
        lines += ["", f"Gérer les budgets : {settings.PANEL_PUBLIC_URL}/admin/code?org={org_id}"]
    return "\n".join(lines)


def _evaluate(*, scope: str, ref_id: str, spend: float, budget: float,
              mails: list, recipients: list[str], subject_name: str,
              body_kwargs: dict) -> int:
    """Compare spend/budget aux seuils ; pousse au plus UN email dans `mails`
    (le seuil le plus haut nouvellement franchi), pose les rows anti-spam de
    tous les seuils franchis, supprime celles des seuils repassés en dessous
    (ré-armement au reset de cycle). Retourne le nombre d'alertes émises."""
    from api.db.db_models import DB, CodeBudgetAlert
    if not budget or budget <= 0:
        return 0
    pct = spend / budget * 100.0

    with DB.connection_context():
        existing = {r.threshold: r for r in CodeBudgetAlert.select().where(
            (CodeBudgetAlert.scope == scope) & (CodeBudgetAlert.ref_id == ref_id))}
        # Ré-armement : le spend est repassé sous un seuil déjà alerté ->
        # cycle réinitialisé côté LiteLLM, on supprime le marqueur.
        for th, row in list(existing.items()):
            if pct < th:
                CodeBudgetAlert.delete().where(CodeBudgetAlert.id == row.id).execute()
                del existing[th]

        crossed_unalerted = [th for th in THRESHOLDS if pct >= th and th not in existing]
        if not crossed_unalerted:
            return 0
        top = max(crossed_unalerted)
        now = datetime.datetime.now(datetime.timezone.utc)
        for th in crossed_unalerted:  # marque TOUS les seuils franchis
            CodeBudgetAlert.create(id=get_uuid(), scope=scope, ref_id=ref_id,
                                   threshold=th, spend_at_alert=spend, alerted_at=now)

    subject = f"[Code] Budget à {top}% — {subject_name}"
    body = _alert_body(pct=top, spend=spend, budget=budget, **body_kwargs)
    for to in recipients:
        mails.append((to, subject, body))
    return 1


def check_and_send_alerts(client=None) -> int:
    """Retourne le nombre d'alertes émises (0 si SMTP absent ou gateway down)."""
    from api.db.db_models import DB, CodeTeam, CodeKey
    from management.server.services.mailer import is_configured, send_mail
    from management.server.services.code_provisioning import _client, spend_by_litellm_team

    if not is_configured():
        return 0
    spend_map = spend_by_litellm_team(client=client)
    if spend_map is None:  # gateway down — un faux spend=0 ré-armerait tout
        return 0

    with DB.connection_context():
        teams = list(CodeTeam.select().where(
            (CodeTeam.status == "active") & (CodeTeam.litellm_team_id.is_null(False))))
        limited_keys = list(CodeKey.select().where(
            (CodeKey.status == "active") & (CodeKey.max_budget.is_null(False))
            & (CodeKey.litellm_key_id.is_null(False))))
    keys_by_team: dict[str, list] = {}
    for k in limited_keys:
        keys_by_team.setdefault(k.code_team_id, []).append(k)

    cl = _client(client)
    mails: list[tuple[str, str, str]] = []
    alerts = 0
    for t in teams:
        if t.litellm_team_id not in spend_map:
            continue  # même principe que snapshot_spend : pas de donnée, pas d'action
        recipients = _recipients_for_team(t)
        if recipients:
            alerts += _evaluate(scope="team", ref_id=t.id,
                                spend=spend_map[t.litellm_team_id], budget=t.max_budget,
                                mails=mails, recipients=recipients, subject_name=t.name,
                                body_kwargs={"kind": "team", "name": t.name,
                                             "team_name": t.name, "org_id": t.org_id})
        # Sièges à budget propre : un seul /key/list par team concernée.
        t_keys = keys_by_team.get(t.id)
        if not t_keys or not recipients:
            continue
        try:
            key_spend = {k.get("token"): float(k.get("spend") or 0.0)
                         for k in cl.list_keys(t.litellm_team_id)}
        except Exception as e:
            logger.warning("budget alerts: key spend indisponible pour team %s: %s", t.id, e)
            continue
        for key in t_keys:
            if key.litellm_key_id not in key_spend:
                continue
            alerts += _evaluate(scope="key", ref_id=key.id,
                                spend=key_spend[key.litellm_key_id], budget=key.max_budget,
                                mails=mails, recipients=recipients,
                                subject_name=f"{key.label} ({t.name})",
                                body_kwargs={"kind": "key", "name": key.label,
                                             "team_name": t.name, "org_id": t.org_id})

    if mails:
        async def _send_all():
            for m in mails:  # séquentiel : simple, et le volume est faible
                await send_mail(*m)
        # Toujours appelé depuis un thread sans event loop (to_thread du
        # scheduler, ou threadpool des routes sync FastAPI) — asyncio.run ok.
        asyncio.run(_send_all())
    return alerts
