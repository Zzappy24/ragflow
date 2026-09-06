"""CUSTOM B2B SaaS — deux durcissements du 2026-09-06 (audit post boucle de login).

1. Jamais un User brut dans une réponse API : ``to_dict()`` / ``to_json()``
   renvoient ``__data__`` entier, donc le HASH du mot de passe et
   l'``access_token`` brut (le secret derrière le token signé). Constaté sur
   GET /api/v1/users/me, /bridge et /set_initial_password. Seul
   ``to_safe_dict(for_self=True)`` est admis pour sérialiser un utilisateur.

2. Les niveaux de log des pods (GET/PUT /system/config/log) sont réservés aux
   superusers : upstream laissait tout utilisateur connecté les lire et les
   CHANGER (inondation DEBUG, ou masquage des traces en ERROR).

Pins statiques (pas de serveur) : un merge upstream qui réintroduit
``user.to_dict()`` ou retire la garde fait échouer ces tests.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
USER_API = ROOT / "api/apps/restful_apis/user_api.py"
SYSTEM_API = ROOT / "api/apps/restful_apis/system_api.py"
DB_MODELS = ROOT / "api/db/db_models.py"


def _func_source(path: pathlib.Path, name: str) -> str:
    src = path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} introuvable dans {path.name}")


class TestNoRawUserInResponses:
    def test_sensitive_fields_cover_password_and_access_token(self):
        src = DB_MODELS.read_text()
        m = re.search(r"SENSITIVE_FIELDS\s*=\s*\{([^}]*)\}", src)
        assert m, "User.SENSITIVE_FIELDS absent"
        fields = m.group(1)
        assert '"password"' in fields and '"access_token"' in fields

    def test_user_api_never_serializes_raw_user(self):
        src = USER_API.read_text()
        assert ".to_json()" not in src, "to_json() renvoie le User brut (hash + access_token)"
        raw = re.findall(r"\b(?:current_user|user)\.to_dict\(\)", src)
        assert not raw, f"User brut sérialisé : {raw}"

    def test_profile_bridge_and_invite_use_safe_dict(self):
        for name in ("user_profile", "bridge_login", "set_initial_password"):
            body = _func_source(USER_API, name)
            assert "to_safe_dict(for_self=True)" in body, f"{name} doit utiliser to_safe_dict(for_self=True)"


class TestLogLevelsSuperuserOnly:
    def test_both_log_routes_check_superuser_before_acting(self):
        for name in ("get_logger_levels", "set_logger_level"):
            body = _func_source(SYSTEM_API, name)
            assert "current_user.is_superuser" in body, f"{name} doit refuser les non-superusers"
            # la garde précède tout traitement (premier `return` du corps hors docstring)
            guard = body.index("current_user.is_superuser")
            first_action = body.index("get_log_levels()" if name == "get_logger_levels" else "request.get_json()")
            assert guard < first_action, f"{name} : la garde superuser doit venir avant le traitement"

    def test_current_user_is_imported_in_system_api(self):
        src = SYSTEM_API.read_text()
        assert re.search(r"^from api\.apps import .*\bcurrent_user\b", src, re.MULTILINE), "current_user non importé dans system_api.py"
