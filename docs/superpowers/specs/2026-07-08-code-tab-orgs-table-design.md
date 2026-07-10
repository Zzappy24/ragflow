# Design — Onglet Code : landing en tableau d'orgs

- **Date** : 2026-07-08 · **Statut** : validé (suite QA UX utilisateur)
- **Problèmes** : (1) picker d'org non cherchable ; (2) aucune distinction code activé/suspendu/non activé ; (3) sélection non URL-driven → bouton retour cassé.

## Backend — 1 route

`GET /api/admin/code/orgs-summary` → `[{org_id, org_name, code_status, org_code_budget, allocated, teams_count, keys_count}]`

- `code_status` : `"active" | "suspended" | null` (null = pas d'entitlement).
- RBAC : superuser → toutes les orgs actives ; sinon orgs où l'appelant est membre (même visibilité que `GET /orgs`).
- Implémentation sans N+1 : liste d'orgs visibles, puis 3 requêtes agrégées (`code_entitlement` par org_id, `SUM/COUNT code_team` groupé par org, `COUNT code_key` groupé par team→org).
- `allocated` = somme des `max_budget` des teams `status="active"` (même définition que l'invariant).
- Tests (`test_code_routes_rbac.py`) : superuser voit ≥ ses orgs ; membre simple voit uniquement les siennes ; org sans entitlement → `code_status: null` ; jamais de plaintext.

## Front — `pages/code/index.tsx`

- **Sans `?org=`** : tableau antd (remplace le Select) — colonnes : Organisation / Statut (Tag : vert `actif`, orange `suspendu`, gris `non activé`) / Budget (`alloué / total €`) / Teams / Clés. `Input.Search` filtre client-side par nom. Tri par défaut : actives → suspendues → non activées, puis alphabétique. Clic ligne → `setSearchParams({org: id})` (push history → retour navigateur OK).
- **Avec `?org=`** : vue org existante + header : lien `← Toutes les organisations` (efface le param) + nom d'org + Tag statut.
- **Auto-select conservé** : exactement 1 org visible → on saute le tableau (comportement actuel).
- Ligne « non activé » + superuser : lien `activer` → `/organisations/{id}` (card entitlement).

## Hors périmètre

Spend réel dans le tableau (différé, cf. spec control-plane §7) · pagination (volumes faibles) · i18n.
