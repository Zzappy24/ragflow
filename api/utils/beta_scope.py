"""CUSTOM B2B SaaS — portée des jetons « beta » (widgets et pages de partage publics).

Un jeton beta circule EN CLAIR dans l'URL d'un iframe ou d'une page partagée :
c'est une credential publique. Upstream l'authentifie comme l'utilisateur
technique du tenant, donc comme TOUT le workspace : n'importe quel visiteur
d'un site client pouvait télécharger n'importe quel document du workspace,
interroger n'importe quelle base, ou lancer n'importe quel agent — et, sans
vérification des identifiants fournis, ceux d'AUTRES tenants (audit
2026-09-06).

Règles appliquées par les routes ``AUTH_BETA`` (bot_api.py, sdk/doc.py) :
- toute ressource nommée par le client (dialog, agent, base, search app) doit
  appartenir au tenant du jeton ;
- un jeton frappé pour un dialog/agent/search précis (``APIToken.dialog_id``)
  ne sert que cet objet ;
- les lectures de documents/chunks sont limitées aux bases de l'objet lié
  quand il est connu (dialog ou search app) ; un jeton lié à un agent, ou non
  lié (héritage), reste borné au tenant.
"""
from quart import g


def beta_token():
    """Ligne APIToken du jeton beta de la requête (posée par _load_user), ou None."""
    return getattr(g, "beta_token", None)


def beta_bound_id(tok=None) -> str | None:
    tok = tok if tok is not None else beta_token()
    return (getattr(tok, "dialog_id", None) or None) if tok is not None else None


def beta_denies(resource_id: str, tok=None) -> bool:
    """True si le jeton beta est lié à un AUTRE objet que ``resource_id``."""
    bound = beta_bound_id(tok)
    return bool(bound) and bound != resource_id


def beta_allowed_kb_ids(tenant_id: str, tok=None) -> set[str] | None:
    """Bases lisibles via le jeton : celles du dialog ou de la search app liée.

    None = pas de restriction au-delà du tenant (jeton lié à un agent, ou non
    lié). set() vide = rien n'est lisible.
    """
    bound = beta_bound_id(tok)
    if not bound:
        return None
    from api.db.services.dialog_service import DialogService
    ok, dialog = DialogService.get_by_id(bound)
    if ok and dialog is not None and getattr(dialog, "tenant_id", None) == tenant_id:
        return set(dialog.kb_ids or [])
    from api.db.services.search_service import SearchService
    apps = SearchService.query(id=bound, tenant_id=tenant_id)
    if apps:
        return set(((apps[0].search_config or {}).get("kb_ids")) or [])
    return None


def pick_embed_token(rows, shared_id: str | None):
    """Clé dont le beta peut embarquer ``shared_id`` (bot, agent ou search app).

    Priorité : clé frappée pour cet objet, puis clé non liée (héritage). Une
    clé liée à un AUTRE objet n'est jamais retenue : ``beta_denies`` la
    refuserait au premier appel du widget. None = rien d'utilisable.
    """
    rows = list(rows or [])
    if shared_id:
        for row in rows:
            if (getattr(row, "dialog_id", None) or None) == shared_id:
                return row
    for row in rows:
        if not (getattr(row, "dialog_id", None) or None):
            return row
    return None


def kb_ids_owned_by(tenant_id: str, kb_ids) -> bool:
    """Toutes les bases demandées appartiennent au tenant (aucune n'est vide)."""
    from api.db.services.knowledgebase_service import KnowledgebaseService
    if isinstance(kb_ids, str):
        kb_ids = [kb_ids]
    for kb_id in kb_ids or []:
        if not kb_id or not KnowledgebaseService.query(tenant_id=tenant_id, id=kb_id):
            return False
    return True
