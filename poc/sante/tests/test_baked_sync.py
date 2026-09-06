"""Garde anti-dérive : la copie baked dans l'image sandbox doit rester
identique à la source poc/sante/health_recipes.py (au header GÉNÉRÉ près).
Sinon le sandbox exécute une version différente de celle qu'on teste.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "poc/sante/health_recipes.py"
BAKED = ROOT / "agent/sandbox/sandbox_base_image/python/health_recipes.py"


def test_baked_copy_matches_source():
    src = SRC.read_text()
    baked = BAKED.read_text()
    # le baked a une 1re ligne de header GÉNÉRÉ en plus
    baked_body = baked.split("\n", 1)[1]
    assert baked_body == src, (
        "La copie baked a dérivé de la source. Régénère :\n"
        "  { echo '# GÉNÉRÉ depuis poc/sante/health_recipes.py — ne pas éditer ici'; "
        "cat poc/sante/health_recipes.py; } > "
        "agent/sandbox/sandbox_base_image/python/health_recipes.py"
    )
