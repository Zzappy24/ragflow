"""
api/apps/extensions/rbac_retriever.py
Transparent proxy on settings.retriever and settings.kg_retriever.
Filters kb_ids according to the current request's user_id (via quart.g).
Automatically covers all current and future calls to retriever.retrieval().
"""


class RBACRetrieverProxy:
    """
    Proxy around settings.retriever.
    - In HTTP context with g.rbac_user_id -> filters kb_ids by group
    - Outside HTTP context (background tasks, parsing) -> no filter
    - __getattr__ delegates everything to the real retriever
    """

    def __init__(self, real_retriever):
        object.__setattr__(self, "_real", real_retriever)

    async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids,
                        *args, **kwargs):
        kb_ids = self._apply_rbac_filter(tenant_ids, kb_ids)
        return await self._real.retrieval(
            question, embd_mdl, tenant_ids, kb_ids, *args, **kwargs
        )

    def _apply_rbac_filter(self, tenant_ids, kb_ids):
        user_id = _get_current_rbac_user()
        if not user_id:
            return kb_ids  # no HTTP context -> no filter
        tenant_id = tenant_ids[0] if tenant_ids else None
        if not tenant_id:
            return kb_ids
        from api.apps.extensions.rbac import filter_chat_dataset_ids
        return filter_chat_dataset_ids(user_id, tenant_id, kb_ids)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name, value):
        if name == "_real":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_real"), name, value)


def _get_current_rbac_user():
    """Get user_id from the current request context (request-scoped)."""
    try:
        from quart import g
        return getattr(g, "rbac_user_id", None)
    except RuntimeError:
        return None  # outside request context


def install_rbac_proxy():
    """
    Call ONCE at server startup, after settings.init().
    Replaces settings.retriever and settings.kg_retriever with proxies.
    """
    from common import settings
    if hasattr(settings, "retriever") and settings.retriever and not isinstance(settings.retriever, RBACRetrieverProxy):
        settings.retriever = RBACRetrieverProxy(settings.retriever)
    if hasattr(settings, "kg_retriever") and settings.kg_retriever and not isinstance(settings.kg_retriever, RBACRetrieverProxy):
        settings.kg_retriever = RBACRetrieverProxy(settings.kg_retriever)
