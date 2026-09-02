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


def test_panel_uses_opaque_sessions():
    """Panel : sessions OPAQUES (modèle GitHub/Slack pour une app web
    first-party) — le token est aléatoire (rien à forger), seul son sha256
    vit en base, la row est la session (suppression = révocation
    instantanée, zéro fenêtre résiduelle)."""
    sessions = (REPO / "management" / "server" / "auth" / "sessions.py").read_text()
    assert "sha256" in sessions, "le token doit être stocké hashé, jamais en clair"
    assert "token_urlsafe" in sessions, "le token doit être aléatoire (rien à forger)"
    deps = (REPO / "management" / "server" / "auth" / "dependencies.py").read_text()
    assert "resolve_session" in deps, "l'auth du panel ne passe plus par les sessions opaques"
    assert "decode_token" not in deps, "retour du JWT dans l'auth du panel"
    auth = (REPO / "management" / "server" / "routers" / "auth.py").read_text()
    assert "open_session" in auth and "close_session" in auth
    assert "/refresh" not in auth, "la route refresh ne doit pas revenir (supprimée avec le JWT)"


def test_panel_session_ttl_is_24h():
    src = (REPO / "management" / "server" / "config.py").read_text()
    assert "ADMIN_SESSION_TTL_MINUTES: int = 60 * 24" in src, (
        "TTL de session panel != 24 h — décision 2026-09-02 à re-acter si changement voulu"
    )
