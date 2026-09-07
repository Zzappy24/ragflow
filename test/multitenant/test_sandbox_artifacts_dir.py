"""Pin : le bootstrap du job sandbox k8s cree artifacts/ AVANT le code utilisateur.

Sans ca, un graphique matplotlib sauve dans artifacts/ echoue : le dossier
n'existe pas et le code utilisateur ne peut pas le creer (import os bloque par
la securite AST). Le bootstrap est du code de confiance (commande conteneur).
Incident 2026-09-08 (agent sante, "fais-moi un graphique").
"""
from agent.sandbox.providers.k8s import _BOOTSTRAP


def test_bootstrap_creates_artifacts_dir_before_user_code():
    assert "makedirs('artifacts'" in _BOOTSTRAP or 'makedirs("artifacts"' in _BOOTSTRAP
    # la creation doit precer l'exec du code utilisateur
    assert _BOOTSTRAP.index("makedirs") < _BOOTSTRAP.index("exec(")
