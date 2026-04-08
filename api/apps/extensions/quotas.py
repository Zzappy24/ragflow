"""
Thin compatibility shim — the real implementation lives in
``api/db/services/quota_service.py``. See that file for the rationale
(importing ``api.apps.*`` triggers ``init_settings()`` and forces an ES
connection, which the management admin panel cannot tolerate).
"""
from api.db.services.quota_service import check_quota  # noqa: F401
