# Démo FAMAT — déroulé

POC détection de dérive process (usinage aéronautique). Agent canvas RAGFlow
(`FAMAT — Dérive process`, id `a01649f6958a11f192717178b890f94c`, workspace
`famat-poc-task11` / `3f20f150958a11f187bf29b64cd89c66`), LLM `qwen-code` (via LiteLLM,
`OpenAI-API-Compatible`), tool `code_exec` (sandbox self-managé, module
`famat_recipes` pré-installé), données servies depuis MinIO (`Payload-20260526.csv`,
révision Task 10 — en attendant l'ouverture des flux réseau prod vers la base Cyllene).

## Setup (avant la réunion)

- [ ] Stack dev up : `scripts/dev_up.sh` (API `:9380` + task_executor). Vérifier :
      `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9222/` → `200`.
- [ ] Sandbox executor-manager up : `cd agent/sandbox && docker compose ps` → tous les
      conteneurs `Up` (1 manager + 3 python + 3 nodejs). Healthz :
      `curl -s http://localhost:9385/healthz` → `{"status":"ok"}`.
- [ ] **Si le pool sandbox a été reconstruit** (rebuild de l'image
      `sandbox-base-python:latest` après une modif de `famat_recipes.py`, ou tout
      autre rebuild d'image sandbox) : les conteneurs du pool existant tournent
      encore sur l'ANCIENNE image tant qu'ils ne sont pas recréés — un rebuild seul
      ne suffit pas. Procédure :
      ```bash
      docker rm -f sandbox_python_0 sandbox_python_1 sandbox_python_2 \
                   sandbox_nodejs_0 sandbox_nodejs_1 sandbox_nodejs_2
      cd agent/sandbox && docker compose up -d --no-build   # recrée le pool depuis l'image courante
      docker compose ps   # vérifier 6/6 Up
      curl -s http://localhost:9385/healthz
      ```
- [ ] MinIO up, CSV accessible : `curl -s -o /dev/null -w '%{http_code}\n'
      http://localhost:9000/famat-poc/Payload-20260526.csv` → `200`.
- [ ] Provider sandbox testé côté admin (si reconfiguration nécessaire — normalement
      déjà persisté en base, pas besoin de repasser par là sauf changement d'environnement) :
      route `/api/v1/admin/sandbox/test`, servie par `admin/server/admin_server.py`
      (Flask legacy, port **9381**). **Attention collision de port** : `scripts/dev_up.sh
      --full` démarre sur ce même port 9381 notre backend admin custom
      (`management/server/main.py`, FastAPI, préfixe `/api/admin` sans `v1`) — les deux
      serveurs ne peuvent pas tourner simultanément sur 9381. Si une reconfiguration du
      provider sandbox est nécessaire pendant la démo (ex. changement d'endpoint) :
      arrêter `management/server` le temps de lancer `admin/server/admin_server.py`
      manuellement (`python admin/server/admin_server.py`), faire le changement via
      `POST /api/v1/admin/sandbox/config` (persisté dans `system_settings`, actif
      immédiatement sans redémarrage de l'API principale), puis arrêter
      `admin_server.py` et relancer `management/server` si les routes `/api/admin/*`
      sont nécessaires ensuite (workspaces, membres…). **Hors reconfiguration, ne pas
      lancer `admin_server.py` du tout** — la config est déjà en base, active.
- [ ] Connectivité base Cyllene : **non applicable en l'état** — le POC charge le CSV
      via MinIO (`fr.load_url`), pas de connexion base Cyllene en direct (flux réseau
      prod pas encore ouverts, cf. `README.md` § Source de données). Ne pas tester une
      connexion base pendant la démo.
- [ ] Chat vierge ouvert sur l'agent « FAMAT — Dérive process » (nouvelle session à
      chaque question de démo pour éviter tout effet d'historique).
- [ ] Rappel opérateur : le modèle `qwen-code` est un modèle *reasoning*, parfois lent
      à démarrer (5-20 s de réflexion avant le premier token visible) — normal, ne pas
      s'inquiéter d'un silence initial.
- [ ] **Playbook anti-échec (lire avant la démo)** : sur 3 répétitions chronométrées
      du déroulé complet en Task 12, 2 passes sur 3 se sont déroulées sans aucun accroc
      (4/4 questions, premier essai). Sur la 3e passe, 2 questions sur 4 ont échoué au
      premier essai avec le même symptôme : réponse coupée après ~16 s, message
      `**ERROR**: CONNECTION_ERROR - peer closed connection...` — un problème
      d'infrastructure côté LiteLLM/vLLM (connexion coupée en cours de génération),
      **pas** une dérive de recette (le modèle avait correctement identifié la bonne
      recette dans son raisonnement avant la coupure, sans jamais inventer de chiffre).
      **Réflexe opérateur** : si aucune carte/aucun chiffre n'apparaît dans les ~20
      premières secondes, ou si le message se termine par `**ERROR**`, reposer
      IMMÉDIATEMENT la même question dans la même session (bouton renvoyer / retaper).
      Sur les cas observés en Task 12, 1 à 5 tentatives ont suffi à obtenir une
      réponse correcte à chaque fois — jamais de blocage total. Ne jamais interpréter
      un échec de ce type comme "le modèle ne sait pas répondre" : c'est un problème de
      transport réseau, pas de raisonnement.

## Mise en garde métier (ledger) — à dire explicitement pendant la démo

**Les limites ±3σ (Shewhart) affichées sur la carte de contrôle sont des limites
STATIQUES, peu sensibles à une dérive lente.** Une pente faible mais régulière peut
mettre des centaines de pièces à franchir ces limites — le temps que l'alerte Shewhart
se déclenche, la dérive est déjà installée depuis longtemps. **L'anticipation réelle de
ce POC vient de la régression par segment (pente courante, extrapolée en "pièces
restantes avant limite")**, pas des limites ±3σ elles-mêmes. À présenter explicitement
comme telle : « les limites vous disent qu'il y a un problème, la régression vous dit
qu'il va y en avoir un ». Le backtest (Recette 2) est la preuve chiffrée de cette
distinction : les alertes générées par un seuil fixe sur historique glissant ratent le
franchissement réel sur cet échantillon (cf. chiffres ci-dessous).

## Déroulé (questions dans l'ordre)

1. **« Analyse la dérive de CORRECTION_X au chapitre 10 »**
   → attendu : carte SPC (image jointe), 93 pièces, 1 seul segment, pente
   quasi nulle (+0,000033 µm/pièce), aucun point hors limites, process stable,
   ~4 143 pièces avant franchissement à la pente actuelle. Temps observé :
   **13-27 s** (3 passes chronométrées, cf. tableau ci-dessous).

2. **« Fais le backtest des alertes sur cette cote »** (ou reformulé avec le nom
   complet : « … sur la cote CORRECTION_X au chapitre 10 »)
   → attendu : 2 alertes générées (pièces 36 et 86), 1 seul franchissement réel
   (pièce 75), 0 vraie alerte anticipée, 2 fausses alertes, 1 franchissement manqué.
   **Message clé à faire ressortir** : le backtest prouve que le seuil fixe
   Shewhart n'a anticipé aucun franchissement réel sur cet échantillon — d'où
   l'intérêt de la régression par segment (cf. mise en garde ci-dessus). Temps
   observé : **14-46 s**.

3. **« Montre la corrélation entre température et redémarrages »**
   → attendu : 495 redémarrages sur 94/94 pièces, température moyenne au
   redémarrage 19,61 °C vs 20,02 °C en moyenne générale, corrélation −0,073
   (quasi nulle). **Message clé** : contredit l'annonce initiale du partenaire —
   voir § Corrélation température ci-dessous pour l'argumentaire complet. Temps
   observé : **29-67 s** — la recette la plus lente et la moins stable des 3
   (le modèle explore/corrige plusieurs pistes avant de converger ; les chiffres
   restent identiques une fois convergés).

4. **Question libre du client (exploration execute_sql)** → montrer la trace
   d'exécution (logs serveur `[ToolCall] invoke/done`, ou `return_trace` dans la
   réponse API) pour prouver que la réponse vient d'un calcul réel sur les vraies
   données, pas d'une estimation. Deux questions testées en Task 12, cf. § Question
   libre ci-dessous.

5. Teaser phase 2 : KB gammes/procédures croisée avec la dérive.

### Chronométrage des 3 passes complètes (Task 12, sessions fraîches)

| Passe | Recette 1 | Recette 2 | Recette 3 | Question libre | Total | Incident |
|---|---|---|---|---|---|---|
| 1 | 13,7 s | 14,4 s | 61,4 s (4 tool calls — auto-correction sur erreurs de code) | 100,1 s (7 tool calls — exploration schéma) | **189,7 s** | Aucun échec, mais 2 questions ont nécessité plusieurs tool calls internes avant de converger |
| 2 | 12,8 s | 14,1 s | 29,4 s | 19,8 s | **76,2 s** | Aucun — 4/4 en un seul tool call chacune, la plus propre des 3 passes |
| 3 | 16,3 s → **échec** (CONNECTION_ERROR), 26,6 s au retry | 16,3 s → **échec** ×4 (CONNECTION_ERROR ×3, GENERIC_ERROR ×1), 45,7 s au 5e essai | 67,1 s | 65,9 s | **165,6 s** (hors retries) | 2 questions sur 4 ont dû être reposées ; convergent toujours vers les mêmes chiffres une fois exécutées |

**Constat de stabilité** : quand une réponse aboutit, les chiffres sont **strictement
identiques** entre les 3 passes (93 pièces, mean=−0,0215, sigma=0,0392, alertes
pièces 36/86, franchissement pièce 75, 495 redémarrages, corr=−0,073…) — seule la
rédaction du rapport varie, comme attendu d'un code déterministe derrière un LLM.
Le seul aléa porte sur le NOMBRE DE TENTATIVES nécessaires pour obtenir une réponse
(1 à 5 selon les cas), pas sur le CONTENU une fois obtenue — voir le playbook
anti-échec en § Setup et le § Durcissement dans `task-12-report.md` pour le détail
de l'investigation.

## Corrélation température : état des lieux

Le partenaire a annoncé, sur un petit échantillon en phase pilote, une corrélation
température ↔ redémarrages qui « expliquait l'intégralité des redémarrages ». Sur le
dataset complet (94 pièces, 90 375 événements), **cette corrélation ne se reproduit
pas**, quelle que soit la définition testée offline (`uv run --with duckdb`, script
d'investigation, résultats ci-dessous) :

| Définition testée | Résultat | Verdict |
|---|---|---|
| **Baseline actuelle** (tout retour de chapitre, `famat_recipes.temperature_restarts`) | n=495 redémarrages, 94/94 pièces concernées, temp. moyenne au redémarrage 19,61 °C vs 20,02 °C en moyenne générale, **corr = −0,073** | Quasi nulle |
| **(a) Redémarrages vers le chapitre 1 spécifiquement** (pas tout retour arrière) | n=50 redémarrages, mais seulement **6 pièces sur 94** concernées, temp. moyenne 19,30 °C, **corr = −0,119** | Toujours quasi nulle, et échantillon trop petit (6 séries) pour être concluant |
| **(b) Température instantanée aux redémarrages vs seuils extrêmes** (<18 °C ou >22 °C) | Aux redémarrages : 3,6 % sous 18 °C, 0,8 % au-dessus de 22 °C, 94,8 % dans [18-22]. **Sur l'ensemble des mesures TEMP_PIECE (hors redémarrage)** : 3,8 % / 0,9 % / 95,3 % — proportions quasi identiques | Aucune sur-représentation des redémarrages aux températures extrêmes |
| **(c) Temps perdu (durée entre le redémarrage et le retour au chapitre atteint) vs température** | n=326 redémarrages appariés à une récupération mesurée, **corr(temps perdu, température) = +0,083** | Quasi nulle, signe même opposé à l'intuition du partenaire |
| **Bonus — écart de moyenne (test de Welch)** | Moyenne au redémarrage (19,61 °C, n=420) vs moyenne globale (20,02 °C, n=1017) : t = −5,68, **Cohen's d = −0,32** (effet petit-moyen, statistiquement détectable vu le grand n) | Un effet directionnel réel mais MODESTE existe (redémarrages légèrement plus fréquents à température un peu plus fraîche) — très loin d'« expliquer l'intégralité des redémarrages » |
| **Simulation petit échantillon** (15 premiers redémarrages chronologiques, 8 pièces — reproduit la taille d'un pilote) | corr = 0,06 | Ne reproduit pas non plus une forte corrélation sur cet échantillon précis (ne prouve pas l'hypothèse d'artefact de petit échantillon, mais ne la contredit pas — un tirage différent aurait pu montrer autre chose par hasard) |

Script d'investigation : `uv run --with duckdb --with matplotlib python3 <script>`
sur `poc/famat/data/Payload-20260526.csv` (non commité, `.gitignore`), reproductible
localement.

**Position à tenir en réunion : la corrélation annoncée par le partenaire est
INFIRMÉE à l'échelle du dataset complet.** Un effet directionnel modeste et
statistiquement détectable existe (les redémarrages surviennent en moyenne à une
température très légèrement plus fraîche que la moyenne du process), mais son
ampleur est sans commune mesure avec « expliquer l'intégralité des redémarrages ».
L'hypothèse la plus probable : l'observation pilote portait sur un échantillon trop
petit pour être représentative (effet de sélection / bruit statistique amplifié à
petite échelle). Ne pas reprendre l'affirmation du partenaire telle quelle sans la
nuancer avec ces chiffres.

## Question libre (exploration execute_sql)

Le tool `code_exec` accepte toute question data hors des 3 recettes canoniques, via
une requête SQL DuckDB agrégée sur `events(seq, serial, chapter, cle, value_num, ts)`.
Montrer la trace d'exécution (`return_trace` / logs serveur `[ToolCall] invoke`) pour
prouver que la réponse vient d'un calcul réel, pas d'une estimation du modèle.

**Question testée 1 — « Combien de pièces ont été usinées au total, et sur quelle
plage de dates ? »**
→ Réponse obtenue (Pass 2, 19,8 s, 1 tool call SQL agrégé) : 94 pièces, du
2025-11-17 au 2026-04-27 (161 jours), cadence moyenne ≈0,58 pièce/jour, cohérent
avec une série courte aéronautique (usinage individuel, cycles longs).

**Question testée 2 — « Quelle est la plage de température de l'atelier observée
sur l'ensemble des pièces ? »**
→ Réponse obtenue (17,4 s, 2 tool calls) : min 15,0 °C, max 27,0 °C (amplitude
12 °C), moyenne 20,93 °C sur 2 034 mesures (`TEMP_PIECE` + `TEMP_ETALON`
combinées — 1 017 mesures chacune). Chiffres vérifiés indépendamment contre le
CSV source (concordance exacte).
**Point de vigilance découvert en Task 12** : sur cette question précise, un
premier essai (avant un dernier tour de durcissement du prompt) avait produit
une réponse chiffrée **entièrement fabriquée** — le tool avait bien été appelé
(pas de violation de la règle n°1), mais le CODE écrit par le modèle générait
lui-même des données simulées (`sqlite3` en mémoire avec des valeurs inventées)
au lieu de charger le vrai CSV via `fr.load_url(...)`, après plusieurs tentatives
infructueuses pour localiser un fichier de données inexistant. Un tour de
durcissement supplémentaire (règle n°1 étendue : interdiction explicite de
générer des données synthétiques dans le code exécuté, retour obligatoire à
`fr.load_url(DATA_URL)` en cas d'échec) a corrigé ce cas précis à la
tentative suivante — mais cette classe de déviation (tool call réel sur données
fabriquées) est distincte de la hallucination "sans tool call" et mérite une
vigilance spécifique pour toute question libre qui sort des 3 recettes
canoniques. Ne PAS considérer les questions libres comme aussi fiables que les
3 recettes pinnées : vérifier le nombre de mesures/pièces retourné contre les
ordres de grandeur connus (94 pièces, ~90 000 événements, ~1000 mesures de
température) avant de citer un chiffre en réunion.

## Teaser phase 2

KB gammes/procédures (documents qualité, gammes d'usinage FAMAT) croisée avec la
détection de dérive : l'agent pourrait, à terme, citer directement la procédure
applicable (ex. « gamme X, opération Y ») quand une dérive est détectée sur une cote
qu'elle contrôle — nécessite l'ingestion des documents gammes dans une KB RAGFlow
classique, orchestrée par un composant Retrieval en amont du composant Agent actuel.
Hors scope de ce POC (fiche S3 / dérive process uniquement).

## Chiffres de référence (validés offline, Tasks 3-5 + Task 12)

Ces chiffres sont calculés directement par `famat_recipes.py` sur le CSV complet
(90 375 événements, 94 pièces) — reproductibles avec
`uv run --with duckdb python -m pytest poc/famat/tests/ -v` et servent de vérité
terrain pour valider que l'agent restitue les bons chiffres (le code est
déterministe, seule la rédaction du LLM varie d'un run à l'autre).

### Recette 1 — Dérive CORRECTION_X, chapitre 10
- n_parts = 93, mean = −0,0215, sigma = 0,0392, LCL = −0,1391, UCL = 0,0961
- 1 seul segment (aucune rupture détectée sur la série)
- pente courante = +0,000033 µm/pièce (quasi nulle), dernière valeur = −0,0408
- beyond_limits (dernier point) = false, ≈4 143 pièces avant franchissement à la
  pente actuelle

### Recette 2 — Backtest CORRECTION_X, chapitre 10
- n_parts = 93, alertes = pièces 36 et 86 (2 alertes), franchissement réel = pièce 75
  (1 crossing)
- true_alerts = 0, false_alerts = 2, missed_crossings = 1
- Illustration concrète de la mise en garde ci-dessus : le seuil fixe Shewhart sur
  historique glissant n'a anticipé aucun franchissement réel sur cet échantillon.

### Recette 3 — Température ↔ redémarrages
- n_restarts = 495, n_serials_with_restart = 94/94 (toutes les pièces ont au moins un
  redémarrage)
- mean_temp_at_restart = 19,61 °C vs mean_temp_overall = 20,02 °C
- corr_restarts_temp = −0,073 (quasi nulle — voir § Corrélation température ci-dessus)
