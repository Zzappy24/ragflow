# Code Tab Orgs-Table Landing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le picker d'org de l'onglet Code par un tableau cherchable avec badges de statut, navigation URL-driven (retour navigateur OK).

**Architecture:** 1 route récap agrégée côté panel (`GET /api/admin/code/orgs-summary`, même visibilité que `GET /orgs`), et un rework du landing de `pages/code/index.tsx` (tableau antd + `setSearchParams`). Vue org existante inchangée hormis un header retour.

**Tech Stack:** FastAPI + Peewee (agrégats `fn.SUM/COUNT`, `.dicts()`), React + antd (Table, Input.Search, Tag).

**Spec:** `docs/superpowers/specs/2026-07-08-code-tab-orgs-table-design.md`

## Global Constraints

- Visibilité orgs = même règle que `GET /orgs` (superuser → toutes actives ; sinon membership).
- `allocated` = somme des `max_budget` des teams `status="active"` (définition de l'invariant).
- Pas de N+1 : 3 requêtes agrégées max après la liste d'orgs.
- Jamais de plaintext / MASTER_KEY dans la réponse.
- Auto-select conservé quand 1 seule org visible.
- Env tests : `ADMIN_JWT_SECRET` depuis `/Users/zappy/ragflow/.env.local` (chemin absolu), dev stack up.

---

### Task 1: Route `GET /api/admin/code/orgs-summary` + tests RBAC

**Files:**
- Modify: `management/server/routers/code.py` (nouvelle route, avant les routes `/code/teams`)
- Test: `test/multitenant/test_code_routes_rbac.py` (3 tests ajoutés)

**Interfaces:**
- Produces: `GET /api/admin/code/orgs-summary` → `list[{org_id: str, org_name: str, code_status: "active"|"suspended"|None, org_code_budget: float, allocated: float, teams_count: int, keys_count: int}]` — consommé par la Task 2.

- [ ] **Step 1: Write the failing tests** (append à `test/multitenant/test_code_routes_rbac.py`)

```python
def test_orgs_summary_superuser_sees_org_with_status(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    client.post(f"/api/admin/orgs/{org_id}/code/teams",
                json={"name": "s", "max_budget": 40.0, "model_access": []},
                headers=_h(tokens["org_admin"]))
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(tokens["superuser"])).json()
    mine = next(r for r in rows if r["org_id"] == org_id)
    assert mine["code_status"] == "active"
    assert mine["org_code_budget"] == 100.0
    assert mine["allocated"] == 40.0
    assert mine["teams_count"] == 1
    assert "plain" not in str(rows)


def test_orgs_summary_member_sees_only_their_orgs(panel_client, org_with_entitlement_and_users, second_org_admin):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    org_b_id, org_b_token, _ = second_org_admin
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(tokens["plain_member"])).json()
    ids = {r["org_id"] for r in rows}
    assert org_id in ids and org_b_id not in ids


def test_orgs_summary_org_without_entitlement_is_null(panel_client, org_with_entitlement_and_users, second_org_admin):
    client, _ = panel_client
    org_b_id, org_b_token, _ = second_org_admin
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(org_b_token)).json()
    mine = next(r for r in rows if r["org_id"] == org_b_id)
    assert mine["code_status"] is None
    assert mine["teams_count"] == 0 and mine["keys_count"] == 0
```

Note : vérifier la forme exacte du yield de `second_org_admin` dans `test/multitenant/conftest.py` (tuple `(org_id, token, ...)`) et adapter le dépaquetage si besoin.

- [ ] **Step 2: Run to verify FAIL (404)**

```bash
export ADMIN_JWT_SECRET=$(grep "^ADMIN_JWT_SECRET=" /Users/zappy/ragflow/.env.local | cut -d= -f2-)
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py -k orgs_summary -v
```
Expected: 3 FAIL (404 route absente)

- [ ] **Step 3: Implement the route** (dans `management/server/routers/code.py`, avant `create_team`)

```python
@router.get("/code/orgs-summary")
def orgs_summary(user=Depends(get_current_user)):
    """Landing de l'onglet Code : récap par org visible (statut, budget, alloué, compteurs)."""
    from peewee import fn
    from api.db.db_models import DB, CodeEntitlement, CodeTeam, CodeKey
    from api.db.services.org_service import OrgService, OrgMemberService

    if user.is_superuser:
        orgs = OrgService.query(status="1")
    else:
        memberships = OrgMemberService.list_orgs_for_user(user.id)
        org_ids_visible = {m.org_id for m in memberships}
        orgs = [o for o in OrgService.query(status="1") if o.id in org_ids_visible] if org_ids_visible else []
    org_ids = [o.id for o in orgs]
    if not org_ids:
        return []

    with DB.connection_context():
        ents = {e.org_id: e for e in CodeEntitlement.select().where(CodeEntitlement.org_id.in_(org_ids))}
        team_agg = {}
        for d in (CodeTeam.select(CodeTeam.org_id,
                                  fn.COALESCE(fn.SUM(CodeTeam.max_budget), 0.0).alias("allocated"),
                                  fn.COUNT(CodeTeam.id).alias("teams"))
                  .where((CodeTeam.org_id.in_(org_ids)) & (CodeTeam.status == "active"))
                  .group_by(CodeTeam.org_id).dicts()):
            team_agg[d["org_id"]] = (float(d["allocated"]), int(d["teams"]))
        key_agg = {}
        for d in (CodeKey.select(CodeTeam.org_id, fn.COUNT(CodeKey.id).alias("keys"))
                  .join(CodeTeam, on=(CodeKey.code_team_id == CodeTeam.id))
                  .where((CodeTeam.org_id.in_(org_ids)) & (CodeTeam.status == "active"))
                  .group_by(CodeTeam.org_id).dicts()):
            key_agg[d["org_id"]] = int(d["keys"])

    out = []
    for o in orgs:
        ent = ents.get(o.id)
        allocated, teams = team_agg.get(o.id, (0.0, 0))
        out.append({
            "org_id": o.id,
            "org_name": o.name,
            "code_status": ent.status if ent else None,
            "org_code_budget": float(ent.org_code_budget) if ent else 0.0,
            "allocated": allocated,
            "teams_count": teams,
            "keys_count": key_agg.get(o.id, 0),
        })
    return out
```

(`get_current_user` est déjà importé dans ce fichier ? Vérifier — sinon l'ajouter à l'import des deps.)

- [ ] **Step 4: Run to verify PASS + non-régression**

```bash
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py -v
```
Expected: 10 passed (7 existants + 3 nouveaux). `uv tool run ruff check management/server/routers/code.py` clean.

- [ ] **Step 5: Commit**

```bash
git add management/server/routers/code.py test/multitenant/test_code_routes_rbac.py
git commit -m "feat(code): route orgs-summary — récap statut/budget/compteurs par org visible"
```

---

### Task 2: Landing tableau + header retour dans `pages/code/index.tsx`

**Files:**
- Modify: `management/web/src/pages/code/index.tsx`

**Interfaces:**
- Consumes: `GET /code/orgs-summary` (Task 1). Vue org existante (overview/teams/keys) inchangée.

- [ ] **Step 1: Ajouter l'interface + le fetch du summary** (remplace l'actuel fetch `GET /orgs` du no-org state)

```tsx
interface OrgSummary {
  org_id: string; org_name: string;
  code_status: 'active' | 'suspended' | null;
  org_code_budget: number; allocated: number;
  teams_count: number; keys_count: number;
}
```

State : `const [summary, setSummary] = useState<OrgSummary[] | null>(null);` + `const [orgSearch, setOrgSearch] = useState('');`
Effect (quand `!orgId`) : `api.get('/code/orgs-summary')` → si `res.data.length === 1` → `setSearchParams({ org: res.data[0].org_id })` sinon `setSummary(res.data)`. Catch → `setLoadError(...)` (état existant).

- [ ] **Step 2: Remplacer le rendu no-org (Select) par le tableau**

```tsx
const statusTag = (s: OrgSummary['code_status']) =>
  s === 'active' ? <Tag color="green">actif</Tag>
  : s === 'suspended' ? <Tag color="orange">suspendu</Tag>
  : <Tag>non activé</Tag>;

const statusRank = { active: 0, suspended: 1 } as Record<string, number>;
const filtered = (summary ?? [])
  .filter((o) => o.org_name.toLowerCase().includes(orgSearch.toLowerCase()))
  .sort((a, b) =>
    (statusRank[a.code_status ?? 'z'] ?? 2) - (statusRank[b.code_status ?? 'z'] ?? 2)
    || a.org_name.localeCompare(b.org_name));

if (!orgId) return (
  <div>
    <div className="flex justify-between items-center mb-4">
      <h2 className="text-xl font-semibold">Code — accès gateway</h2>
      <Input.Search placeholder="Rechercher une organisation…" allowClear
        style={{ width: 320 }} onChange={(e) => setOrgSearch(e.target.value)} />
    </div>
    <Card>
      <Table rowKey="org_id" size="middle" loading={summary === null && !loadError}
        pagination={false} dataSource={filtered}
        onRow={(r) => ({ onClick: () => setSearchParams({ org: r.org_id }), style: { cursor: 'pointer' } })}
        columns={[
          { title: 'Organisation', dataIndex: 'org_name' },
          { title: 'Statut', dataIndex: 'code_status', width: 130, render: statusTag },
          { title: 'Budget', width: 180,
            render: (_, r) => r.code_status ? `${r.allocated} € / ${r.org_code_budget} €` : '—' },
          { title: 'Teams', dataIndex: 'teams_count', width: 90,
            render: (v, r) => (r.code_status ? v : '—') },
          { title: 'Clés', dataIndex: 'keys_count', width: 90,
            render: (v, r) => (r.code_status ? v : '—') },
        ]} />
    </Card>
  </div>
);
```

(garder le rendu `loadError` existant au-dessus ; supprimer le Select et son state devenu mort.)

- [ ] **Step 3: Header retour + badge dans la vue org**

Dans le rendu avec `orgId`, remplacer le titre par :

```tsx
<div className="flex items-center gap-3 mb-4">
  <Button type="link" className="px-0" onClick={() => setSearchParams({})}>
    ← Toutes les organisations
  </Button>
</div>
<div className="flex justify-between items-center mb-4">
  <h2 className="text-xl font-semibold">
    Code — accès gateway
    {ent?.status === 'active' && <Tag color="green" className="ml-2">actif</Tag>}
    {ent?.status === 'suspended' && <Tag color="orange" className="ml-2">suspendu</Tag>}
  </h2>
  <Button type="primary" icon={<PlusOutlined />} onClick={() => setTeamModal(true)}>
    Nouvelle code-team
  </Button>
</div>
```

Imports à compléter : `Input`, `Table` (et retirer `Select` si plus utilisé).

- [ ] **Step 4: Build + vérif visuelle Playwright**

```bash
cd management/web && PATH=/opt/homebrew/bin:$PATH npm run build
```
Expected: zéro erreur TS. Puis re-driver `qa_ui_drive.py` adapté (le no-org state doit montrer le tableau ; clic ligne → vue org ; `page.go_back()` → retour au tableau).

- [ ] **Step 5: Commit**

```bash
git add management/web/src/pages/code/index.tsx
git commit -m "feat(code-ui): landing tableau d'orgs — recherche, badges statut, retour navigateur"
```

## Self-review

Spec coverage : route ✅ (Task 1), tableau+recherche+badges+tri ✅ (Task 2 Step 2), URL-driven/back ✅ (setSearchParams partout), auto-select 1 org ✅ (Step 1), header retour ✅ (Step 3), lien « activer » superuser — **retiré du scope** (YAGNI : la ligne non-activée est cliquable et la vue org montre l'état ; l'activation reste sur la page Organisations où vit la card). Pas de placeholder. Types cohérents.
