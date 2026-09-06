"""CUSTOM B2B SaaS — client de la purge physique (panel → ragflow-api).

Le panel ne peut pas effacer lui-même les chunks (ES/Infinity) ni les
fichiers (MinIO) : image slim. Il délègue à la route interne de l'API,
protégée par le secret partagé, et n'efface ses lignes de structure QUE si
l'API confirme. Toute situation ambiguë (env absente, réseau, refus, erreur)
lève ``TenantPurgeError`` : pas de purge « best effort » qui laisserait des
données client derrière une suppression apparemment réussie.
"""
import logging
import os

import httpx

logger = logging.getLogger("management.tenant_purge")

# Une purge parcourt tous les objets d'un tenant : borne longue, alignée sur
# le RESPONSE_TIMEOUT de l'API (600 s).
_TIMEOUT_S = float(os.environ.get("TENANT_PURGE_TIMEOUT_S", "600"))


class TenantPurgeError(RuntimeError):
    """La purge physique n'a PAS été confirmée par l'API : ne rien effacer."""


def purge_tenant_content_via_api(tenant_id: str) -> dict:
    api_base = os.environ.get("RAGFLOW_API_URL", "").rstrip("/")
    secret = os.environ.get("INTERNAL_API_SECRET", "")
    if not api_base or not secret:
        raise TenantPurgeError("RAGFLOW_API_URL / INTERNAL_API_SECRET manquants : purge physique impossible")
    url = f"{api_base}/api/v1/internal/workspaces/{tenant_id}/purge-data"
    try:
        resp = httpx.post(url, headers={"X-Internal-Secret": secret}, timeout=_TIMEOUT_S)
    except Exception as e:
        raise TenantPurgeError(f"purge physique injoignable ({e})") from e
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.status_code != 200 or body.get("code") != 0:
        raise TenantPurgeError(
            f"purge physique refusée (HTTP {resp.status_code}, code {body.get('code')}): {body.get('message')}"
        )
    summary = body.get("data") or {}
    logger.info("tenant %s: purge physique confirmée %s", tenant_id, summary)
    return summary
