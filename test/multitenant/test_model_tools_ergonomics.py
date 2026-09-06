"""CUSTOM B2B SaaS — épingle le lot « ergonomie modèles » (incident 2026-09-03).

Trois contrats issus de 3h de diagnostic d'une bulle vide :
1. Un agent avec tools sur un modèle sans function calling échoue BRUYAMMENT
   (exception remontée en conversation), plus jamais en warning silencieux.
2. Le cache de config modèle des pods api est invalidé immédiatement quand le
   panel admin mute un modèle (tampon de version Redis), plus de fenêtre de
   5 min où deux pods servent deux configs différentes.
3. La liste des modèles du panel expose is_tools (badge FC), et l'édition ne
   peut plus effacer le flag par omission.
"""

import inspect

import pytest


class TestBindToolsFailsLoud:
    def test_bind_tools_raises_when_model_has_no_tools(self):
        from api.db.services.llm_service import LLMBundle

        bundle = LLMBundle.__new__(LLMBundle)
        bundle.is_tools = False
        bundle.model_config = {"llm_name": "test-model"}
        with pytest.raises(RuntimeError) as exc:
            bundle.bind_tools(object(), [{"type": "function"}])
        # le message doit guider l'utilisateur vers le panel admin
        assert "Function calling" in str(exc.value)
        assert "test-model" in str(exc.value)

    def test_bind_tools_delegates_when_supported(self):
        from api.db.services.llm_service import LLMBundle

        calls = []

        class FakeMdl:
            def bind_tools(self, session, tools):
                calls.append((session, tools))

        bundle = LLMBundle.__new__(LLMBundle)
        bundle.is_tools = True
        bundle.model_config = {"llm_name": "test-model"}
        bundle.mdl = FakeMdl()
        bundle.bind_tools("s", ["t"])
        assert calls == [("s", ["t"])]


class TestVersionedModelConfigCache:
    def _seed(self, svc, key, ver):
        svc._MODEL_CONFIG_CACHE[key] = ({"llm_name": "m"}, __import__("time").monotonic(), ver)

    def test_hit_when_version_unchanged(self, monkeypatch):
        import api.db.joint_services.tenant_model_service as svc

        monkeypatch.setattr(svc, "_cfg_redis_version", lambda t: "7")
        self._seed(svc, "t1:chat:m", "7")
        assert svc._cache_lookup("t1:chat:m", "t1") == {"llm_name": "m"}

    def test_miss_when_panel_bumped_version(self, monkeypatch):
        import api.db.joint_services.tenant_model_service as svc

        monkeypatch.setattr(svc, "_cfg_redis_version", lambda t: "8")
        self._seed(svc, "t2:chat:m", "7")
        assert svc._cache_lookup("t2:chat:m", "t2") is None
        assert "t2:chat:m" not in svc._MODEL_CONFIG_CACHE

    def test_fail_open_without_redis(self, monkeypatch):
        import api.db.joint_services.tenant_model_service as svc

        monkeypatch.setattr(svc, "_cfg_redis_version", lambda t: None)
        self._seed(svc, "t3:chat:m", "7")
        assert svc._cache_lookup("t3:chat:m", "t3") == {"llm_name": "m"}

    def test_all_mutation_routes_bump_version(self):
        """Chaque sync panel->nouvelles tables doit être suivie d'un bump."""
        import management.server.routers.models as routers

        src = inspect.getsource(routers)
        syncs = src.count("sync_tenant_llm_to_new_tables(")
        bumps = src.count("_bump_model_cfg_version(")
        # 1 définition d'import du sync par site + le helper _bump défini 1 fois
        assert bumps - 1 >= syncs - src.count(
            "from management.server.services.sync_tenant_model_tables"
        ), (
            f"{syncs} appels sync vs {bumps - 1} bumps — une route de mutation "
            "modèle n'invalide plus le cache des pods api"
        )


class TestIsToolsExposure:
    def test_list_route_returns_is_tools(self):
        import management.server.routers.models as routers

        src = inspect.getsource(routers.list_workspace_providers)
        assert "is_tools" in src and "_decode_api_key_config" in src, (
            "la liste des modèles n'expose plus is_tools — le badge FC du "
            "panel afficherait faux en silence"
        )

    def test_response_schema_has_is_tools(self):
        from management.server.models.schemas import WsLlmProviderResponse

        assert "is_tools" in WsLlmProviderResponse.model_fields


class TestMaxRoundsAlwaysVisible:
    """La sortie « max rounds » ne peut plus être une bulle vide : consigne
    finale explicite + filet _max_rounds_fallback affichant la dernière
    erreur d'outil (incident 2026-09-03, run santé bloqué par l'AST)."""

    def test_fallback_surfaces_last_tool_error(self):
        from rag.llm.chat_model import Base

        b = Base.__new__(Base)
        b.max_rounds = 5
        history = [
            {"role": "user", "content": "q"},
            {"role": "tool", "content": "Code is unsafe: Line 4"},
            {"role": "assistant", "content": ""},
        ]
        out = b._max_rounds_fallback(history)
        assert "ERROR" in out and "Code is unsafe" in out

    def test_both_loop_exits_use_final_prompt_and_fallback(self):
        import inspect

        from rag.llm import chat_model

        src = inspect.getsource(chat_model)
        assert src.count("_MAX_ROUNDS_FINAL_PROMPT.format") >= 2, (
            "un des deux chemins max-rounds est revenu au message brut "
            "« Exceed max rounds » sans consigne de réponse finale"
        )
        assert src.count("_max_rounds_fallback(history)") >= 2, (
            "un des deux chemins max-rounds peut à nouveau finir en bulle vide"
        )


class TestPanelModelRoutesHaveNoUndefinedNames:
    def test_ruff_f821_clean_on_models_router(self):
        """La liste des modèles d'un workspace renvoyait 500 (NameError
        TenantLLMService) : le badge FC référençait un nom jamais importé.
        F821 (nom indéfini) doit rester vide sur ce routeur (recette 2026-09-07)."""
        import shutil, subprocess, pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        ruff = shutil.which("ruff") or shutil.which("uvx")
        if not ruff:
            pytest.skip("ruff indisponible")
        cmd = [ruff] + (["ruff"] if ruff.endswith("uvx") else []) + ["check", "--select", "F821", "--config", str(root / "pyproject.toml"), "management/server/routers/models.py"]
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        assert res.returncode == 0, res.stdout + res.stderr
