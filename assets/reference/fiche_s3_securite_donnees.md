# Référentiel interne — Collaborateur sous fiche S3

> Document de référence pour la rédaction de procédures internes Cyllene.
> Sujet : encadrement de l'embauche d'un collaborateur dont la fonction implique
> un accès aux données et aux outils de **niveau de sensibilité 3 (S3)** —
> niveau le plus élevé du référentiel interne de classification.
> Aligné sur les exigences ISO 27001, SOC 2 Type II, SecNumCloud (ANSSI),
> HDS, RGS, RGPD, et la politique de souveraineté des outils Cyllene.

---

## 1. Contexte et enjeux

Cyllene opère sur des périmètres soumis à des exigences de sécurité élevées :
infrastructures clients régulés (santé, finance, secteur public), hébergement
souverain, données à fort impact métier. Tout collaborateur dont la fonction
implique un accès permanent aux données ou aux systèmes les plus sensibles
relève du niveau **S3** du référentiel interne de classification des
collaborateurs.

L'embauche d'un collaborateur sous fiche S3 mobilise un dispositif renforcé
couvrant : vérifications préalables, contrôles d'accès, formation, suivi,
levée d'accès. La présente procédure formalise ce dispositif de bout en bout
et garantit la conformité aux référentiels ci-après.

---

## 2. Classification interne des collaborateurs

Cyllene classifie ses collaborateurs en trois niveaux selon la sensibilité
des données manipulées et la criticité des systèmes accédés :

| Niveau | Périmètre | Exemples de fonctions |
|---|---|---|
| **S1** | Données publiques, environnements de tests, postes administratifs sans accès sensible | Marketing, communication, fonctions support non IT |
| **S2** | Données internes, environnements de pré-production, accès clients standards | Développeurs sur projets internes, support N1, RH |
| **S3** | Données critiques clients régulés, infrastructures de production, outils certifiés, secrets et clés cryptographiques | Administrateurs systèmes production, ingénieurs SRE, RSSI, équipes infrastructure souveraine, équipes IA souveraine, équipes données santé/finance |

Le rattachement à un niveau est décidé conjointement par la **DRH**, le
**RSSI** et le **manager opérationnel** lors de la définition du poste.

---

## 3. Référentiels applicables

Une procédure d'embauche S3 conforme s'aligne sur les référentiels suivants :

- **ISO/IEC 27001** : management de la sécurité de l'information (clauses 7
  Ressources humaines : avant emploi, pendant emploi, fin/changement d'emploi).
- **ISO/IEC 27002** : code de bonnes pratiques (contrôles 6.1 à 6.7 sur le
  personnel).
- **SOC 2 Type II** : critères Common Criteria CC1.4 (recrutement), CC1.5
  (formation), CC2.3 (communication des politiques).
- **SecNumCloud (ANSSI)** : référentiel d'exigences pour la qualification des
  services cloud — section sur le personnel d'exploitation et la souveraineté.
- **HDS (Hébergeur de Données de Santé)** : exigences renforcées pour les
  collaborateurs accédant à des données de santé.
- **RGS (Référentiel Général de Sécurité)** : applicable aux interactions
  avec l'administration française.
- **RGPD / Loi Informatique et Libertés** : tenue d'un registre des traitements
  RH, base légale du contrôle d'embauche, durée de conservation des dossiers.
- **Charte informatique Cyllene** : charte interne signée à l'embauche.
- **PSSI Cyllene** (Politique de Sécurité des Systèmes d'Information) :
  document interne maître.

---

## 4. Politique de souveraineté des outils

Cyllene applique une politique stricte de **souveraineté numérique** pour les
collaborateurs S3. Les principes sont :

- **Aucun outil d'IA générative non souverain** ne peut être utilisé dans le
  cadre de la fonction (interdiction explicite de ChatGPT public, Microsoft
  Copilot grand public, Gemini, Claude grand public, etc.).
- **Modèles d'IA exclusivement locaux ou hébergés sur cloud souverain** (LLM
  on-prem, infrastructure SecNumCloud).
- **Suite bureautique** : alternatives souveraines privilégiées ; usage de
  Microsoft 365 ou Google Workspace soumis à validation RSSI et matrice de
  classification de la donnée traitée.
- **Outils de communication** : messagerie souveraine (ex. Olvid, Tchap selon
  périmètre). Slack et Teams interdits pour les échanges classifiés S3.
- **Stockage** : datacenters Cyllene souverains exclusivement ; pas de Dropbox,
  Google Drive, OneDrive personnel.
- **Code source et secrets** : forge interne (GitLab self-hosted) ; pas de
  GitHub public ; secrets dans Vault interne (HashiCorp Vault ou équivalent).
- **CI/CD et orchestration** : runners et clusters internes ; pas de SaaS
  externe non DPA-validé.
- **Endpoint** : poste fourni Cyllene, durci selon le baseline S3, EDR
  obligatoire (SentinelOne / CrowdStrike interne), chiffrement disque (FileVault
  / BitLocker), MDM.
- **BYOD strictement interdit** sur les périmètres S3.
- **Tout nouvel outil** doit passer un processus de qualification sécurité
  (analyse de risque + DPA si données personnelles + classification de la
  donnée traitée) avant déploiement.

---

## 5. Procédure d'embauche pas à pas

### 5.1 Phase pré-embauche — Définition du poste et qualification S3

- Le **manager** rédige la fiche de poste avec mention explicite des accès
  envisagés et de la classification de données.
- Le **RSSI** valide le rattachement au niveau S3 sur la base de la matrice
  d'accès et confirme la pertinence du dispositif renforcé.
- La **DRH** documente la décision dans le SIRH (champ « Niveau de
  classification : S3 », horodatage, signataires).
- Une **fiche d'analyse de risque RH** est jointe au dossier (sensibilité des
  données, criticité des systèmes, durée d'exposition, scope géographique).

### 5.2 Phase de recrutement — Vérifications préalables

- **Entretien sécurité** dédié, mené par le RSSI ou son délégué, couvrant :
  parcours, expériences en environnement régulé, sensibilisation aux risques,
  cohérence du projet professionnel.
- **Vérifications administratives** :
  - Identité (pièce officielle, vérification d'authenticité).
  - Diplômes et certifications déclarées (vérification directe auprès des
    organismes ou via plateformes type Diplome.gouv.fr / EuropaSearch).
  - Casier judiciaire B3 (extrait fourni par le candidat, vérification de
    cohérence avec le poste).
  - **Références professionnelles** : minimum 2 références appelées, traces
    écrites conservées.
- **Background check étendu** (selon poste et exigences clients) :
  - Vérifications réseaux sociaux publics (cohérence du discours, pas
    d'engagements à risque).
  - Vérification absence de sanctions financières publiques (Banque de France,
    sanctions internationales — UE, OFAC).
  - **Pour les postes en relation avec des clients défense / OIV** :
    coordination avec l'officier de sécurité client si habilitation
    secondaire requise.
- **RGPD** : consentement explicite du candidat pour l'ensemble des
  vérifications, base légale documentée (intérêt légitime + obligation
  contractuelle), durée de conservation des données 5 ans après décision
  d'embauche.

### 5.3 Constitution du dossier d'embauche

- Promesse d'embauche **conditionnée** à la validation du dispositif sécurité
  (clause type « sous réserve de signature de la charte sécurité, du NDA, de
  la charte informatique et de la complétude du dossier sécurité »).
- **NDA renforcé S3** : engagement de confidentialité incluant clauses
  spécifiques sur les secrets industriels, codes sources clients, données
  personnelles RGPD, clauses post-emploi (5 ans après le départ).
- **Charte informatique Cyllene** signée.
- **Charte de l'éthique IA Cyllene** signée (interdictions outils non
  souverains, signalement obligatoire d'usage de tout outil tiers).
- **Engagement de signalement** des conflits d'intérêts (participations,
  mandats, prestations annexes).
- **Volet RGPD** : information préalable du candidat sur les traitements RH le
  concernant, droits CNIL, contact DPO.

### 5.4 Onboarding — Premiers jours

- **Provisioning du poste** par l'équipe IT selon le baseline S3 :
  - Endpoint chiffré, durci, EDR déployé, MFA matériel (clé FIDO2 fournie).
  - Compte AD / IAM créé avec **moindre privilège** par défaut, accès
    nominatifs uniquement, pas de comptes partagés.
  - VPN / ZTNA configuré, certificats individuels.
  - Accès au coffre-fort de secrets internes, MFA obligatoire.
- **Création du compte SIRH** avec le tag S3, déclenchement automatique de la
  matrice de formation associée.
- **Briefing sécurité initial** par le RSSI (1 à 2h) : politique de
  classification, gestion des incidents, points de contact, hotline.
- **Remise du livret d'accueil sécurité S3** (procédures critiques, contacts
  d'urgence, schéma d'escalade).

### 5.5 Formation obligatoire

- **Module 1 — Fondamentaux sécurité** (2h, e-learning) : phishing, mots de
  passe, ingénierie sociale, gestion des supports amovibles, télétravail
  sécurisé.
- **Module 2 — Classification de la donnée et souveraineté** (1h, présentiel
  ou visio sur outil souverain) : règles internes S1/S2/S3, politique de
  souveraineté des outils, IA générative — interdictions et alternatives.
- **Module 3 — RGPD et données personnelles** (1h, e-learning) : bases
  légales, droits des personnes, registre des traitements, gestion d'une
  violation de données.
- **Module 4 — Spécifique métier** (durée variable) selon poste : sécurité
  développement (OWASP, secure coding), opérations cloud souverain,
  administration HDS, etc.
- **Évaluation post-formation** obligatoire (QCM, score minimal 80 %) ;
  rattrapage si échec.

### 5.6 Mise en production progressive

- **Période de bridage** de 30 jours : accès aux environnements de production
  S3 délivrés progressivement, sous supervision du tuteur sécurité désigné.
- **Pair-review obligatoire** sur les premières actions critiques (déploiements
  prod, accès données clients, modifications de configuration sensibles).
- **Revue à 30 jours** par le manager + RSSI pour valider la levée du bridage.

### 5.7 Suivi pendant le contrat

- **Revue d'accès trimestrielle** (RBAC review) : confirmation de la
  pertinence des droits attribués, retrait des accès inutilisés depuis 90
  jours.
- **Sensibilisation continue** : communication mensuelle (newsletter sécurité,
  alertes phishing, retours d'expérience incidents).
- **Audits annuels internes** : entretien individuel sécurité, mise à jour
  des engagements, vérification des outils utilisés.
- **Tests d'intrusion humaine** (phishing simulé, tailgating) : participation
  obligatoire, restitution individuelle.
- **Mise à jour de la fiche** en cas de changement significatif : changement
  de poste, prise de responsabilité, nouvelle exposition à un client régulé.

### 5.8 Levée d'accès et fin de contrat

- **Préavis** : déclenchement du processus de levée 5 jours ouvrés avant le
  départ effectif.
- **Restitution intégrale** : poste, clé MFA, badge, documents physiques,
  certificats personnels.
- **Révocation systématique** des accès dans tous les systèmes (IAM, VPN,
  Vault, dépôts code, applications métiers).
- **Entretien de fin de contrat** par le RSSI : rappel des engagements
  post-emploi, signature du procès-verbal de restitution.
- **Engagement post-emploi S3** : confidentialité maintenue 5 ans après le
  départ ; non-débauchage des collaborateurs S3 sur 12 mois ; obligation de
  notification en cas de mise en cause judiciaire ultérieure liée au
  périmètre Cyllene.
- **Archivage** du dossier RH selon la durée légale (5 ans) ; archivage
  spécifique des éléments sensibles selon politique RSSI (10 ans en archive
  sécurisée).

---

## 6. Rôles et responsabilités

| Acteur | Mission |
|---|---|
| **DRH** | Pilotage de la procédure, conformité RGPD, archivage du dossier |
| **Manager direct** | Définition du périmètre fonctionnel, demande d'accès, suivi opérationnel |
| **RSSI** | Validation du rattachement S3, briefing sécurité, audit annuel, levée des accès |
| **DPO** | Conformité RGPD du processus, registre des traitements, droits des personnes |
| **Équipe IT** | Provisioning du poste, IAM, EDR, MFA, journalisation |
| **Tuteur sécurité** | Encadrement du collaborateur pendant la période de bridage 30 jours |
| **Direction Générale** | Validation des cas dérogatoires, arbitrage en cas d'écart |

---

## 7. Indicateurs et reporting

Le dispositif S3 est piloté par les indicateurs suivants, revus trimestriellement
en comité sécurité :

- Délai moyen entre validation S3 et premier accès productif (cible : ≤ 10
  jours ouvrés).
- Taux de complétion des formations obligatoires à 30 jours (cible : 100 %).
- Score moyen aux QCM post-formation (cible : ≥ 90 %).
- Taux de réussite aux tests phishing internes (cible : ≥ 95 %).
- Nombre de revues d'accès réalisées dans les délais (cible : 100 %).
- Nombre d'incidents de sécurité imputables à un collaborateur S3 (cible : 0).
- Délai moyen de révocation des accès en sortie (cible : ≤ 1 jour ouvré).

---

## 8. Sanctions et escalade

Le manquement aux règles applicables aux collaborateurs S3 est traité selon
la matrice suivante :

| Type de manquement | Réponse |
|---|---|
| Usage d'un outil non souverain non déclaré (LLM cloud, stockage perso) | Avertissement, formation correctrice, blocage technique |
| Partage non autorisé d'information classifiée S3 | Sanction disciplinaire (du blâme au licenciement) ; signalement CNIL si données personnelles |
| Compromission volontaire ou par négligence grave | Licenciement pour faute lourde, action en responsabilité, dépôt de plainte |
| Manquement aux engagements post-emploi | Action judiciaire, dommages et intérêts contractuels |

Toute violation est notifiée au RSSI dans l'heure, avec déclenchement
automatique de la cellule de gestion d'incident si l'impact est avéré.

---

## 9. Spécificités liées aux outils d'IA générative

Étant donné l'enjeu stratégique de la souveraineté numérique chez Cyllene, les
collaborateurs S3 sont particulièrement encadrés sur l'usage de l'IA :

- **Interdiction absolue** d'utiliser un service d'IA générative grand public
  hébergé hors UE pour traiter de la donnée Cyllene ou client.
- **Outils internes autorisés** : LLM Cyllene on-prem (Qwen, Mistral, Llama
  selon stack), plateforme RAG souveraine, agents internes.
- **Outils externes sous DPA validé** : possibles uniquement après qualification
  RSSI + DPA signé + classification du périmètre de données autorisé.
- **Logs et traçabilité** : tout prompt soumis à un LLM (interne ou externe
  qualifié) est journalisé pour audit.
- **Formation IA obligatoire** : module dédié au démarrage couvrant les biais,
  la fuite de données par prompt, la propriété intellectuelle des sorties,
  les bonnes pratiques de prompt engineering responsable.

---

## 10. Glossaire

- **B3** : extrait n° 3 du casier judiciaire (seul accessible au candidat lui-même).
- **DPA** : Data Processing Agreement, accord de traitement RGPD.
- **DPO** : Data Protection Officer, délégué à la protection des données.
- **EDR** : Endpoint Detection and Response, surveillance comportementale du poste.
- **FIDO2** : standard d'authentification matérielle forte.
- **HDS** : Hébergeur de Données de Santé (certification ANS).
- **IAM** : Identity and Access Management.
- **MFA** : Multi-Factor Authentication.
- **NDA** : Non-Disclosure Agreement, accord de confidentialité.
- **PSSI** : Politique de Sécurité des Systèmes d'Information.
- **RBAC** : Role-Based Access Control, contrôle d'accès par rôles.
- **RGS** : Référentiel Général de Sécurité (ANSSI).
- **RSSI** : Responsable de la Sécurité des Systèmes d'Information.
- **SecNumCloud** : référentiel ANSSI de qualification cloud souverain.
- **SIRH** : Système d'Information Ressources Humaines.
- **SOC 2** : référentiel d'audit pour service organizations (AICPA).
- **ZTNA** : Zero Trust Network Access.
