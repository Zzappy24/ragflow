Dans l'écosystème RAGFlow, il y a une mécanique fondamentale à comprendre : un Chat (l'interface conversationnelle) peut interroger plusieurs Datasets en même temps. Par conséquent, la bonne pratique absolue n'est pas de tout mettre dans un dataset géant, mais de séparer les datasets selon 3 axes stricts.

Voici la méthode "Enterprise" pour organiser un workspace, illustrée avec ton exemple de l'équipe "DEV Data".

Axe 1 : Séparer par Stratégie de Parsing (La contrainte technique)
C'est la règle d'or numéro 1 dans RAGFlow. Le système découpe (chunk) les documents différemment selon leur nature. Tu ne peux pas appliquer le même algorithme de découpage à un contrat PDF de 50 pages et à un fichier de code Python.

Si tu mélanges : RAGFlow appliquera une stratégie par défaut moyenne qui cassera la structure logique de ton code ou perdra le contexte de ton PDF.

La bonne pratique : Un dataset = Un type de structure de donnée.

Axe 2 : Séparer par "Périmètre de Vérité" (Projet / Contexte)
Le RAG est bête : si tu lui demandes "Comment se connecte-t-on à l'API ?", il va chercher le mot "API". S'il y a 5 projets différents dans le même dataset, il risque de ramener la documentation de l'API du Projet A pour répondre à une question sur le Projet B.

La bonne pratique : Un dataset = Une bulle d'information isolée qui ne concerne qu'un sujet ou un projet précis.

Axe 3 : Séparer par Fréquence de Mise à Jour (Cycle de vie)
Il y a la donnée froide (des manuels d'architecture qui ne changent jamais) et la donnée très chaude (des logs d'erreurs quotidiens ou des tickets Jira).

La bonne pratique : Séparer les données statiques des données dynamiques permet de ne réindexer (et donc consommer de la ressource) que ce qui est nécessaire.

📦 Cas concret : Le Workspace "DEV Data"
Si j'accompagne ton client qui crée le workspace "DEV Data", je vais lui imposer cette structure de Datasets (Knowledge Bases) :

1. Les Datasets Transverses (Le savoir global de l'équipe)

[Global] Docs Architectures & Standards (Type: PDF/Markdown) : Les règles de code de l'entreprise, les process de déploiement.

[Global] Manuels Librairies Externes (Type: Web/Q&A) : Les docs techniques des outils qu'ils utilisent (Pandas, Scikit-learn, etc.).

2. Les Datasets par Projet (Isolés)

[Projet Algo-Pricing] - Spécifications Métier (Type: Word/PDF) : Les règles de calcul données par le métier.

[Projet Algo-Pricing] - Base de Code (Type: Code) : Les scripts Python et requêtes SQL du projet.

[Projet Churn-Prediction] - Documentation (Type: Wiki/Markdown) : Tout ce qui concerne ce deuxième projet.

Comment s'en servir ensuite au niveau du Chat ?
C'est là que la magie opère. Quand le développeur créera un assistant conversationnel (Chat) pour travailler sur le pricing, il cochera les datasets suivants pour son chatbot :
✅ [Global] Docs Architectures & Standards
✅ [Projet Algo-Pricing] - Spécifications Métier
✅ [Projet Algo-Pricing] - Base de Code