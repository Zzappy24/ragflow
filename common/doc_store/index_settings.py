"""CUSTOM B2B SaaS — shards / réplicas des index ES pilotés par l'environnement.

Un index par workspace (chunks + doc_meta). Le nombre de shards PRIMAIRES est
figé à la création d'un index et ne se change plus sans reindex/shrink ; le
fichier upstream ``conf/mapping.json`` en crée 2, réglage pensé mono-tenant
(un seul index qui grossit). Chez nous chaque workspace consomme donc 4 shards
sur un nœud plafonné à 1000 par défaut (``cluster.max_shards_per_node``), sans
gain : un workspace tient dans un shard. Les réplicas se changent à chaud sur
les index existants, mais un réplica sans second nœud = cluster jaune.

Variables lues à CHAQUE création d'index (api et task-executor) :

- ``ES_NUMBER_OF_SHARDS``   entier >= 1, primaires par index (défaut : mapping)
- ``ES_NUMBER_OF_REPLICAS`` entier >= 0, réplicas par index (défaut : mapping)

Vide/absente = valeur du mapping ; invalide = ignorée avec un warning, jamais
bloquant. N'affecte que les index créés APRÈS le déploiement.
"""
import copy
import logging
import os

logger = logging.getLogger("ragflow.es_conn")

_ENV_KEYS = (
    ("ES_NUMBER_OF_SHARDS", "number_of_shards", 1),
    ("ES_NUMBER_OF_REPLICAS", "number_of_replicas", 0),
)


def apply_env_index_settings(settings: dict | None, environ=None) -> dict:
    """Retourne une COPIE des settings d'index avec les surcharges d'environnement.

    Les clés sont posées sous ``settings["index"]`` (forme du mapping upstream),
    que la clé existe déjà ou non. Le dict d'entrée n'est jamais modifié.
    """
    env = os.environ if environ is None else environ
    out = copy.deepcopy(settings) if settings else {}
    for env_key, setting, minimum in _ENV_KEYS:
        raw = (env.get(env_key) or "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            logger.warning("%s=%r ignoré : entier attendu", env_key, raw)
            continue
        if value < minimum:
            logger.warning("%s=%d ignoré : minimum %d", env_key, value, minimum)
            continue
        index_block = out.setdefault("index", {})
        if index_block.get(setting) != value:
            logger.info("index settings : %s=%d (env %s, mapping=%s)", setting, value, env_key, index_block.get(setting))
        index_block[setting] = value
    return out
