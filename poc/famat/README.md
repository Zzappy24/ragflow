# POC FAMAT — détection de dérive process

Spec : docs/superpowers/specs/2026-08-11-famat-drift-agent-poc-design.md
Données : `data/Payload-20260526.csv` (JAMAIS commité — .gitignore).
Tests : `uv run --with duckdb python -m pytest poc/famat/tests/ -v`

## Resync de l'image sandbox custom

`agent/sandbox/sandbox_base_image/python/famat_recipes.py` est une **copie cuite** de
`poc/famat/famat_recipes.py`, embarquée dans l'image `sandbox-base-python:latest` pour que le
tool `code_exec` de RAGFlow puisse faire `import famat_recipes`. Ce n'est PAS un module partagé
par symlink — c'est un artefact de build régénérable, marqué en tête par le commentaire
`# GÉNÉRÉ depuis poc/famat/famat_recipes.py — ne pas éditer ici`.

À chaque modification de `poc/famat/famat_recipes.py`, refaire la copie puis rebuilder l'image :

```bash
cp poc/famat/famat_recipes.py agent/sandbox/sandbox_base_image/python/famat_recipes.py
# ré-ajouter le header si l'éditeur ne le préserve pas :
#   sed -i '1i # GÉNÉRÉ depuis poc/famat/famat_recipes.py — ne pas éditer ici' \
#     agent/sandbox/sandbox_base_image/python/famat_recipes.py
cd agent/sandbox
docker build --build-arg NEED_MIRROR=0 -t sandbox-base-python:latest ./sandbox_base_image/python
docker run --rm sandbox-base-python:latest python -c \
  "import duckdb, pymysql, matplotlib, famat_recipes; print('OK', duckdb.__version__)"
```
