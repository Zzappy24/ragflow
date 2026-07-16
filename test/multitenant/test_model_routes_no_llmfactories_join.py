"""Garde de régression — bug 'Model not found after update' (2026-07-16).

TenantLLMService.get_my_llms() fait un INNER JOIN sur llm_factories, table
vide par design dans notre fork (init_llm_factory désactivé après la
migration tenant_model_provider). Toute route d'admin qui re-lit un modèle
via get_my_llms(tenant_id) obtient donc zéro ligne et renvoie 404
('Model not found after update') ou des champs vides, alors que la ligne
existe. Les routes de configuration modèle DOIVENT interroger tenant_llm
directement (cf. _fetch_stored_row / la route liste).

Voir memory feedback_init_llm_factory_recomment + CLAUDE.md.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.p1


def test_model_routes_do_not_refetch_via_get_my_llms():
    src = Path(__file__).resolve().parents[2] / "management/server/routers/models.py"
    text = src.read_text()
    # `get_my_llms(tenant_id)` = l'appel réel ; les commentaires explicatifs
    # écrivent `get_my_llms()` (sans argument), donc ne matchent pas.
    assert "get_my_llms(tenant_id)" not in text, (
        "management/server/routers/models.py re-lit un modèle via "
        "get_my_llms(tenant_id) : INNER JOIN sur llm_factories (vide) -> 404 "
        "'Model not found after update' sur un update valide. Interroger "
        "tenant_llm directement (voir _fetch_stored_row)."
    )
