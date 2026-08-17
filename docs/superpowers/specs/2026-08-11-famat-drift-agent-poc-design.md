# FAMAT — Agent RAGFlow de détection de dérive process (POC)

**Date** : 2026-08-11
**Statut** : validé pour plan d'implémentation
**Objectif business** : démontrer devant FAMAT (réunion client Cyllene/Zefire) que la plateforme RAGFlow de Cyllene sait faire l'analyse de dérive process et l'explication de non-conformités en interne — sans délégation de la partie technique à Zefire.

## Révisions — 2026-08-11, exécution

Écarts constatés entre ce spec et l'exécution réelle du POC (tasks 9-12,
`.superpowers/sdd/2026-08-11-famat-drift-agent-poc/`) :

- **Source de données** : le spec décrivait `execute_sql`/ExeSQL branché directement sur
  la base Cyllene (topologie de prod dès le POC, § Architecture). En pratique, la base
  Cyllene n'était pas accessible pendant l'exécution — le flux réel est un fichier CSV
  (`Payload-20260526.csv`) servi via MinIO local et chargé par `famat_recipes.load_url()`
  dans DuckDB. Le branchement base directe (`fr.load_db(...)`) reste différé, documenté
  comme TODO en tête de `poc/famat/system_prompt.md`.
- **Tool unique** : conséquence directe du point précédent — seul `code_exec` est câblé
  dans le canvas (recettes 1/2/3 + exploration SQL agrégée via DuckDB, toutes passant par
  le même tool). `execute_sql`/ExeSQL n'a jamais été branché ; à ouvrir quand l'accès base
  Cyllene sera disponible.
- **Critère « < 1 min/analyse »** : qualifié par la mesure réelle (task 12) plutôt
  qu'atteint uniformément. Recettes 1 et 2 tiennent le critère (≈13-16 s en cas de succès
  au premier essai). Recette 3 (température/redémarrages) monte jusqu'à ≈67 s, et
  l'exploration libre jusqu'à ≈100 s. La latence ne vient PAS des recettes elles-mêmes
  (déterministes, quelques secondes d'exécution DuckDB/matplotlib) mais du LLM reasoning
  + de l'infrastructure LiteLLM/vLLM en aval (voir inquiétudes task-12-report.md).
- **Verdict corrélation température** : le spec (§ Contexte, « corrélation déjà
  établie ») présentait le lien température↔redémarrages comme un fait acquis côté
  partenaire. L'investigation sur le dump complet (task 12) **infirme** cette
  corrélation : `corr = −0,073` (quasi nulle), effet directionnel modeste mais réel
  (test de Welch, Cohen's d ≈ −0,32) — très loin d'expliquer l'intégralité des
  redémarrages. Détail complet dans `poc/famat/DEMO.md` § Corrélation température.
- **Backtest CORRECTION_X, chapitre 10** : le backtest rejoué sur ce couple clé/chapitre
  donne **0 vraie alerte / 2 fausses alertes** — les limites Shewhart statiques ±3σ sont
  peu sensibles à une dérive lente et n'anticipent pas les franchissements sur ce cas
  précis. L'anticipation démontrée par le POC vient de la **régression par segment**
  (pente courante), pas des limites statiques elles-mêmes — cohérent avec la remarque
  déjà présente en fin de prompt système, mais à noter explicitement ici car le chiffre
  contredit l'hypothèse implicite du critère de réussite (§ Critères de réussite du POC)
  selon laquelle le backtest produirait un chiffre d'anticipation favorable sur le premier
  cas testé.

## Contexte

FAMAT (usinage aéronautique, JV Safran/GE) veut réduire ses coûts de non-conformité :
1. **Détecter la dérive process** : anticiper les re-réglages machine avant que les cotes n'approchent les seuils de tolérance.
2. **Expliquer les non-conformités** : ex. corrélation déjà établie entre température atelier et redémarrages d'usinage (retour au chapitre 1 après palpage).

### Données disponibles

Dump CSV (`Payload-20260526.csv`, ~10 Mo) et, en cible, base SQL Cyllene alimentée par webhook (accès imminent). Format : une ligne par événement webhook.

| Colonne | Contenu |
|---|---|
| `WebhookMesure_Id` | id auto-incrément |
| `WebhookMesure_Contenu` | JSON `{"Serial", "Chapter", "cle", "value"}` |
| `WebhookMesure_DateCreation` | horodatage ms |

Profil du dump : 90 375 lignes, **94 pièces** (serials), 5 mois (2025-11-17 → 2026-04-27), chapitres 0→57 (+99), 109 clés distinctes.

Signaux clés identifiés :
- `CORRECTION_X` / `CORRECTION_Z` : correction outil par chapitre (99 % non nulles, ±0,15 mm) — **signal principal de dérive**
- `COTR_X` / `COTR_Z` : cotes mesurées (non nulles sur chapitres finaux uniquement)
- `JAUGE_X` / `JAUGE_Z`, `RAYON_OUT` : jauges outil, rayon
- `TEMP_PIECE` / `TEMP_ETALON` : 15 → 24,6 °C — variable de corrélation des redémarrages
- **Redémarrages** (chapitre décroissant dans la séquence) : présents sur 94/94 pièces, moyenne 5,4/pièce

### Contraintes

- **Pas de labels "non conforme"** : approche SPC (limites de contrôle statistiques), pas de ML supervisé.
- **Pas de référentiel de tolérances structuré** (schémas visuels non formalisés côté client) : le POC utilise des limites **±3σ** calculées par (clé, chapitre) ; le branchement d'un référentiel JSON client est prévu mais hors scope POC.
- **Pas de base intermédiaire** : ExeSQL se connecte directement à la base Cyllene où la donnée vit (topologie de prod dès le POC). Le CSV sert uniquement à prototyper le code d'analyse hors ligne.
- **Séparation des use cases** : rien n'est chargé dans le MariaDB/MySQL du stack RAGFlow.
- Souveraineté des données : tout tourne sur la plateforme Cyllene (LLM inclus), rien ne sort.

## Architecture

Hybride : **couche conversationnelle** (fondamentale, cœur de la démo) au-dessus de **recettes d'analyse déterministes**.

```
Utilisateur (chat)
   │
   ▼
Agent (composant tool-calling RAGFlow)
   │   prompt système = contexte métier FAMAT
   │   + 3 recettes d'analyse canoniques (SQL + code paramétrés)
   │
   ├─ tool: execute_sql  ──► base SQL Cyllene (table webhook)
   │        EXPLORATION LIBRE uniquement : agrégats, échantillons
   │        (les résultats repassent par le contexte LLM → jamais de bulk)
   │
   └─ tool: code_exec    ──► sandbox Python (image custom)
            module `famat_recipes` CUIT DANS L'IMAGE : va chercher
            les données à la source lui-même (load_db → base Cyllene),
            DuckDB in-memory : pivot, détection de segments
            (redémarrages), regr_slope/regr_intercept par segment,
            limites ±3σ ; matplotlib → PNG (cartes de contrôle)
            renvoyés en pièces jointes dans le chat.
            Le code passé par le LLM = appel de recette paramétré,
            jamais les 90k lignes dans le contexte.
   │
   ▼
Réponse : interprétation métier en français + graphiques
```

### Principe recettes vs improvisation

Les trois analyses phares suivent des **recettes structurées dans le prompt système** de l'agent (SQL et code quasi-figés, paramétrés par clé/chapitre/période) — le LLM route et paramètre, il n'invente pas la méthode. Les questions hors catalogue passent par `execute_sql` / `code_exec` libres : c'est le mode exploratoire, assumé comme tel.

Justification : la surveillance/alerte doit être déterministe et reproductible (client aéronautique) ; l'exploration conversationnelle est la valeur différenciante de la plateforme. Les recettes du POC deviennent le moteur d'alerte de la phase 1 (déclenchement webhook/cron, hors scope POC).

### Les 3 analyses canoniques

1. **Dérive par clé/chapitre** : série `value = f(pièce, temps)` pour une clé (ex. `CORRECTION_X`) à un chapitre donné, segmentée par cycle de réglage (détection des ruptures via redémarrages/sauts), régression linéaire **par segment** (les dents de scie interdisent une régression globale), pente exprimée en µm/pièce, extrapolation du franchissement des limites ±3σ.
2. **Backtest d'alertes** : rejeu de l'historique — « le système aurait alerté N pièces avant l'approche de la limite, avec X fausses alertes ». C'est le critère de succès chiffrable proposé à FAMAT.
3. **Température ↔ redémarrages** : reproduction/extension de la corrélation déjà trouvée par Cyllene (`TEMP_PIECE` vs occurrences de retour en arrière de chapitre), sur les 94 pièces.

### Choix techniques

- **DuckDB** dans CodeExec (pas pandas) : agrégats `regr_slope`/`regr_intercept` natifs → la régression par segment s'écrit en SQL ; fenêtrage pour la détection de segments ; nettement plus rapide. **Polars en repli** si DuckDB absent de l'image sandbox.
- **Extraction JSON** : selon le moteur de la base Cyllene (à découvrir), soit `JSON_VALUE`/`JSON_EXTRACT` côté SQL (idéalement une vue si les droits le permettent), soit parsing dans CodeExec.
- **`max_records` ExeSQL** : à monter au-delà du défaut 1024, ou pousser l'agrégation côté SQL pour rester sous la limite (préféré).
- **Graphiques** : matplotlib avec style SPC soigné (bandes ±3σ, points hors-contrôle marqués, régression par segment annotée en µm/pièce, marqueurs de re-réglage), via le mécanisme d'artefacts CodeExec (`_ARTIFACTS` → MinIO → pièces jointes chat). Export **SVG si le pipeline d'artefacts l'accepte** (vectoriel, net en projection — à tester en première tâche), sinon PNG 200 dpi. Seaborn écarté (même moteur, rien de structurel pour du SPC). Graphiques interactifs JS dans le chat (renderer custom de code-fence + recharts, comme le panel admin) : **phase 1**, pas POC — chantier frontend à part entière.

## Hors scope POC

- Déclenchement automatique (webhook d'invocation d'agent, cron) — phase 1.
- Référentiel de tolérances client (formalisation JSON des schémas) — chantier client co-construit.
- Alerting/notifications (email, etc.).
- Multi-machines / multi-séries de pièces (le dump = 1 machine, 1 série).

## Prérequis à vérifier avant implémentation

1. Sandbox CodeExec opérationnel dans l'environnement de démo, avec `duckdb` (sinon `polars`) et `matplotlib` dans l'image.
2. Accès réseau RAGFlow → base SQL Cyllene ; type de moteur (MySQL/Postgres/MSSQL/…) et droits (lecture seule suffit ; droit de créer une vue = bonus).
3. Un LLM du workspace avec tool-calling fiable pour le composant Agent.

## Critères de réussite du POC

- En chat, l'agent répond correctement aux 3 analyses canoniques sur les données réelles, graphiques inclus, en < ~1 min par analyse.
- Le backtest produit le chiffre « N pièces d'anticipation, X fausses alertes » — l'argument ROI de la réunion.
- Au moins une question hors catalogue traitée proprement en mode exploratoire pendant la démo.
- Démo rejouable de bout en bout sans intervention manuelle entre deux runs.

## Tests

- Prototypage et validation du code d'analyse hors ligne sur le dump CSV (mêmes requêtes DuckDB) avant branchement ExeSQL.
- Validation croisée : la corrélation température↔redémarrages retrouvée doit être cohérente avec le résultat déjà obtenu par Cyllene sur leur échantillon.
- Répétition générale de la démo sur l'environnement cible avec la vraie base.
