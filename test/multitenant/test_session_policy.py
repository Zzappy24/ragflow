"""Pin de la politique de session (actée 2026-09-02).

« Access court (60 min panel), session 7 jours, révocation serveur
instantanée. » Avant : token RAGFlow vérifié sans max_age (session
éternelle) et refresh token du panel émis mais jamais utilisé par le
front (déconnexion toutes les 60 min).
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_ragflow_token_has_max_age():
    src = (REPO / "api" / "apps" / "__init__.py").read_text()
    assert "SESSION_MAX_AGE_S" in src
    assert "7 * 24 * 3600" in src, "défaut de session != 7 jours — décision à re-acter"
    assert "max_age=SESSION_MAX_AGE_S" in src, (
        "jwt.loads sans max_age = session éternelle (retour au comportement d'avant)"
    )


def test_panel_front_uses_refresh_token():
    src = (REPO / "management" / "web" / "src" / "lib" / "api.ts").read_text()
    assert "auth/refresh" in src, "le front ne consomme plus le refresh token (déconnexion 60 min)"
    assert "_retried" in src, "garde anti-boucle du rejeu absente"
    assert "refreshing" in src, "single-flight absent (tempête de refresh sur 401 concurrents)"


def test_panel_refresh_is_stateful_with_rotation():
    """Le refresh du panel doit vérifier la row admin_session (révocation
    serveur — le JWT seul ne suffit plus, une fuite du secret ne forge plus
    de session durable) et DÉTRUIRE l'ancien jti (rotation : un refresh
    rejoué échoue)."""
    src = (REPO / "management" / "server" / "routers" / "auth.py").read_text()
    refresh_src = src[src.index('@router.post("/refresh"'):src.index('@router.post("/logout"')]
    assert "AdminSession.get_or_none" in refresh_src, "refresh sans vérification de session serveur"
    assert "delete_instance" in refresh_src, "rotation absente — un refresh volé serait rejouable"
    assert "connection_context" in refresh_src
    logout_src = src[src.index('@router.post("/logout"'):]
    assert "delete_instance" in logout_src, "logout ne révoque pas la session serveur"
    assert "AdminSession" in (REPO / "api" / "db" / "db_models.py").read_text()


def test_panel_refresh_ttl_is_24h():
    src = (REPO / "management" / "server" / "config.py").read_text()
    assert "JWT_REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 h" in src, (
        "TTL du refresh panel != 24 h — décision 2026-09-02 à re-acter si changement voulu"
    )
