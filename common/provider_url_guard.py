"""CUSTOM B2B SaaS — garde SSRF des URLs de fournisseurs de modèles (audit 2026-09-06).

Un ws_admin (un client) peut déclarer un fournisseur avec son propre
``api_base``/``base_url`` : côté produit (``provider_api_service``) comme côté
panel (``management/server/routers/models.py``). Le pod api instancie alors un
client OpenAI/HTTP vers cette URL : sans garde, ``http://ragflow-es:9200``,
``http://kubernetes.default.svc`` ou le lien-local 169.254.169.254 étaient
sondés depuis le cluster et l'erreur renvoyée à l'appelant.

Règle : URL publique obligatoire (``common.ssrf_guard.assert_url_is_safe``),
SAUF les hôtes des fournisseurs de la plateforme elle-même (vLLM interne,
variables ``VLLM_*_BASE_URL``), qui sont volontairement intra-cluster.
"""
import os
from urllib.parse import urlparse

_PLATFORM_URL_ENVS = ("VLLM_CHAT_BASE_URL", "VLLM_EMBED_BASE_URL", "VLLM_RERANK_BASE_URL")


def platform_provider_hosts() -> set[str]:
    hosts = set()
    for env in _PLATFORM_URL_ENVS:
        host = urlparse(os.environ.get(env, "") or "").hostname
        if host:
            hosts.add(host.lower())
    return hosts


def assert_provider_base_url_allowed(base_url: str | None) -> None:
    """Lève ``ValueError`` si l'URL vise un hôte non public hors plateforme."""
    if not base_url or not str(base_url).strip():
        return
    url = str(base_url).strip()
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if host and host in platform_provider_hosts():
        return
    from common.ssrf_guard import assert_url_is_safe
    assert_url_is_safe(url)
