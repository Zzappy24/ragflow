#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

# CUSTOM B2B SaaS — adaptive chunking (CIA-10). Pure helper, no RAGFlow
# dependency, consumed by rag.svr.task_executor.build_chunks for the
# two-pass adaptive re-chunk of oversized documents.

"""Adaptive chunk-size helper for the two-pass document chunking (CIA-10).

Kept dependency-free so it can be unit-tested without importing the task
executor (which pulls in the full RAGFlow runtime).
"""

import math
import os


def adaptive_settings() -> tuple[bool, int, int]:
    """Read the three env knobs governing adaptive chunking.

    Returns (enabled, max_chunks_per_doc, hard_cap_tokens).

    Défauts qualité-d'abord (révision 2026-08-14) : 16384 chunks avant
    déclenchement (≈ >130 Mo de texte pur à 512 tokens/chunk — une spec client
    Word/PDF dense garde sa granularité intégrale) et 1024 tokens de plafond
    (zone de bonne qualité d'embedding bge-m3 ; à 2048 la dilution sémantique
    coûte à chaque requête alors que le volume ne coûte qu'à l'ingestion).
    """
    enabled = os.environ.get("ADAPTIVE_CHUNK_SIZE", "1") not in ("0", "false", "False", "")
    max_chunks = int(os.environ.get("MAX_CHUNKS_PER_DOC", "16384"))
    hard_cap = int(os.environ.get("ADAPTIVE_CHUNK_TOKEN_MAX", "1024"))
    return enabled, max_chunks, hard_cap


def resolve_adaptive_settings(parser_config: dict | None) -> tuple[bool, int, int]:
    """Env knobs overridden by per-KB keys from the dataset parser_config.

    Reconnaît dans parser_config (posés par l'admin/API sur la KB) :
      - adaptive_enabled: bool
      - adaptive_max_chunks: int > 0
      - adaptive_token_max: int > 0
    Toute valeur absente ou invalide retombe sur l'env. Politique par
    workspace/KB : une KB qualité-critique désactive ou relève son plafond,
    une KB d'ingestion de masse serre.
    """
    enabled, max_chunks, hard_cap = adaptive_settings()
    if not isinstance(parser_config, dict):
        return enabled, max_chunks, hard_cap

    override_enabled = parser_config.get("adaptive_enabled")
    if isinstance(override_enabled, bool):
        enabled = override_enabled

    for key, current in (("adaptive_max_chunks", max_chunks), ("adaptive_token_max", hard_cap)):
        raw = parser_config.get(key)
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            if key == "adaptive_max_chunks":
                max_chunks = value
            else:
                hard_cap = value
    return enabled, max_chunks, hard_cap


def effective_chunk_token_num(configured: int, first_pass_chunks: int, embd_max_tokens: int, max_chunks: int = 16384, hard_cap: int = 1024) -> tuple[int, str | None]:
    """Retourne (chunk_token_num_effectif, raison|None).

    raison None => pas d'adaptation (first_pass_chunks <= max_chunks, ou configured
    invalide <= 0, ou embd_max_tokens <= 0).
    Sinon : new = ceil(configured * first_pass_chunks / max_chunks), borné par
    min(hard_cap, int(embd_max_tokens * 0.9)) et > configured sinon None.
    raison = message français lisible pour la progression (« document volumineux :
    N chunks à X tokens → taille portée à Y »), utilisé tel quel par build_chunks.
    """
    if configured <= 0 or embd_max_tokens <= 0:
        return configured, None
    if first_pass_chunks <= max_chunks:
        return configured, None

    needed = math.ceil(configured * first_pass_chunks / max_chunks)
    cap = min(hard_cap, int(embd_max_tokens * 0.9))
    new = min(needed, cap)

    if new <= configured:
        return configured, None

    reason = (
        f"document volumineux : {first_pass_chunks} chunks à {configured} tokens → taille portée à {new}. "
        "Pour une meilleure précision de recherche sur ce type de document, envisagez le mode "
        "parent-child de la base de connaissances ou le découpage du fichier source."
    )
    return new, reason
