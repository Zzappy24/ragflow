"""CUSTOM B2B SaaS — toute route service-à-service vérifie le secret partagé.

Constaté le 2026-09-06 : ``/api/v1/internal/bridge/prepare`` et
``/api/v1/internal/invite/prepare`` (user_api.py) n'avaient NI login NI
secret : quiconque connaissait un user_id pouvait fabriquer un code de
connexion (bridge → login en tant que ce compte) ou d'invitation (→ poser
le mot de passe du compte). Joignables depuis internet jusqu'au blocage
nginx du même jour. Deux verrous statiques :

1. chaque handler dont la route commence par ``/internal/`` appelle
   ``check_internal_secret`` / ``_check_internal_secret`` AVANT toute
   lecture du corps ou de la base ;
2. chaque appel du panel vers ``/api/v1/internal/`` envoie l'en-tête
   ``X-Internal-Secret``.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESTFUL = ROOT / "api/apps/restful_apis"
PANEL = ROOT / "management/server"

# ast.get_source_segment d'un décorateur ne contient pas le `@`.
ROUTE_RE = re.compile(r'manager\.route\("(/internal/[^"]*)"')


def _internal_handlers():
    out = []
    for path in sorted(RESTFUL.glob("*.py")):
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                seg = ast.get_source_segment(src, deco) or ""
                m = ROUTE_RE.search(seg)
                if m:
                    out.append((path.name, m.group(1), node.name, ast.get_source_segment(src, node)))
    return out


class TestInternalRoutesCheckSecret:
    def test_at_least_the_known_routes_are_found(self):
        names = {h[2] for h in _internal_handlers()}
        assert {"internal_bridge_prepare", "internal_invite_prepare", "internal_llm_verify",
                "internal_purge_workspace_data"} <= names

    def test_every_internal_route_checks_the_secret_first(self):
        offenders = []
        for fname, route, name, body in _internal_handlers():
            m = re.search(r"_?check_internal_secret\(\)", body)
            if not m:
                offenders.append(f"{fname}::{name} ({route}) : aucune vérification du secret")
                continue
            # la vérification précède toute lecture du corps / de la base
            first_io = min([i for i in (body.find("get_request_json("), body.find("request.get_json("),
                                        body.find("Service."), body.find("request.args")) if i != -1] or [len(body)])
            if m.start() > first_io:
                offenders.append(f"{fname}::{name} ({route}) : secret vérifié APRÈS une lecture")
        assert not offenders, "\n".join(offenders)

    def test_single_shared_implementation(self):
        src = (ROOT / "api/utils/internal_secret.py").read_text()
        assert "hmac.compare_digest" in src
        assert 'os.environ.get("INTERNAL_API_SECRET", "")' in src
        # plus de copie locale dans internal_api.py
        assert "def _check_internal_secret" not in (RESTFUL / "internal_api.py").read_text()


class TestPanelSendsSecret:
    def test_every_panel_call_to_internal_routes_sends_the_header(self):
        offenders = []
        for path in sorted(PANEL.rglob("*.py")):
            src = path.read_text()
            for m in re.finditer(r"/api/v1/internal/", src):
                window = src[m.start(): m.start() + 600]
                if "X-Internal-Secret" not in window and "internal_headers" not in window:
                    offenders.append(f"{path.relative_to(ROOT)}:{src.count(chr(10), 0, m.start()) + 1}")
        assert not offenders, "appels internes sans X-Internal-Secret :\n" + "\n".join(offenders)
