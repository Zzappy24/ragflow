"""
Quota checking for organisation resources.

Lives under ``api/db/services/`` rather than ``api/apps/extensions/`` because
the management admin panel imports it from a non-Flask context. Any import
under ``api.apps.*`` triggers ``api/apps/__init__.py`` which calls
``settings.init_settings()`` and tries to connect to Elasticsearch — the admin
server is an identity/RBAC service and must not require a doc-store to boot.
"""


def check_quota(org_id: str, resource_type: str) -> tuple[bool, str]:
    """
    Check if an org has exceeded its quota for a resource type.
    Returns (ok, message). If ok is False, the message explains why.
    """
    from api.db.services.org_service import OrgService

    e, org = OrgService.get_by_id(org_id)
    if not e or not org:
        return True, ""  # legacy / no org

    counts = OrgService.get_resource_counts(org_id)
    limits = {
        "user": (counts.get("users", 0), org.max_users),
        "workspace": (counts.get("workspaces", 0), org.max_workspaces),
        "dataset": (counts.get("datasets", 0), org.max_datasets),
        "document": (counts.get("documents", 0), org.max_documents),
    }
    if resource_type in limits:
        current, maximum = limits[resource_type]
        if current >= maximum:
            return False, f"Quota exceeded: {resource_type} ({current}/{maximum})"
    return True, ""
