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
