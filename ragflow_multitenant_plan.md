# RAGFlow Multi-Tenant RBAC — Plan V3.1

## 1. Problématique

RAGFlow est le meilleur moteur RAG open source disponible en 2025 : parsing de
documents complexes (PDF avec tableaux, layouts, images), chunking configurable,
GraphRAG, reranking natif et UI complète. Licencié Apache 2.0.

### Ce qui existe déjà dans RAGFlow

RAGFlow n'est pas purement mono-tenant. Il possède déjà :

- `Tenant` distinct de `User` (même si `tenant_id == user_id` pour le owner)
- `UserTenant` avec `role` (OWNER, ADMIN, NORMAL, INVITE)
- `TenantPermission.ME` / `TenantPermission.TEAM` par ressource
- Pattern `accessible()` dans les services qui vérifie l'appartenance tenant
- Module `check_team_permission.py` qui valide l'accès KB par tenant
- Système d'invitation par email

### Ce qui manque pour un produit vendable

1. **Pas de hiérarchie Organisation → Workspace** : un "tenant" = un user
2. **Pas de rôles granulaires** : `ADMIN` existe dans l'enum mais n'est jamais utilisé
3. **Pas de groupes** : un membre voit TOUS les datasets "team" ou aucun.
   Pas de granularité équipe → dataset (ex: SAV ne voit que les KB SAV)
4. **Pas d'audit trail**
5. **Pas de gestion API keys par scope**
6. **Pas de quotas/limites**
7. **Inscription publique non contrôlable**
8. **Pas de panel d'administration**

## 2. Objectifs

### Objectif principal
Ajouter une couche RBAC complète (Organisation → Workspace → Groupe → Permission)
au-dessus du système tenant existant, **sans modifier le moteur RAG**
(Infinity, parser, chunking, retrieval).

### Objectifs business
- **Vendable** : multi-client avec isolation totale entre organisations
- **Granulaire** : au sein d'un workspace, des groupes (SAV, Commercial, Direction)
  voient des datasets différents
- **Self-service** : les admins gèrent leurs workspaces et membres sans super-admin
- **Auditable** : chaque action sensible est tracée (SOC2, RGPD)
- **Limitable** : quotas par org → plans tarifaires
- **Extensible** : rôles custom prévus en V2

### Non-objectifs (hors scope V1)
- Modifier Infinity, le parser, le chunking ou le retrieval
- Isolation CPU/RAM par tenant
- SSO / SAML / OIDC (prévu V2)
- Billing automatique / Stripe (prévu V2)
- Rôles 100% custom avec éditeur de permissions (V2)
- Isolation physique MinIO (bucket-per-org) — V2, pour clients bancaires/défense/santé
- Optimisation `IN` clause → `JOIN` pour >10 000 datasets par workspace (V2 scaling)

## 3. Modèle RBAC

### Hiérarchie

```
Super Admin (is_superuser=True)
  └── Organisation (= client payant : Bodemer, Acme Corp, ...)
        ├── Org Admin (gère l'org, les workspaces, les membres)
        └── Workspace (= un Tenant RAGFlow = un projet / cas d'usage)
              ├── WS Admin (gère datasets, chats, membres, groupes)
              ├── WS Editor (CRUD datasets, documents, chats)
              ├── WS Viewer (lecture seule, utilisation chats)
              ├── Groupes (SAV, Commercial, Direction...)
              │     └── filtrent QUELS datasets sont visibles
              └── Ressources : Datasets, Documents, Chats, Agents
```

### Deux axes de contrôle orthogonaux

Le RBAC repose sur deux questions distinctes :

1. **"Que peut-il FAIRE ?"** → le rôle (ws_admin / editor / viewer)
   - Géré par `ws_member.role` + matrice `ROLE_PERMISSIONS`
   - Décorateur `@require_permission(Permission.DATASET_CREATE)`

2. **"Quels datasets peut-il VOIR ?"** → les groupes
   - Géré par `ws_group` + `ws_group_member` + `ws_group_dataset`
   - Fonction `get_visible_dataset_ids(user_id, workspace_id)`
   - Un user sans groupe → voit tout (admin / rétrocompatibilité)

### Matrice rôles → permissions

| Permission             | Super Admin | Org Admin | WS Admin | WS Editor | WS Viewer |
|------------------------|:-----------:|:---------:|:--------:|:---------:|:---------:|
| Créer/supprimer orgs   | x           |           |          |           |           |
| Gérer quotas orgs      | x           |           |          |           |           |
| Voir toutes les orgs   | x           |           |          |           |           |
| Gérer membres org      | x           | x         |          |           |           |
| Créer/supprimer WS     | x           | x         |          |           |           |
| Config LLM org         | x           | x         |          |           |           |
| Gérer membres WS       | x           | x         | x        |           |           |
| Gérer groupes          | x           | x         | x        |           |           |
| CRUD datasets          | x           | x         | x        | x         |           |
| Upload documents       | x           | x         | x        | x         |           |
| Supprimer documents    | x           | x         | x        | x         |           |
| CRUD chats/agents      | x           | x         | x        | x         |           |
| Utiliser chats         | x           | x         | x        | x         | x         |
| Voir datasets*         | x           | x         | x        | x         | x         |
| Voir documents*        | x           | x         | x        | x         | x         |
| Voir audit log         | x           | x         | x        |           |           |

*\*Filtré par les groupes : un viewer SAV ne voit que les datasets SAV*

### Exemple concret : Bodemer

```
Organisation "Bodemer Auto"
  └── Workspace "Bodemer RAG" (= 1 Tenant RAGFlow)
        ├── Groupe "SAV"
        │     ├── Membres : Jean, Marie
        │     └── Datasets : KB-Garanties, KB-Procédures-SAV
        ├── Groupe "Commercial"
        │     ├── Membres : Paul, Sophie
        │     └── Datasets : KB-Tarifs, KB-Fiches-Véhicules
        └── Groupe "Direction"
              ├── Membres : Pierre (aussi dans SAV)
              └── Datasets : KB-Reporting, KB-Garanties (partagé avec SAV)

Résultat :
- Jean (SAV, editor) → voit KB-Garanties + KB-Procédures-SAV, peut créer/modifier
- Paul (Commercial, viewer) → voit KB-Tarifs + KB-Fiches-Véhicules, lecture seule
- Pierre (SAV + Direction, ws_admin) → voit les 3 groupes réunis
- Admin sans groupe → voit tout
```

### Mapping sur le modèle RAGFlow existant

**Principe clé : un Workspace = un Tenant RAGFlow.** On ne casse rien, on enveloppe.

```
Organisation (NOUVEAU)
  └── Workspace (NOUVEAU, pointe vers Tenant RAGFlow existant)
        ├── ws_member (NOUVEAU, source de vérité RBAC — remplace UserTenant.role)
        └── ws_group → ws_group_dataset (NOUVEAU, filtrage dataset-level)
```

**Règle critique (correction Gemini #1)** : on ne touche PAS à `UserTenant.role`.
RAGFlow fait des comparaisons strictes (`role == OWNER`, `role == NORMAL`) dans ses
requêtes service. Injecter des valeurs custom casserait `get_joined_tenants_by_user_id()`
et `get_info_by()`. La table `ws_member` est notre seule source de vérité RBAC.
`UserTenant` reste en `NORMAL` pour tous les membres non-owner.

**Règle critique (correction Gemini #2)** : la création de tokens API natifs depuis
l'UI RAGFlow est bloquée pour les non-superusers. Tous les tokens passent par le
panel admin avec scoping. Le `@token_required` natif est enrichi pour vérifier
`api_key_scope`.

## 4. Modèle de données

### Nouvelles tables (9 au total)

```sql
-- ============================================================
-- ORGANISATION : client payant, regroupe des workspaces
-- ============================================================
CREATE TABLE organisation (
    id              VARCHAR(32) PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(255) NOT NULL UNIQUE,
    logo            TEXT,
    status          VARCHAR(1) NOT NULL DEFAULT '1',
    max_users       INT NOT NULL DEFAULT 50,
    max_workspaces  INT NOT NULL DEFAULT 10,
    max_datasets    INT NOT NULL DEFAULT 100,
    max_documents   INT NOT NULL DEFAULT 10000,
    max_storage_gb  INT NOT NULL DEFAULT 100,
    llm_config      JSON,
    settings        JSON,
    created_by      VARCHAR(32) NOT NULL,
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    update_time     DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    INDEX idx_org_slug (slug),
    INDEX idx_org_status (status)
);

-- ============================================================
-- MEMBERSHIP ORG : lien user → organisation
-- ============================================================
CREATE TABLE org_member (
    id              VARCHAR(32) PRIMARY KEY,
    org_id          VARCHAR(32) NOT NULL,
    user_id         VARCHAR(32) NOT NULL,
    role            VARCHAR(16) NOT NULL DEFAULT 'member',  -- org_admin | member
    status          VARCHAR(1) NOT NULL DEFAULT '1',
    invited_by      VARCHAR(32),
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    update_time     DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    UNIQUE INDEX idx_om_org_user (org_id, user_id),
    INDEX idx_om_user (user_id)
);

-- ============================================================
-- WORKSPACE : enveloppe un Tenant RAGFlow existant
-- ============================================================
CREATE TABLE workspace (
    id              VARCHAR(32) PRIMARY KEY,
    org_id          VARCHAR(32) NOT NULL,
    tenant_id       VARCHAR(32) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    status          VARCHAR(1) NOT NULL DEFAULT '1',
    settings        JSON,
    created_by      VARCHAR(32) NOT NULL,
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    update_time     DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    INDEX idx_ws_org (org_id),
    UNIQUE INDEX idx_ws_tenant (tenant_id)
);

-- ============================================================
-- MEMBERSHIP WORKSPACE : source de vérité RBAC
-- (on ne modifie PAS UserTenant.role — cf. correction Gemini)
-- ============================================================
CREATE TABLE ws_member (
    id              VARCHAR(32) PRIMARY KEY,
    workspace_id    VARCHAR(32) NOT NULL,
    user_id         VARCHAR(32) NOT NULL,
    role            VARCHAR(16) NOT NULL DEFAULT 'viewer',  -- ws_admin | editor | viewer
    status          VARCHAR(1) NOT NULL DEFAULT '1',
    invited_by      VARCHAR(32),
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    update_time     DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    UNIQUE INDEX idx_wm_ws_user (workspace_id, user_id),
    INDEX idx_wm_user (user_id)
);

-- ============================================================
-- GROUPES : regroupement logique d'utilisateurs dans un workspace
-- ============================================================
CREATE TABLE ws_group (
    id              VARCHAR(32) PRIMARY KEY,
    workspace_id    VARCHAR(32) NOT NULL,
    name            VARCHAR(64) NOT NULL,
    description     VARCHAR(255),
    status          VARCHAR(1) NOT NULL DEFAULT '1',
    created_by      VARCHAR(32) NOT NULL,
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    UNIQUE INDEX idx_wg_ws_name (workspace_id, name),
    INDEX idx_wg_ws (workspace_id)
);

-- ============================================================
-- MEMBERSHIP GROUPE : lien user → groupe(s)
-- Un user peut être dans plusieurs groupes
-- ============================================================
CREATE TABLE ws_group_member (
    id              VARCHAR(32) PRIMARY KEY,
    group_id        VARCHAR(32) NOT NULL,
    user_id         VARCHAR(32) NOT NULL,
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    UNIQUE INDEX idx_wgm_group_user (group_id, user_id),
    INDEX idx_wgm_user (user_id)
);

-- ============================================================
-- MAPPING GROUPE → DATASETS : quels datasets un groupe peut voir
-- ============================================================
CREATE TABLE ws_group_dataset (
    id              VARCHAR(32) PRIMARY KEY,
    group_id        VARCHAR(32) NOT NULL,
    dataset_id      VARCHAR(32) NOT NULL,
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    UNIQUE INDEX idx_wgd_group_ds (group_id, dataset_id),
    INDEX idx_wgd_dataset (dataset_id)
);

-- ============================================================
-- AUDIT LOG
-- ============================================================
CREATE TABLE audit_log (
    id              VARCHAR(32) PRIMARY KEY,
    org_id          VARCHAR(32),
    workspace_id    VARCHAR(32),
    user_id         VARCHAR(32) NOT NULL,
    action          VARCHAR(64) NOT NULL,
    resource_type   VARCHAR(32),
    resource_id     VARCHAR(32),
    details         JSON,
    ip_address      VARCHAR(45),
    user_agent      VARCHAR(512),
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    INDEX idx_al_org (org_id, create_time),
    INDEX idx_al_ws (workspace_id, create_time),
    INDEX idx_al_user (user_id, create_time),
    INDEX idx_al_action (action)
);

-- ============================================================
-- API KEY SCOPING
-- ============================================================
CREATE TABLE api_key_scope (
    id              VARCHAR(32) PRIMARY KEY,
    token           VARCHAR(255) NOT NULL,
    workspace_id    VARCHAR(32) NOT NULL,
    permissions     JSON NOT NULL,
    name            VARCHAR(255),
    expires_at      DATETIME,
    last_used_at    DATETIME,
    status          VARCHAR(1) NOT NULL DEFAULT '1',
    created_by      VARCHAR(32) NOT NULL,
    create_time     DATETIME NOT NULL DEFAULT NOW(),
    UNIQUE INDEX idx_aks_token (token),
    INDEX idx_aks_ws (workspace_id)
);
```

### Tables RAGFlow existantes : AUCUNE modification de schéma

```
UserTenant.role reste OWNER | NORMAL | INVITE (pas de nouvelles valeurs)
Tous les membres workspace sont mis en UserTenant.role = NORMAL
Le rôle réel est lu depuis ws_member.role (notre table)
```

### Diagramme de relations

```
                    ┌──────────────┐
                    │  Super Admin │  (is_superuser=true)
                    └──────┬───────┘
                           │
          ┌────────────────▼────────────────┐
          │          Organisation            │
          │  name, slug, quotas, llm_config │
          └──┬──────────────────────────┬───┘
             │                          │
    ┌────────▼────────┐       ┌────────▼────────┐
    │   OrgMember     │       │   Workspace     │
    │ (org_admin |    │       │ org_id          │
    │  member)        │       │ tenant_id ──────┼──→ Tenant RAGFlow
    └─────────────────┘       └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │   WsMember      │
                              │ role: ws_admin  │
                              │   | editor      │
                              │   | viewer      │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │   WsGroup       │
                              │ "SAV"           │
                              │ "Commercial"    │
                              └──┬──────────┬───┘
                                 │          │
                    ┌────────────▼┐    ┌───▼─────────────┐
                    │ GroupMember │    │ GroupDataset     │
                    │ user → group│    │ group → dataset  │
                    └─────────────┘    └─────────────────┘

Axe 1 (RBAC) : WsMember.role → "que peut-il FAIRE ?"
Axe 2 (Scope) : WsGroup → GroupDataset → "quels datasets peut-il VOIR ?"
```

## 5. Architecture technique

**Base de fork : commit `60ec5880e`** (main au 2026-04-04, "Feat: mysql data migrate script #13927").
Pas v0.24.0 — depuis cette version, 395 commits ont atterri dont un refactoring
majeur des routes SDK → RESTful API. Les fichiers `api/apps/sdk/chat.py` et
`api/apps/sdk/dataset.py` ont été supprimés et remplacés par
`api/apps/restful_apis/chat_api.py` et `dataset_api.py`. Partir de v0.24.0
forcerait un rebase complet au premier merge upstream.

```
┌─────────────────────────────────────────────────────────────┐
│                  Panel Admin (séparé)                        │
│            FastAPI + Vue 3 + Tailwind — port 8080            │
│                                                             │
│  Super Admin: Orgs, Quotas, System                          │
│  Org Admin: Workspaces, Membres, LLM Config                 │
│  WS Admin: Membres, Groupes, Dataset↔Groupe, Audit          │
└──────────────────────────┬──────────────────────────────────┘
                           │ appels API internes
┌──────────────────────────▼──────────────────────────────────┐
│                    RAGFlow (fork de main HEAD)               │
│              Python/Quart  —  port 9380                      │
│                                                             │
│  Fichiers modifiés :                                        │
│  ┌────────────────────────────────────────────────────┐     │
│  │  api/apps/extensions/                    NOUVEAU   │     │
│  │    ├── rbac.py         (RBAC : rôles + groupes)    │     │
│  │    ├── rbac_retriever.py (proxy retriever RBAC)    │     │
│  │    ├── audit.py        (logging d'audit)           │     │
│  │    └── quotas.py       (vérification quotas)       │     │
│  │                                                    │     │
│  │  api/db/                                           │     │
│  │    ├── db_models.py    MODIFIÉ (+9 modèles)        │     │
│  │    └── services/                                   │     │
│  │        ├── org_service.py              NOUVEAU     │     │
│  │        ├── workspace_service.py        NOUVEAU     │     │
│  │        ├── group_service.py            NOUVEAU     │     │
│  │        └── audit_service.py            NOUVEAU     │     │
│  │                                                    │     │
│  │  api/apps/restful_apis/          (ex-sdk/, refactoré)    │
│  │    ├── chat_api.py     MODIFIÉ (~5 lignes)         │     │
│  │    ├── dataset_api.py  MODIFIÉ (~8 lignes)         │     │
│  │    ├── file_api.py     MODIFIÉ (~5 lignes)         │     │
│  │    └── search_api.py   MODIFIÉ (~5 lignes)         │     │
│  │                                                    │     │
│  │  api/apps/sdk/         (routes legacy encore actives)    │
│  │    ├── doc.py          MODIFIÉ (~10 lignes)        │     │
│  │    ├── session.py      MODIFIÉ (~5 lignes)         │     │
│  │    └── agents.py       MODIFIÉ (~5 lignes)         │     │
│  │                                                    │     │
│  │  api/apps/                                         │     │
│  │    ├── kb_app.py       MODIFIÉ (~5 lignes)         │     │
│  │    ├── document_app.py MODIFIÉ (~15 lignes) ← +auth│     │
│  │    ├── canvas_app.py   MODIFIÉ (~5 lignes)         │     │
│  │    ├── chunk_app.py    MODIFIÉ (~5 lignes)         │     │
│  │    ├── api_app.py      MODIFIÉ (bloquer tokens)    │     │
│  │    └── user_app.py     MODIFIÉ (signup toggle)     │     │
│  │                                                    │     │
│  │  api/utils/api_utils.py MODIFIÉ (~20 lignes)       │     │
│  └────────────────────────────────────────────────────┘     │
│                                                             │
│  INCHANGÉ : moteur RAG, Infinity, parsers, frontend React   │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
    MySQL              Infinity           MinIO/S3
  (+9 tables)      (INCHANGÉ)           (INCHANGÉ)
```

## 6. Module RBAC central — `api/apps/extensions/rbac.py`

Ce fichier contient **toute** la logique de contrôle d'accès (rôles ET groupes).
Les autres fichiers ne font qu'importer depuis ici.

```python
"""
api/apps/extensions/rbac.py
Module central RBAC — rôles + filtrage dataset par groupe.
"""
from enum import Enum
from functools import wraps
from api.utils.api_utils import get_json_result

# ── Rôles ──────────────────────────────────────────────────

class OrgRole(str, Enum):
    ORG_ADMIN = "org_admin"
    MEMBER = "member"

class WsRole(str, Enum):
    WS_ADMIN = "ws_admin"
    EDITOR = "editor"
    VIEWER = "viewer"

# ── Permissions (axe 1 : que peut-il FAIRE ?) ─────────────

class Permission(str, Enum):
    DATASET_CREATE = "dataset.create"
    DATASET_READ = "dataset.read"
    DATASET_UPDATE = "dataset.update"
    DATASET_DELETE = "dataset.delete"
    DOCUMENT_CREATE = "document.create"
    DOCUMENT_READ = "document.read"
    DOCUMENT_DELETE = "document.delete"
    CHAT_CREATE = "chat.create"
    CHAT_READ = "chat.read"
    CHAT_UPDATE = "chat.update"
    CHAT_DELETE = "chat.delete"
    CHAT_USE = "chat.use"
    AGENT_CREATE = "agent.create"
    AGENT_READ = "agent.read"
    AGENT_UPDATE = "agent.update"
    AGENT_DELETE = "agent.delete"
    MEMBER_INVITE = "member.invite"
    MEMBER_REMOVE = "member.remove"
    MEMBER_LIST = "member.list"
    GROUP_MANAGE = "group.manage"
    AUDIT_READ = "audit.read"

ROLE_PERMISSIONS = {
    WsRole.VIEWER: {
        Permission.DATASET_READ, Permission.DOCUMENT_READ,
        Permission.CHAT_READ, Permission.CHAT_USE, Permission.AGENT_READ,
    },
    WsRole.EDITOR: {
        Permission.DATASET_CREATE, Permission.DATASET_READ,
        Permission.DATASET_UPDATE, Permission.DATASET_DELETE,
        Permission.DOCUMENT_CREATE, Permission.DOCUMENT_READ,
        Permission.DOCUMENT_DELETE,
        Permission.CHAT_CREATE, Permission.CHAT_READ,
        Permission.CHAT_UPDATE, Permission.CHAT_DELETE, Permission.CHAT_USE,
        Permission.AGENT_CREATE, Permission.AGENT_READ,
        Permission.AGENT_UPDATE, Permission.AGENT_DELETE,
    },
    WsRole.WS_ADMIN: set(Permission),  # toutes les permissions
}

# ── Résolution de contexte ─────────────────────────────────

def resolve_workspace_from_tenant(tenant_id: str):
    """Résout le workspace à partir d'un tenant_id RAGFlow."""
    from api.db.services.workspace_service import WorkspaceService
    return WorkspaceService.get_by_tenant_id(tenant_id)

def get_user_ws_role(user_id: str, workspace_id: str) -> WsRole | None:
    """Retourne le rôle WS, ou None si pas membre."""
    from api.db.services.workspace_service import WsMemberService
    member = WsMemberService.get_membership(workspace_id, user_id)
    if not member:
        return None
    return WsRole(member.role)

def get_user_org_role(user_id: str, org_id: str) -> OrgRole | None:
    """Retourne le rôle Org, ou None si pas membre."""
    from api.db.services.org_service import OrgMemberService
    member = OrgMemberService.get_membership(org_id, user_id)
    if not member:
        return None
    return OrgRole(member.role)

# ── Axe 1 : has_permission (que peut-il FAIRE ?) ──────────

def has_permission(user_id: str, tenant_id: str, permission: Permission) -> bool:
    """
    1. Super admin → True
    2. Pas de workspace trouvé → mode legacy (tenant_id == user_id)
    3. Org admin de l'org du workspace → True
    4. Sinon → vérifie ws_member.role dans la matrice
    """
    from api.db.services.user_service import UserService
    user = UserService.filter_by_id(user_id)
    if user and user.is_superuser:
        return True

    workspace = resolve_workspace_from_tenant(tenant_id)
    if not workspace:
        return tenant_id == user_id  # mode legacy

    org_role = get_user_org_role(user_id, workspace.org_id)
    if org_role == OrgRole.ORG_ADMIN:
        return True

    ws_role = get_user_ws_role(user_id, workspace.id)
    if not ws_role:
        return False

    return permission in ROLE_PERMISSIONS.get(ws_role, set())

# ── Axe 2 : get_visible_dataset_ids (quels datasets ?) ────

def get_visible_dataset_ids(user_id: str, tenant_id: str) -> set[str] | None:
    """
    Retourne les dataset_ids visibles pour un user, ou None si pas de filtre
    (= voit tout : admin, org_admin, user sans groupe, mode legacy).
    """
    from api.db.services.user_service import UserService
    user = UserService.filter_by_id(user_id)
    if user and user.is_superuser:
        return None  # voit tout

    workspace = resolve_workspace_from_tenant(tenant_id)
    if not workspace:
        return None  # mode legacy, pas de filtre

    org_role = get_user_org_role(user_id, workspace.org_id)
    if org_role == OrgRole.ORG_ADMIN:
        return None  # voit tout

    ws_role = get_user_ws_role(user_id, workspace.id)
    if ws_role == WsRole.WS_ADMIN:
        return None  # voit tout

    # Récupérer les groupes du user dans ce workspace
    from api.db.services.group_service import GroupService
    groups = GroupService.get_user_groups(workspace.id, user_id)

    if not groups:
        return None  # pas de groupe = pas de filtre (rétrocompat)

    # Union des datasets de tous les groupes du user
    group_ids = [g.id for g in groups]
    return GroupService.get_datasets_for_groups(group_ids)

def assert_dataset_access(user_id: str, tenant_id: str, dataset_ids: list[str]):
    """
    Lève 403 si un dataset_id n'est pas visible pour le user.
    Utilisé dans les opérations de CRÉATION (chat.create, agent.create)
    où le user choisit explicitement les datasets à référencer.
    """
    visible = get_visible_dataset_ids(user_id, tenant_id)
    if visible is None:
        return  # pas de filtre
    forbidden = set(dataset_ids) - visible
    if forbidden:
        from werkzeug.exceptions import Forbidden
        raise Forbidden(f"Access denied to datasets: {forbidden}")

def filter_chat_dataset_ids(user_id: str, tenant_id: str, chat_dataset_ids: list[str]) -> list[str]:
    """
    Filtre les dataset_ids d'un chat/agent aux seuls visibles par le user.
    NE LÈVE PAS D'ERREUR — retourne un sous-ensemble.
    
    Utilisé au RUNTIME (completions, retrieval) pour permettre aux agents
    cross-groupes de fonctionner. Le user ne voit que les résultats des
    datasets auxquels il a accès via ses groupes.
    
    Exemple : Agent "Global" → datasets [SAV, Commercial, Direction]
    - User groupe SAV → retrieval sur [SAV] uniquement
    - User groupe Direction → retrieval sur [Direction] uniquement
    - WS Admin (pas de filtre) → retrieval sur tout
    """
    visible = get_visible_dataset_ids(user_id, tenant_id)
    if visible is None:
        return chat_dataset_ids  # pas de filtre
    return [did for did in chat_dataset_ids if did in visible]

# ── Décorateurs ────────────────────────────────────────────

def require_permission(permission: Permission):
    """
    S'utilise APRÈS @token_required ou @login_required.
    Vérifie le rôle (axe 1). Le filtrage dataset (axe 2) est fait
    dans la logique métier via filter_datasets() / assert_dataset_access().
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tenant_id = kwargs.get("tenant_id")
            user_id = _extract_user_id(kwargs)
            if not has_permission(user_id, tenant_id, permission):
                return get_json_result(
                    data=False, message=f"Permission denied: {permission.value}",
                    code=403
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator

def require_org_admin(func):
    """Décorateur pour les routes admin org."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        org_id = kwargs.get("org_id")
        user_id = _extract_user_id(kwargs)
        from api.db.services.user_service import UserService
        user = UserService.filter_by_id(user_id)
        if user and user.is_superuser:
            return func(*args, **kwargs)
        role = get_user_org_role(user_id, org_id)
        if role != OrgRole.ORG_ADMIN:
            return get_json_result(data=False, message="Org admin required", code=403)
        return func(*args, **kwargs)
    return wrapper

def _extract_user_id(kwargs):
    from api.apps import current_user
    if current_user and hasattr(current_user, 'id'):
        return current_user.id
    return kwargs.get("user_id")
```

## 7. Audit de surface d'attaque

Avant de lister les modifications, il faut comprendre **tous** les chemins d'accès
aux données dans RAGFlow. Le RBAC ne sert à rien s'il ne couvre pas tous les entry points.

### 7.1 Routes sans authentification (existantes dans RAGFlow upstream)

| Route | Fichier | Méthodes | Risque |
|---|---|---|---|
| `/webhook/<agent_id>` | `sdk/agents.py:156` | ALL | **CRITIQUE** — exécution d'agents sans auth |
| `/webhook_test/<agent_id>` | `sdk/agents.py:157` | ALL | **CRITIQUE** — idem + trace |
| `/webhook_trace/<agent_id>` | `sdk/agents.py:835` | GET | **HIGH** — logs d'exécution exposés |
| `/image/<image_id>` | `document_app.py:818` | GET | **CRITIQUE** — `@login_required` commenté |
| `/upload/<canvas_id>` | `canvas_app.py:375` | POST | **HIGH** — upload fichiers cross-tenant |
| `/trace` | `canvas_app.py:632` | GET | **HIGH** — logs canvas sans auth |
| `/test_mcp` | `mcp_server_app.py:401` | POST | **HIGH** — SSRF via test URL arbitraire |

**Action Sprint 2** : ajouter `@login_required` + vérification tenant sur chacune.

### 7.2 Le pipeline de retrieval ne connaît pas le user

La chaîne d'appel du retrieval :

```
Route API (@require_permission ✓)
  → async_chat()                    (dialog_service.py)
    → retriever.retrieval()         (rag/nlp/search.py:365)
      → Elasticsearch/Infinity      (aucun contexte user)

Route API (@require_permission ✓)
  → Canvas.run()                    (agent/canvas.py)
    → Retrieval component           (agent/tools/retrieval.py:88)
      → retriever.retrieval()       (rag/nlp/search.py:365)
        → Elasticsearch/Infinity    (aucun contexte user)
```

`retriever.retrieval()` reçoit `tenant_ids` et `kb_ids` mais **jamais `user_id`**.
Il y a **18 appels** à `retriever.retrieval()` / `kg_retriever.retrieval()` dans
**7 fichiers** différents. Modifier chaque appel individuellement est fragile :
si RAGFlow ajoute un 19ème appel dans une future version, le RBAC est bypassé.

**Solution : Proxy Pattern sur `settings.retriever`**

Au lieu de modifier les 18 appels, on interpose un proxy transparent entre tous
les appelants et le retriever. Le proxy filtre les `kb_ids` selon le user_id du
contexte de requête courant (via `quart.g`). Un seul fichier nouveau, une seule
ligne d'init. Tout appel futur à `retriever.retrieval()` est automatiquement filtré.

```
AVANT :
  appelant → settings.retriever.retrieval(kb_ids=[...])

APRÈS :
  appelant → RBACRetrieverProxy.retrieval(kb_ids=[...])
                 ↓ filtre kb_ids via g.rbac_user_id
             settings.retriever.retrieval(kb_ids=[filtrés])
```

Le proxy est transparent : `__getattr__` délègue tout au vrai retriever.
Les tâches de fond (parsing, indexation) n'ont pas de contexte de requête,
donc `g.rbac_user_id` est None → pas de filtre → comportement inchangé.

**Fichiers appelant retriever.retrieval() (18 appels, tous couverts par le proxy)** :
- `dialog_service.py` — 4 appels (async_chat + fonctions search)
- `agent/tools/retrieval.py` — 3 appels (composant Retrieval du canvas)
- `sdk/session.py` — 2 appels (completions)
- `sdk/doc.py` — 2 appels (route /retrieval)
- `sdk/dify_retrieval.py` — 2 appels (intégration Dify)
- `chunk_app.py` — 2 appels (recherche chunks)
- `rag/benchmark.py` — 1 appel (benchmark, non critique)

### 7.3 Endpoints "bot" publics (hors scope RBAC)

Ces routes sont **intentionnellement publiques** — conçues pour être embedées
(widget chatbot sur un site, API tiers) :

```
POST /chatbots/<dialog_id>/completions      # session.py — @apikey_required
POST /agentbots/<agent_id>/completions      # session.py — @apikey_required
POST /searchbots/ask                         # session.py — @apikey_required
POST /dify/retrieval                         # dify_retrieval.py — @apikey_required
```

Elles utilisent un API token "beta" lié à un dialog/agent spécifique.
Le RBAC ne s'applique pas : c'est le **propriétaire du bot** qui décide
quels datasets exposer en créant le token.

**Risque résiduel** : si un API key est compromis, il donne accès à tout le
tenant via le dialog/agent référencé. La table `api_key_scope` atténue ce risque
en limitant les permissions et en ajoutant une expiration.

**Action** : documenter dans le panel admin que ces endpoints sont publics et
que les API keys doivent être traitées comme des secrets.

### 7.4 Récapitulatif des couches de protection

```
Couche 1 — Auth         : @login_required / @token_required (existe)
Couche 2 — RBAC rôle    : @require_permission (NOUVEAU, décorateur route)
Couche 3 — RBAC dataset : RBACRetrieverProxy (NOUVEAU, proxy sur settings.retriever)
                           Filtre les kb_ids de TOUT appel retrieval selon g.rbac_user_id
Couche 4 — Quota        : check_quota() (NOUVEAU, dans routes de création)
Couche 5 — Audit        : audit_log() (NOUVEAU, décorateur route)

rag/nlp/search.py est INCHANGÉ. dialog_service.py est INCHANGÉ.
agent/tools/retrieval.py est INCHANGÉ. Le proxy intercepte tout.
```

## 8. Modifications dans les fichiers RAGFlow existants

> **Note** : depuis v0.24.0, les routes SDK ont été refactorées.
> `api/apps/sdk/chat.py` et `dataset.py` ont été remplacés par
> `api/apps/restful_apis/chat_api.py` et `dataset_api.py`.
> Les routes utilisent maintenant `@login_required` + `@add_tenant_id_to_kwargs`
> au lieu de `@token_required`.

Chaque modification est chirurgicale. Trois types d'injection :

### Type A : Décorateur `@require_permission` (axe 1 — rôle)

```python
# Exemple : api/apps/restful_apis/chat_api.py
from api.apps.extensions.rbac import require_permission, Permission  # ← ajout

@manager.route("/chats", methods=["POST"])
@login_required
@require_permission(Permission.CHAT_CREATE)  # ← ajout (1 ligne)
async def create():
    ...  # code existant inchangé
```

Appliqué sur :
- `api/apps/restful_apis/chat_api.py` — toutes les routes CRUD chats
- `api/apps/restful_apis/dataset_api.py` — toutes les routes CRUD datasets
- `api/apps/restful_apis/file_api.py` — routes fichiers
- `api/apps/restful_apis/search_api.py` — routes recherche
- `api/apps/sdk/doc.py` — routes documents (upload, delete, chunks)
- `api/apps/sdk/session.py` — routes sessions/completions
- `api/apps/sdk/agents.py` — routes agents
- `api/apps/kb_app.py` — routes KB legacy
- `api/apps/document_app.py` — routes documents legacy
- `api/apps/canvas_app.py` — routes canvas/agents legacy
- `api/apps/chunk_app.py` — routes chunks legacy

### Type B : Filtrage SQL dataset par groupe (axe 2 — groupes)

**Correction critique #1** : le filtrage se fait EN SQL, pas en Python post-query.

```python
# Exemple : api/apps/restful_apis/dataset_api.py — listing
from api.apps.extensions.rbac import (
    require_permission, Permission, get_visible_dataset_ids, _extract_user_id
)

@manager.route("/datasets", methods=["GET"])
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_READ)
async def list_datasets(tenant_id: str = None):
    ...
    # ← AJOUT : injecter le filtre dans la requête SQL
    visible_ids = get_visible_dataset_ids(_extract_user_id({}), tenant_id)
    if visible_ids is not None:
        # Note scaling V2 : si >10 000 datasets, remplacer par JOIN sur ws_group_dataset.
        # Pour V1, IN clause MySQL gère sans problème jusqu'à ~65 000 paramètres.
        kbs = KnowledgebaseService.query(tenant_id=tenant_id, id__in=visible_ids, ...)
    else:
        kbs = KnowledgebaseService.query(tenant_id=tenant_id, ...)
    ...
```

### Type C : Proxy Retriever — filtrage automatique de TOUS les appels retrieval

**Correction critique #2** : un agent qui référence des datasets de plusieurs
groupes ne doit PAS être bloqué par un 403. Au lieu de ça, on filtre les
`kb_ids` au runtime du retrieval — le user ne voit que les résultats des
datasets auxquels il a accès.

Il y a **18 appels** à `retriever.retrieval()` dans 7 fichiers. Plutôt que de
les modifier un par un (fragile — un 19ème appel dans un futur merge bypasse
tout), on interpose un **proxy transparent** sur `settings.retriever`.

**Nouveau fichier : `api/apps/extensions/rbac_retriever.py`**

```python
"""
api/apps/extensions/rbac_retriever.py
Proxy transparent sur settings.retriever et settings.kg_retriever.
Filtre les kb_ids selon le user_id du contexte de requête courant.
Couvre automatiquement tout appel présent et futur à retriever.retrieval().
"""

class RBACRetrieverProxy:
    """
    Proxy autour de settings.retriever.
    - En contexte HTTP avec g.rbac_user_id → filtre les kb_ids par groupe
    - Hors contexte HTTP (tâches de fond, parsing) → pas de filtre
    - __getattr__ délègue tout au vrai retriever (retrieval_by_toc, etc.)
    """
    def __init__(self, real_retriever):
        self._real = real_retriever

    async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids,
                        *args, **kwargs):
        kb_ids = self._apply_rbac_filter(tenant_ids, kb_ids)
        return await self._real.retrieval(
            question, embd_mdl, tenant_ids, kb_ids, *args, **kwargs
        )

    def _apply_rbac_filter(self, tenant_ids, kb_ids):
        user_id = _get_current_rbac_user()
        if not user_id:
            return kb_ids  # pas de contexte HTTP → pas de filtre
        tenant_id = tenant_ids[0] if tenant_ids else None
        if not tenant_id:
            return kb_ids
        from api.apps.extensions.rbac import filter_chat_dataset_ids
        return filter_chat_dataset_ids(user_id, tenant_id, kb_ids)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _get_current_rbac_user():
    """Récupère le user_id du contexte de requête courant (request-scoped)."""
    try:
        from quart import g
        return getattr(g, 'rbac_user_id', None)
    except RuntimeError:
        return None  # hors contexte de requête


def install_rbac_proxy():
    """
    À appeler UNE FOIS au démarrage du serveur, après settings.init().
    Remplace settings.retriever et settings.kg_retriever par des proxies.
    """
    from common import settings
    if not isinstance(settings.retriever, RBACRetrieverProxy):
        settings.retriever = RBACRetrieverProxy(settings.retriever)
    if settings.kg_retriever and not isinstance(settings.kg_retriever, RBACRetrieverProxy):
        settings.kg_retriever = RBACRetrieverProxy(settings.kg_retriever)
```

**Installation (1 ligne dans le démarrage serveur)** :

```python
# Dans api/ragflow_server.py ou équivalent, après settings.init()
from api.apps.extensions.rbac_retriever import install_rbac_proxy
install_rbac_proxy()
```

**Injection du user_id dans le contexte de requête (dans le middleware auth)** :

```python
# Dans @require_permission ou dans un before_request hook
from quart import g
g.rbac_user_id = current_user.id
```

**Pourquoi pas hériter de `search.Dealer` ?** Le proxy utilise `__getattr__` pour
déléguer, pas l'héritage. Hériter de `Dealer` forcerait à appeler `Dealer.__init__()`
qui attend un `docStoreConn` et initialise des connexions — couplage inutile.
Il n'existe aucun `isinstance(settings.retriever, Dealer)` dans le codebase.
Si un futur commit en ajoute un, c'est un fix d'une ligne au moment du merge.

**Ce que ce proxy couvre automatiquement** :
- Les 18 appels actuels à `retriever.retrieval()` dans 7 fichiers
- Tout futur appel ajouté par un merge upstream
- Les agents cross-groupes : un user SAV utilise un agent "Global" →
  le proxy filtre les kb_ids aux seuls datasets SAV, pas de 403

### Correction critique #4 : protection des routes de fichiers physiques

RAGFlow a une faille de sécurité existante : la route `GET /v1/document/image/<image_id>`
n'a **aucune authentification** (le décorateur `@login_required` est commenté dans
le code upstream). La route de download vérifie l'auth mais pas l'accès au dataset.

```python
# api/apps/document_app.py — CORRECTION : réactiver l'auth sur get_image

@manager.route("/image/<image_id>", methods=["GET"])
@login_required  # ← CORRECTION : dé-commenter (était commenté upstream !)
async def get_image(image_id):
    try:
        arr = image_id.split("-")
        if len(arr) != 2:
            return get_data_error_result(message="Image not found.")
        bkt, nm = image_id.split("-")
        # ← AJOUT : vérifier que le user a accès au bucket (= tenant_id)
        from api.apps.extensions.rbac import has_permission, Permission
        if not has_permission(current_user.id, bkt, Permission.DOCUMENT_READ):
            return get_json_result(data=False, message="Access denied", code=403)
        data = await thread_pool_exec(settings.STORAGE_IMPL.get, bkt, nm)
        ...
```

```python
# api/apps/document_app.py — CORRECTION : vérifier l'accès sur download

@manager.route("/download/<attachment_id>", methods=["GET"])
@login_required
async def download_attachment(attachment_id):
    try:
        # ← AJOUT : vérifier que le user a accès
        from api.apps.extensions.rbac import has_permission, Permission
        if not has_permission(current_user.id, current_user.id, Permission.DOCUMENT_READ):
            return get_json_result(data=False, message="Access denied", code=403)
        ...
```

### Modification `api/utils/api_utils.py` — enrichissement `@token_required`

```python
def token_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        ...
        token = authorization_list[1]
        objs = APIToken.query(token=token)
        if not objs:
            raise WerkzeugUnauthorized(...)

        kwargs["tenant_id"] = objs[0].tenant_id

        # ── AJOUT : vérification api_key_scope ──
        from api.db.services.workspace_service import ApiKeyScopeService
        scope = ApiKeyScopeService.get_by_token(token)
        if scope:
            if scope.expires_at and scope.expires_at < datetime.utcnow():
                raise WerkzeugUnauthorized(description="API key expired")
            if scope.status != "1":
                raise WerkzeugUnauthorized(description="API key disabled")
            ApiKeyScopeService.touch_last_used(token)
        # ── FIN AJOUT ──

        result = func(*args, **kwargs)
        if inspect.iscoroutine(result):
            return await result
        return result
    return wrapper
```

### Modification `api/apps/api_app.py` — bloquer création tokens natifs

```python
@manager.route('/new_token', methods=['POST'])
@login_required
async def new_token():
    # ← AJOUT : seuls les superusers peuvent créer des tokens natifs
    if not current_user.is_superuser:
        return get_json_result(
            data=False,
            message="API tokens must be created via the admin panel",
            code=403
        )
    ...  # code existant
```

### Modification `api/apps/user_app.py` — toggle signup

```python
@manager.route("/register", methods=["POST"])
async def register():
    if os.environ.get("DISABLE_SIGNUP", "").lower() in ("true", "1", "yes"):
        return get_json_result(data=False, message="Registration disabled", code=403)
    ...  # code existant
```

### Module `api/apps/extensions/audit.py`

```python
from functools import wraps
from quart import request  # Note : Quart, pas Flask (RAGFlow a migré)

def audit_log(action: str, resource_type: str = None):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            try:
                from api.db.services.audit_service import AuditService
                from api.apps.extensions.rbac import resolve_workspace_from_tenant
                from api.apps import current_user
                tenant_id = kwargs.get("tenant_id")
                ws = resolve_workspace_from_tenant(tenant_id) if tenant_id else None
                AuditService.record(
                    org_id=ws.org_id if ws else None,
                    workspace_id=ws.id if ws else None,
                    user_id=current_user.id if current_user else None,
                    action=action, resource_type=resource_type,
                    resource_id=kwargs.get("dataset_id") or kwargs.get("document_id"),
                    details={"method": request.method, "path": request.path},
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent", "")[:512],
                )
            except Exception:
                pass  # l'audit ne bloque jamais
            return result
        return wrapper
    return decorator
```

### Module `api/apps/extensions/quotas.py`

```python
def check_quota(org_id: str, resource_type: str) -> tuple[bool, str]:
    from api.db.services.org_service import OrgService
    org = OrgService.get_by_id(org_id)
    if not org:
        return True, ""  # legacy
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
```

## 8. Panel d'administration

### Stack
- **Backend** : FastAPI (Python 3.12, async, OpenAPI auto-docs)
- **Frontend** : Vue 3 + Vite + Tailwind CSS + shadcn-vue
- **Auth** : JWT propre
- **Déploiement** : Service Docker séparé

### Routes API

```
# Auth
POST   /api/admin/auth/login
GET    /api/admin/auth/me
POST   /api/admin/auth/refresh

# Super Admin : Organisations
GET    /api/admin/orgs
POST   /api/admin/orgs
GET    /api/admin/orgs/{org_id}
PUT    /api/admin/orgs/{org_id}
DELETE /api/admin/orgs/{org_id}
GET    /api/admin/orgs/{org_id}/stats

# Super Admin : System
GET    /api/admin/system/stats
GET    /api/admin/system/health

# Org Admin : Membres org
GET    /api/admin/orgs/{org_id}/members
POST   /api/admin/orgs/{org_id}/members
PUT    /api/admin/orgs/{org_id}/members/{uid}
DELETE /api/admin/orgs/{org_id}/members/{uid}

# Org Admin : Workspaces
GET    /api/admin/orgs/{org_id}/workspaces
POST   /api/admin/orgs/{org_id}/workspaces
GET    /api/admin/orgs/{org_id}/workspaces/{ws_id}
PUT    /api/admin/orgs/{org_id}/workspaces/{ws_id}
DELETE /api/admin/orgs/{org_id}/workspaces/{ws_id}

# Org Admin : Config LLM
GET    /api/admin/orgs/{org_id}/llm-config
PUT    /api/admin/orgs/{org_id}/llm-config
POST   /api/admin/orgs/{org_id}/llm-config/sync

# WS Admin : Membres workspace
GET    /api/admin/workspaces/{ws_id}/members
POST   /api/admin/workspaces/{ws_id}/members
PUT    /api/admin/workspaces/{ws_id}/members/{uid}
DELETE /api/admin/workspaces/{ws_id}/members/{uid}

# WS Admin : Groupes                                    ← NOUVEAU V3
GET    /api/admin/workspaces/{ws_id}/groups
POST   /api/admin/workspaces/{ws_id}/groups
PUT    /api/admin/workspaces/{ws_id}/groups/{gid}
DELETE /api/admin/workspaces/{ws_id}/groups/{gid}

# WS Admin : Membres d'un groupe                        ← NOUVEAU V3
GET    /api/admin/workspaces/{ws_id}/groups/{gid}/members
POST   /api/admin/workspaces/{ws_id}/groups/{gid}/members/{uid}
DELETE /api/admin/workspaces/{ws_id}/groups/{gid}/members/{uid}

# WS Admin : Datasets d'un groupe                       ← NOUVEAU V3
GET    /api/admin/workspaces/{ws_id}/groups/{gid}/datasets
POST   /api/admin/workspaces/{ws_id}/groups/{gid}/datasets/{did}
DELETE /api/admin/workspaces/{ws_id}/groups/{gid}/datasets/{did}

# WS Admin : Datasets & stats
GET    /api/admin/workspaces/{ws_id}/datasets
GET    /api/admin/workspaces/{ws_id}/stats

# WS Admin : API Keys scopées                           ← NOUVEAU V3
GET    /api/admin/workspaces/{ws_id}/api-keys
POST   /api/admin/workspaces/{ws_id}/api-keys
DELETE /api/admin/workspaces/{ws_id}/api-keys/{key_id}

# Audit
GET    /api/admin/orgs/{org_id}/audit
GET    /api/admin/workspaces/{ws_id}/audit
```

### Vues frontend

```
management/web/src/views/
├── Login.vue
├── Dashboard.vue
├── organisations/
│   ├── OrgList.vue
│   ├── OrgCreate.vue
│   └── OrgDetail.vue          # onglets: Members / Workspaces / LLM / Audit
├── workspaces/
│   ├── WsList.vue
│   ├── WsCreate.vue
│   └── WsDetail.vue           # onglets: Members / Groups / Datasets / API Keys / Audit
├── groups/                     ← NOUVEAU V3
│   └── GroupDetail.vue         # membres du groupe + datasets assignés
├── members/
│   ├── MemberList.vue
│   └── MemberInvite.vue
└── audit/
    └── AuditLog.vue
```

### Structure dossier

```
management/
├── server/
│   ├── main.py
│   ├── config.py
│   ├── auth/
│   │   ├── jwt.py
│   │   └── dependencies.py
│   ├── routers/
│   │   ├── auth.py
│   │   ├── orgs.py
│   │   ├── workspaces.py
│   │   ├── members.py
│   │   ├── groups.py          ← NOUVEAU V3
│   │   ├── api_keys.py        ← NOUVEAU V3
│   │   ├── llm_config.py
│   │   ├── audit.py
│   │   └── system.py
│   ├── services/
│   │   ├── ragflow_client.py
│   │   ├── db.py
│   │   └── provisioning.py
│   ├── models/
│   │   └── schemas.py
│   ├── Dockerfile
│   └── requirements.txt
├── web/
│   ├── src/
│   ├── Dockerfile
│   ├── package.json
│   └── vite.config.ts
└── docker-compose.yml
```

## 9. Flows de provisioning

### Création d'une organisation

```
Super Admin → POST /api/admin/orgs
  1. Créer `organisation` (name, slug, quotas)
  2. Créer `org_member` (creator = org_admin)
  3. Retourner org_id
  4. PUT /api/admin/orgs/{org_id}/llm-config → stocker config LLM
```

### Création d'un workspace

```
Org Admin → POST /api/admin/orgs/{org_id}/workspaces
  1. Vérifier quota workspaces
  2. Créer un User RAGFlow "technique" (email: ws-{slug}@internal)
  3. Créer le Tenant RAGFlow associé (tenant_id = user.id)
  4. Copier la config LLM de l'org → TenantLLM du nouveau tenant
  5. Créer `workspace` (org_id, tenant_id, name)
  6. Créer `ws_member` (creator = ws_admin)
  7. Créer `UserTenant` (user=creator, tenant=new_tenant, role=NORMAL)
     Le role RAGFlow reste NORMAL — le rôle réel est dans ws_member
```

### Création d'un groupe et assignation

```
WS Admin → POST /api/admin/workspaces/{ws_id}/groups       {name: "SAV"}
WS Admin → POST .../groups/{gid}/members/{jean_id}          # ajouter Jean
WS Admin → POST .../groups/{gid}/members/{marie_id}         # ajouter Marie
WS Admin → POST .../groups/{gid}/datasets/{kb_garanties_id} # assigner KB
WS Admin → POST .../groups/{gid}/datasets/{kb_sav_id}       # assigner KB
```

### Invitation d'un membre

```
Org/WS Admin → POST /api/admin/workspaces/{ws_id}/members
  1. Vérifier que le user est membre de l'org (ou le créer)
  2. Vérifier quota users
  3. Si user n'existe pas dans RAGFlow : créer User + copier LLM config
  4. Créer `ws_member` (user_id, workspace_id, role=viewer|editor|ws_admin)
  5. Créer `UserTenant` (user_id, tenant_id, role=NORMAL)
  6. Optionnel : ajouter à un ou plusieurs groupes
  7. Audit log + email d'invitation
```

### Accès d'un user à RAGFlow

```
User se connecte à RAGFlow (email/password) → inchangé
  1. RAGFlow charge ses tenants via UserTenant (inchangé)
  2. Sélection d'un tenant/workspace
  3. @require_permission vérifie le rôle via ws_member
  4. filter_datasets() / assert_dataset_access() filtre par groupes
  5. L'UI RAGFlow fonctionne normalement — elle ne sait pas qu'il y a du RBAC
```

## 10. Rétrocompatibilité

Le système est **opt-in**. Trois niveaux de fallback :

```python
# 1. Pas de workspace trouvé → mode legacy RAGFlow natif
workspace = resolve_workspace_from_tenant(tenant_id)
if not workspace:
    return tenant_id == user_id  # has_permission()
    return None                  # get_visible_dataset_ids() = pas de filtre

# 2. Workspace existe mais user sans groupe → voit tous les datasets
groups = GroupService.get_user_groups(workspace.id, user_id)
if not groups:
    return None  # pas de filtre

# 3. Workspace existe et user a des groupes → filtrage actif
return GroupService.get_datasets_for_groups(group_ids)
```

### Migration des installations existantes

Script optionnel :
1. Crée une org "Default" avec le super admin
2. Crée un workspace par tenant existant
3. Mappe les UserTenant existants → ws_member (`normal` → `editor`, `owner` → `ws_admin`)
4. Pas de groupes créés → tout le monde voit tout (comme avant)

## 11. Plan de développement

### Sprint 0 — Setup (2 jours)

- [ ] Fork du repo RAGFlow depuis commit `60ec5880e` (HEAD de main au 2026-04-04, "Feat: mysql data migrate script #13927")
- [ ] `git tag rbac/fork-point 60ec5880e` — figer le point de fork
- [ ] Documenter le commit exact dans `FORK_BASE.md`
- [ ] Vérifier que le build Docker fonctionne en local
- [ ] Créer la structure `management/` vide
- [ ] Créer `MODIFIED_FILES.md`

### Sprint 1 — Modèles et services (5 jours)

- [ ] Ajouter les 9 modèles Peewee dans `db_models.py`
- [ ] Script de création des tables (pattern `init_data.py`)
- [ ] `OrgService` : CRUD org + get_resource_counts
- [ ] `WorkspaceService` + `WsMemberService` : CRUD + get_membership + get_by_tenant_id
- [ ] `GroupService` : CRUD groupes + get_user_groups + get_datasets_for_groups
- [ ] `OrgMemberService` : CRUD membres org
- [ ] `AuditService` : record + query avec filtres
- [ ] `ApiKeyScopeService` : CRUD + get_by_token + touch_last_used
- [ ] Tests unitaires de chaque service

### Sprint 2 — RBAC core + sécurisation routes (6.5 jours)

**2a. Modules RBAC (2 jours)**
- [ ] `rbac.py` : Permission enum, ROLE_PERMISSIONS, has_permission()
- [ ] `rbac.py` : get_visible_dataset_ids(), assert_dataset_access(),
      filter_chat_dataset_ids()
- [ ] `rbac.py` : require_permission(), require_org_admin()
- [ ] `audit.py` : audit_log() décorateur
- [ ] `quotas.py` : check_quota()

**2b. Sécurisation des routes non protégées (1 jour)**
- [ ] `document_app.py` : dé-commenter @login_required sur `get_image`,
      ajouter vérification RBAC sur `download_attachment`
- [ ] `agents.py` : ajouter @login_required + validation tenant sur
      `webhook`, `webhook_test`, `webhook_trace`
- [ ] `canvas_app.py` : ajouter @login_required sur `upload/<canvas_id>`,
      `trace`
- [ ] `mcp_server_app.py` : ajouter @login_required sur `test_mcp`
- [ ] `api_app.py` : bloquer création tokens natifs pour non-superusers
- [ ] `user_app.py` : toggle inscription via env var
- [ ] `api_utils.py` : enrichir @token_required avec api_key_scope

**2c. Injection RBAC dans les routes (2 jours)**
- [ ] Injecter `@require_permission` dans les routes RESTful :
      `chat_api.py`, `dataset_api.py`, `file_api.py`, `search_api.py`
- [ ] Injecter `@require_permission` dans les routes SDK :
      `doc.py`, `session.py`, `agents.py`
- [ ] Injecter `@require_permission` dans les routes app :
      `kb_app.py`, `document_app.py`, `canvas_app.py`, `chunk_app.py`
- [ ] Injecter filtrage SQL dataset dans les listings
      (`get_visible_dataset_ids` → `id__in` dans la query service)
- [ ] Injecter `assert_dataset_access()` dans chat.create, agent.create

**2d. Proxy Retriever — filtrage automatique (0.5 jour)**
- [ ] Créer `api/apps/extensions/rbac_retriever.py` (proxy pattern)
- [ ] Appeler `install_rbac_proxy()` au démarrage serveur (1 ligne)
- [ ] Injecter `g.rbac_user_id = current_user.id` dans le middleware auth
      (before_request hook ou dans @require_permission)
- [ ] Vérifier que les tâches de fond (parsing, indexation) ne sont PAS
      impactées (pas de contexte HTTP → pas de filtre → OK)

**2e. Tests d'intégration (1 jour)**
- [ ] Viewer ne peut pas créer de dataset → 403
- [ ] Editor peut créer mais pas gérer les membres → 403
- [ ] User groupe "SAV" ne voit que les datasets SAV dans le listing
- [ ] User groupe "SAV" utilise un agent "Global" → retrieval uniquement sur datasets SAV
- [ ] User dans 2 groupes voit l'union des datasets
- [ ] User sans groupe voit tout (rétrocompat)
- [ ] WS Admin voit tout
- [ ] Org Admin voit tout dans tous les workspaces de son org
- [ ] Super Admin voit tout partout
- [ ] User sans workspace → comportement legacy
- [ ] API key scopée : accès limité aux permissions déclarées
- [ ] API key expirée → 401
- [ ] `GET /image/<id>` sans auth → 401 (plus accessible publiquement)
- [ ] `POST /webhook/<id>` sans auth → 401
- [ ] `POST /upload/<canvas_id>` sans auth → 401

### Sprint 3 — Config LLM partagée (2 jours)

- [ ] Copie config LLM : org → workspace (TenantLLM)
- [ ] Sync : propager changements org vers tous les tenants
- [ ] Hook invitation : copier config LLM vers nouveau membre
- [ ] Tests

### Sprint 4 — Panel admin backend (6 jours)

- [ ] Setup FastAPI dans `management/server/`
- [ ] Auth JWT : login, refresh, middleware
- [ ] Routes orgs : list, create, update, delete, stats
- [ ] Routes workspaces : list, create, update, delete, stats
- [ ] Routes members org : list, invite, update role, remove
- [ ] Routes members ws : list, add, update role, remove
- [ ] Routes groupes : list, create, update, delete
- [ ] Routes groupe-members : list, add, remove
- [ ] Routes groupe-datasets : list, add, remove
- [ ] Routes API keys : list, create, delete
- [ ] Routes LLM config : get, update, sync
- [ ] Routes audit : query avec filtres
- [ ] Routes system : stats, health
- [ ] `ragflow_client.py` : wrapper API RAGFlow
- [ ] `provisioning.py` : création org/workspace/membre
- [ ] Tests API

### Sprint 5 — Panel admin frontend (6 jours)

- [ ] Setup Vue 3 + Vite + Tailwind + shadcn-vue
- [ ] Login + auth store (Pinia)
- [ ] Layout sidebar (adaptatif selon rôle)
- [ ] Dashboard : stats, alertes quotas
- [ ] Orgs : liste, création, détail (onglets Members/Workspaces/LLM/Audit)
- [ ] Workspaces : liste, création, détail (onglets Members/Groups/Datasets/API Keys/Audit)
- [ ] Groupes : détail avec drag & drop membres + datasets
- [ ] Membres : liste, invite, changement rôle
- [ ] API Keys : liste, création avec sélection permissions, suppression
- [ ] LLM Config : formulaire + bouton sync
- [ ] Audit Log : table filtrable, export CSV
- [ ] Responsive + dark mode

### Sprint 6 — Docker et intégration (3 jours)

- [ ] Dockerfile multi-stage management (backend + frontend)
- [ ] Mise à jour docker-compose.yml
- [ ] `.env.example` documenté
- [ ] Script de provisioning initial
- [ ] Script de migration (installations existantes → RBAC)
- [ ] Test end-to-end environnement clean
- [ ] Documentation d'installation

### Sprint 7 — Hardening et production (5 jours)

- [ ] Audit sécurité : toutes les routes protégées, pas de bypass
- [ ] Rate limiting sur login, invite
- [ ] Validation quotas sur toutes les routes de création
- [ ] Gestion erreurs propre (pas de stack traces)
- [ ] Tests de charge : 50 orgs, 500 users, 1000 datasets, 100 groupes
- [ ] Fix bugs et edge cases
- [ ] Déploiement Bodemer
- [ ] Création orgs/workspaces/groupes/membres Bodemer
- [ ] Validation : SAV ne voit pas les KB Commercial et vice-versa
- [ ] Validation end-to-end

---

**Total estimé : ~10 semaines** pour une V1 production-ready.
(+2 jours sur Sprint 2 par rapport à V3, pour la sécurisation des routes
et la propagation user_id dans le pipeline retrieval)

## 12. Ce qui rend le système vendable

| Feature | Valeur business |
|---------|----------------|
| Multi-org | Chaque client isolé, un déploiement pour N clients |
| RBAC 5 rôles | Les entreprises exigent le contrôle d'accès par rôle |
| Groupes + dataset-level | SAV ne voit que ses KB, Commercial les siennes — le use case #1 |
| Audit trail | Compliance SOC2/RGPD — qui a fait quoi et quand |
| Quotas | Plans tarifaires (Free: 5 users, Pro: 50, Enterprise: illimité) |
| Panel admin | Self-service pour org admins, réduit le support |
| Config LLM centralisée | L'org admin configure une fois, tous en bénéficient |
| API keys scopées | CI/CD sécurisé avec minimum de permissions |
| Rétrocompatibilité | Migration sans disruption, adoption progressive |
| Minimal fork diff | Merge upstream en <1h, ~15 fichiers touchés |

## 13. Roadmap V2

- [ ] SSO / SAML / OIDC
- [ ] Rôles custom : éditeur de matrice de permissions
- [ ] Billing / Stripe : plans tarifaires, facturation auto
- [ ] Usage tracking : compteurs tokens LLM, stockage, API calls par org
- [ ] Webhooks : notifications (nouveau membre, quota atteint)
- [ ] Multi-region : config par org du endpoint Infinity/MinIO
- [ ] Isolation stockage physique par org (bucket-per-org ou instance S3 dédiée) :
  - L'architecture est déjà prête : `STORAGE_IMPL` est un singleton avec factory
    (`RAGFlowMinio` supporte déjà multi-bucket et single-bucket avec préfixes)
  - Toutes les méthodes (`put`, `get`, `rm`, `obj_exist`) reçoivent déjà `tenant_id`
  - Implémentation : proxy `OrgStorageProxy` sur `STORAGE_IMPL` (même pattern que
    `RBACRetrieverProxy`) qui route vers un storage dédié si `organisation.storage_config`
    est renseigné (colonne JSON nullable dans la table `organisation`)
  - Compatible tout backend S3 (MinIO, AWS S3, Azure, GCS) — même interface
  - Pas de migration de données : nouveaux clients sur bucket dédié, anciens inchangés
  - Estimation : ~1 jour de dev, le plus long est la config infra (bucket + IAM)
- [ ] White-labeling : branding custom par org
- [ ] Frontend capabilities API : `GET /api/v1/me/permissions` pour masquer
  les boutons dans l'UI RAGFlow selon le rôle (correction Gemini #3)

## 14. Stratégie de merge upstream

- Version de base fixée dans `FORK_BASE.md`
- Fichiers modifiés listés dans `MODIFIED_FILES.md`
- Merge upstream tous les **3 mois ou sur alerte sécurité**
- Procédure :
  1. `git fetch upstream`
  2. Changelog des versions intermédiaires
  3. `git merge upstream/vX.Y.Z`
  4. Résoudre conflits (fichiers listés dans `MODIFIED_FILES.md`)
  5. Tests d'intégration
- Les modifications sont des imports + décorateurs (2-5 lignes par fichier).
  Conflits mineurs même si RAGFlow refactore ces fichiers.

## 15. Corrections intégrées depuis les reviews

| # | Problème identifié | Correction appliquée |
|---|---|---|
| Gemini #1 | Modifier `UserTenant.role` casserait `get_joined_tenants_by_user_id()` et `get_info_by()` | `ws_member` est la seule source de vérité. `UserTenant.role` reste à `NORMAL` pour tous les membres. |
| Gemini #2 | Les tokens API natifs bypassent le scoping | Création de tokens natifs bloquée pour non-superusers. `@token_required` enrichi pour vérifier `api_key_scope`. |
| Gemini #3 | Boutons visibles dans l'UI RAGFlow pour un viewer → 403 frustrant | Accepté comme compromis V1 (backend bloque). Endpoint capabilities API prévu en V2. |
| Review interne | V2 avait perdu la granularité dataset-level de V1 | Réintégrée via `ws_group` + `ws_group_member` + `ws_group_dataset` (3 tables). |
| Review interne | Pas de notion de "groupe" / "équipe" dans V2 | Ajoutée. Un user peut être dans N groupes, chaque groupe voit N datasets. |
| Review interne | Pas de partage cross-workspace | Hors scope V1. Chaque workspace = 1 tenant isolé. Un dataset ne peut être que dans un workspace. |
| Critique #1 | Filtrage dataset en Python post-query = anti-pattern perf | Filtrage en SQL via `id__in=visible_ids` injecté dans la requête service. |
| Critique #2 | Agent cross-groupes bloqué par 403 → inutilisable | `filter_chat_dataset_ids()` filtre au runtime du retrieval au lieu de bloquer. L'agent fonctionne pour tous, chacun voit les résultats de ses datasets. |
| Critique #3 | Boutons fantômes UX | Identique à Gemini #3. Quick win possible : bandeau rôle (20 lignes React). |
| Critique #4 | `GET /v1/document/image/<id>` n'a AUCUNE auth (commentée upstream !) | `@login_required` réactivé + vérification `has_permission(DOCUMENT_READ)`. Route download protégée aussi. |
| V3.1 | Le plan référençait `api/apps/sdk/chat.py` et `dataset.py` — fichiers supprimés depuis v0.24.0 | Mis à jour vers `api/apps/restful_apis/chat_api.py` et `dataset_api.py`. Base de fork = HEAD de main. |
| V3.1 | Le plan utilisait Flask — RAGFlow a migré vers Quart (async) | Tous les décorateurs et wrappers utilisent `async def` et `await`. |
| V3.1 audit | 7 routes sans aucune auth dans RAGFlow upstream (webhooks, image, upload, trace, mcp) | Toutes sécurisées avec @login_required + vérification tenant au Sprint 2b. |
| V3.1 audit | Le retrieval pipeline (`rag/nlp/search.py`) ne reçoit jamais de `user_id` — 18 appels dans 7 fichiers | `RBACRetrieverProxy` interposé sur `settings.retriever` via proxy pattern. Couvre tous les appels présents et futurs. Aucun fichier RAG modifié. |
| V3.1 audit | Les endpoints "bot" publics (`/chatbots/`, `/agentbots/`) bypassent le RBAC | Accepté : ces endpoints sont intentionnellement publics. Documenté. Le scoping API key atténue le risque. |
| V3.1 audit | `Canvas.__init__()` ne reçoit que `tenant_id`, pas `user_id` | Plus besoin de modifier Canvas — le proxy retriever lit `g.rbac_user_id` depuis le contexte de requête Quart. |
| V3.1 proxy | Modifier les 18 appels retrieval un par un est fragile (un 19ème bypasse le RBAC) | Proxy pattern : `RBACRetrieverProxy` intercepte tout appel à `settings.retriever.retrieval()`. 1 fichier nouveau + 1 ligne d'init. |
| Gemini final #1 | "HEAD de main" est une cible mouvante — risque de fork sur un commit cassé | Commit figé : `60ec5880e` (2026-04-04). Tag `rbac/fork-point` créé au Sprint 0. |
| Gemini final #2 | `id__in=visible_ids` peut exploser avec 15 000+ datasets (limite IN clause MySQL) | Non-bloquant V1 (< 1 000 datasets par workspace, MySQL supporte ~65K params). V2 : migration vers JOIN sur `ws_group_dataset`. Schéma déjà compatible. |
| Gemini final #3 | MinIO : isolation logique (chemin `tenant_id/`) insuffisante pour banque/défense/santé | Hors scope V1. V2 : bucket-per-org avec policy IAM MinIO. L'isolation applicative (RBAC) couvre les cas B2B standards. |
