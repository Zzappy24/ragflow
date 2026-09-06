"""CUSTOM B2B SaaS — injection de source dans code_exec (agent data, 2026-09-06).

Le LLM ne doit JAMAIS porter l'URL présignée (400 caractères que les petits
modèles mutilent). Quand le tool code_exec a une `source_file` configurée, il
résout l'URL côté serveur et l'injecte dans les arguments (donc dans main(url)),
sans que le modèle la voie. Le code libre reste entier : le modèle écrit ce
qu'il veut, seul l'accès à la donnée est câblé.

On teste l'assemblage des arguments (couture pure), sans canvas ni sandbox.
"""
from types import SimpleNamespace

from agent.tools.code_exec import CodeExec


def _param(**kw):
    base = {"arguments": {}, "source_file": "", "source_arg": "url"}
    base.update(kw)
    return SimpleNamespace(**base)


def test_source_file_injects_resolved_url_without_model():
    param = _param(source_file="file-abc")
    args = CodeExec._assemble_arguments(
        param, kwargs={}, get_var=lambda v: None,
        resolve_source=lambda src: "https://presigned/" + src,
    )
    assert args["url"] == "https://presigned/file-abc"


def test_no_source_file_leaves_arguments_untouched():
    param = _param(arguments={"foo": "{Begin@x}"})
    args = CodeExec._assemble_arguments(
        param, kwargs={}, get_var=lambda v: "resolved-x",
        resolve_source=lambda src: (_ for _ in ()).throw(AssertionError("resolver ne doit PAS être appelé")),
    )
    assert args == {"foo": "resolved-x"}
    assert "url" not in args


def test_custom_source_arg_name():
    param = _param(source_file="f", source_arg="data_url")
    args = CodeExec._assemble_arguments(
        param, kwargs={}, get_var=lambda v: None,
        resolve_source=lambda src: "u://" + src,
    )
    assert args == {"data_url": "u://f"}
