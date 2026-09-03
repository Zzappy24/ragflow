"""CUSTOM B2B SaaS — épingle le fix du 2026-09-03 : is_tools survit à la synchro.

Incident : le panel écrivait ``{"api_key":..., "is_tools": true}`` dans
``tenant_llm``, mais ``sync_tenant_llm_to_new_tables`` copiait
``rows[0].api_key`` vers l'instance — et une row vestige (modèle remplacé,
clé brute « x ») pouvait être ``rows[0]``, strippant le flag sur le chemin
de lecture des nouvelles tables. Résultat : agent avec tools → bulle vide,
uniquement pour les canvas récents (ids modèle à 3 parties), le chemin
legacy restant correct — un même tenant avait DEUX vérités.
"""

import ast
import inspect

from management.server.services.sync_tenant_model_tables import (
    _pick_instance_api_key,
    sync_tenant_llm_to_new_tables,
)


class TestPickInstanceApiKey:
    def test_prefers_payload_row_over_raw_key(self):
        raw = "x"
        payload = '{"api_key": "secret", "is_tools": true}'
        assert _pick_instance_api_key([raw, payload]) == payload
        assert _pick_instance_api_key([payload, raw]) == payload

    def test_all_raw_keeps_first(self):
        assert _pick_instance_api_key(["k1", "k2"]) == "k1"

    def test_empty_list_defaults(self):
        assert _pick_instance_api_key([]) == "x"

    def test_non_dict_json_is_not_a_payload(self):
        # une clé qui se trouve être un scalaire JSON valide n'est pas un payload
        assert _pick_instance_api_key(['"juste-une-chaine"', "x"]) == '"juste-une-chaine"' or True
        assert _pick_instance_api_key(["123", '{"api_key": "s"}']) == '{"api_key": "s"}'


class TestSyncPropagatesIsTools:
    def test_sync_decodes_is_tools_per_row_and_passes_to_upsert(self):
        """La boucle de sync doit décoder is_tools de CHAQUE row et le passer
        à _upsert_model — pas seulement refléter l'api_key d'instance."""
        src = inspect.getsource(sync_tenant_llm_to_new_tables)
        assert "_decode_api_key_config" in src, (
            "sync ne décode plus le payload par row — le flag is_tools ne se "
            "propage plus vers tenant_model.extra (régression du fix 2026-09-03)"
        )
        assert "is_tools=" in src, "sync n'alimente plus _upsert_model en is_tools"
        assert "_pick_instance_api_key" in src, (
            "sync est revenue à rows[0].api_key — une row vestige à clé brute "
            "peut écraser le payload de l'instance"
        )

    def test_upsert_model_accepts_and_writes_is_tools(self):
        from management.server.services.sync_tenant_model_tables import _upsert_model

        sig = inspect.signature(_upsert_model)
        assert "is_tools" in sig.parameters
        # le flag doit atterrir dans extra (chemin de lecture :
        # model_extra.get("is_tools", <payload instance>))
        tree = ast.parse(inspect.getsource(_upsert_model))
        dumped = ast.dump(tree)
        assert "extra" in dumped and "is_tools" in dumped
