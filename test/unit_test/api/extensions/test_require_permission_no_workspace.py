"""Régression du bug 403-superuser-sans-workspace (découvert chantier get_file).

require_permission refusait AVANT d'atteindre le bypass superuser de
has_permission() dès que le contexte workspace manquait (pas de header
X-Workspace-Id) : un superuser prenait 403 sur toute route décorée.
Le fix ajoute _is_superuser_without_workspace() sur le chemin sans tenant.
Les non-superusers restent refusés à l'identique (fail closed).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api.apps.extensions import rbac
from api.apps.extensions.rbac import Permission, require_permission


def _protected():
    @require_permission(Permission.DOCUMENT_CREATE)
    async def handler(**kwargs):
        return "OK"
    return handler


@pytest.mark.asyncio
async def test_superuser_without_workspace_passes_and_is_audited():
    audit = MagicMock()
    user = SimpleNamespace(is_superuser=True, email="root@x")
    with patch.object(rbac, "_extract_user_id", return_value="u-super"), \
         patch.object(rbac, "_extract_tenant_id", return_value=None), \
         patch("api.db.services.user_service.UserService.get_by_id", return_value=(True, user)), \
         patch("api.db.services.audit_service.AuditService.record", audit):
        assert await _protected()() == "OK"
    assert audit.call_count == 1
    assert audit.call_args.kwargs["action"] == "SUPERUSER_BYPASS"
    assert audit.call_args.kwargs["resource_id"] == "(no-workspace-context)"


@pytest.mark.asyncio
async def test_regular_user_without_workspace_still_403():
    user = SimpleNamespace(is_superuser=False, email="user@x")
    with patch.object(rbac, "_extract_user_id", return_value="u-normal"), \
         patch.object(rbac, "_extract_tenant_id", return_value=None), \
         patch("api.db.services.user_service.UserService.get_by_id", return_value=(True, user)):
        resp = await _protected()()
    assert resp != "OK"  # enveloppe 403 get_json_result


@pytest.mark.asyncio
async def test_no_auth_context_still_403_without_user_lookup():
    lookup = MagicMock()
    with patch.object(rbac, "_extract_user_id", return_value=None), \
         patch.object(rbac, "_extract_tenant_id", return_value=None), \
         patch("api.db.services.user_service.UserService.get_by_id", lookup):
        resp = await _protected()()
    assert resp != "OK"
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_user_lookup_failure_fails_closed():
    with patch.object(rbac, "_extract_user_id", return_value="u-x"), \
         patch.object(rbac, "_extract_tenant_id", return_value=None), \
         patch("api.db.services.user_service.UserService.get_by_id", side_effect=RuntimeError("db down")):
        resp = await _protected()()
    assert resp != "OK"


@pytest.mark.asyncio
async def test_with_workspace_context_path_unchanged():
    # tenant présent → le chemin nominal has_permission est consulté, pas le bypass sans-workspace
    with patch.object(rbac, "_extract_user_id", return_value="u-1"), \
         patch.object(rbac, "_extract_tenant_id", return_value="t-1"), \
         patch.object(rbac, "has_permission", return_value=True) as hp:
        assert await _protected()() == "OK"
    hp.assert_called_once_with("u-1", "t-1", Permission.DOCUMENT_CREATE)
