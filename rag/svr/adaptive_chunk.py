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
    """
    enabled = os.environ.get("ADAPTIVE_CHUNK_SIZE", "1") not in ("0", "false", "False", "")
    max_chunks = int(os.environ.get("MAX_CHUNKS_PER_DOC", "4096"))
    hard_cap = int(os.environ.get("ADAPTIVE_CHUNK_TOKEN_MAX", "2048"))
    return enabled, max_chunks, hard_cap


def effective_chunk_token_num(configured: int, first_pass_chunks: int, embd_max_tokens: int, max_chunks: int = 4096, hard_cap: int = 2048) -> tuple[int, str | None]:
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

    reason = f"document volumineux : {first_pass_chunks} chunks à {configured} tokens → taille portée à {new}"
    return new, reason
