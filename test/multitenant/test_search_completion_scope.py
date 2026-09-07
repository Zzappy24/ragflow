"""
Pins des correctifs « Recherche » du 2026-09-07 (vidéo de démo) :

1. La complétion de recherche (`/searches/<id>/completion[s]`) doit résoudre
   ses modèles sur le tenant du workspace actif. Upstream passe
   `current_user.id` en POSITIONNEL à `async_ask` — hors de portée du pin
   `TestUserIdAsTenantIdMisuse` (qui ne voit que le mot-clé `tenant_id=`).
   Symptôme : « Résumé IA : Tenant Model … not found » sur tout workspace.
2. `async_ask` doit replier sur le modèle par défaut du tenant quand
   `search_config.chat_id` est absent (sinon `split_model_name(None)`).
3. White-label : la page Recherche du front n'affiche plus le wordmark
   « RAGFlow » ni le « Hi there » codé en dur.
"""
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _function_source(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} introuvable dans {path}")


class TestSearchCompletionTenantScope:
    def test_completion_route_uses_active_tenant_not_user_id(self):
        path = REPO_ROOT / "api/apps/restful_apis/search_api.py"
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, str(path))
        # fonctions de premier niveau uniquement : la closure `stream()` interne
        # hérite du scope de la route, c'est la route qui doit résoudre le tenant
        routes_with_ask = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(src, node) or ""
                if "async_ask(" in body:
                    routes_with_ask.append((node.name, body))
        assert routes_with_ask, "aucune route n'appelle async_ask : le pin est périmé, adapter"
        for name, body in routes_with_ask:
            assert not re.search(r"\buid\s*=\s*current_user\.id\b", body), (
                f"{name} : `uid = current_user.id` re-passe le tenant personnel à async_ask "
                "(modèle de résumé introuvable en workspace) — utiliser active_tenant_id()"
            )
            assert "active_tenant_id()" in body, f"{name} : async_ask doit recevoir active_tenant_id()"

    def test_async_ask_falls_back_to_tenant_default_chat_model(self):
        body = _function_source(REPO_ROOT / "api/db/services/dialog_service.py", "async_ask")
        assert "if not chat_llm_name:" in body and "TenantService.get_by_id(tenant_id)" in body, (
            "async_ask doit replier sur tenant.llm_id quand search_config.chat_id est absent"
        )
        # le repli précède la résolution du modèle de chat
        assert body.index("if not chat_llm_name:") < body.index("LLMType.CHAT, chat_llm_name"), "repli après la résolution : inutile"


class TestSearchPageWhiteLabel:
    def test_search_page_has_no_ragflow_wordmark(self):
        logo = (REPO_ROOT / "web/src/pages/next-search/ragflow-logo.tsx").read_text(encoding="utf-8")
        assert not re.search(r">\s*RAGFlow\s*<", logo), "wordmark « RAGFlow » réintroduit sur la page Recherche"
        assert "searchData?.name" in logo, "le titre de la page Recherche doit être le nom de l'app de recherche"
        home = (REPO_ROOT / "web/src/pages/next-search/search-home.tsx").read_text(encoding="utf-8")
        assert "Hi there" not in home, "salutation anglaise codée en dur sur la page Recherche"
        for loc in ("en", "fr"):
            text = (REPO_ROOT / f"web/src/locales/{loc}.ts").read_text(encoding="utf-8")
            assert re.search(r"\n\s+hello: '", text), f"clé search.hello absente de {loc}.ts"
