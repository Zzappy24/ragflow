"""CUSTOM B2B SaaS — shards/réplicas des index ES pilotés par env (2026-09-06).

Contrat épinglé :
1. ``apply_env_index_settings`` : copie, jamais de mutation ; ES_NUMBER_OF_SHARDS
   / ES_NUMBER_OF_REPLICAS posés sous settings["index"] ; vide = mapping,
   invalide = ignoré (jamais bloquant).
2. ``es_conn_base`` l'applique aux TROIS créations d'index (chunks avec et sans
   exclude_source_vectors, doc_meta) — un merge upstream qui remet les settings
   bruts du mapping fait échouer le pin.
"""
import pathlib
import re

from common.doc_store.index_settings import apply_env_index_settings

ROOT = pathlib.Path(__file__).resolve().parents[2]
ES_BASE = ROOT / "common/doc_store/es_conn_base.py"

MAPPING_SETTINGS = {
    "index": {"number_of_shards": 2, "number_of_replicas": 0, "refresh_interval": "1000ms"},
    "similarity": {"scripted_sim": {"type": "scripted"}},
}


class TestApplyEnvIndexSettings:
    def test_no_env_keeps_mapping_and_never_mutates(self):
        out = apply_env_index_settings(MAPPING_SETTINGS, environ={})
        assert out == MAPPING_SETTINGS
        assert out is not MAPPING_SETTINGS and out["index"] is not MAPPING_SETTINGS["index"]

    def test_shards_override_only_touches_shards(self):
        out = apply_env_index_settings(MAPPING_SETTINGS, environ={"ES_NUMBER_OF_SHARDS": "1"})
        assert out["index"]["number_of_shards"] == 1
        assert out["index"]["number_of_replicas"] == 0
        assert out["index"]["refresh_interval"] == "1000ms"
        assert out["similarity"] == MAPPING_SETTINGS["similarity"]
        assert MAPPING_SETTINGS["index"]["number_of_shards"] == 2  # entrée intacte

    def test_replicas_override(self):
        out = apply_env_index_settings(MAPPING_SETTINGS, environ={"ES_NUMBER_OF_REPLICAS": "1"})
        assert out["index"]["number_of_replicas"] == 1
        assert out["index"]["number_of_shards"] == 2

    def test_invalid_values_are_ignored_not_fatal(self):
        env = {"ES_NUMBER_OF_SHARDS": "abc", "ES_NUMBER_OF_REPLICAS": "-1"}
        out = apply_env_index_settings(MAPPING_SETTINGS, environ=env)
        assert out == MAPPING_SETTINGS
        out = apply_env_index_settings(MAPPING_SETTINGS, environ={"ES_NUMBER_OF_SHARDS": "0"})
        assert out["index"]["number_of_shards"] == 2  # minimum 1

    def test_blank_env_means_mapping(self):
        out = apply_env_index_settings(MAPPING_SETTINGS, environ={"ES_NUMBER_OF_SHARDS": "  "})
        assert out == MAPPING_SETTINGS

    def test_settings_without_index_block(self):
        out = apply_env_index_settings({"similarity": {}}, environ={"ES_NUMBER_OF_SHARDS": "1"})
        assert out == {"similarity": {}, "index": {"number_of_shards": 1}}


class TestEsConnBaseAppliesOverride:
    def test_all_three_index_creations_go_through_helper(self):
        src = ES_BASE.read_text()
        assert src.count("apply_env_index_settings(") >= 3, "les 3 créations d'index (chunks ×2, doc_meta) doivent passer par apply_env_index_settings"
        raw = re.findall(r'settings=(?:self\.mapping|doc_meta_mapping)\["settings"\]', src)
        assert not raw, f"settings du mapping passés bruts à IndicesClient.create : {raw}"
