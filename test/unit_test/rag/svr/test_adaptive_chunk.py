from rag.svr.adaptive_chunk import (
    adaptive_settings,
    effective_chunk_token_num,
    resolve_adaptive_settings,
)


def test_no_adaptation_under_threshold():
    num, reason = effective_chunk_token_num(512, 1000, 8192)
    assert (num, reason) == (512, None)


def test_quality_first_defaults_spare_client_specs():
    # Révision 2026-08-14 : défaut max_chunks=16384 — une grosse spec client
    # (10 000 chunks à 512 tokens ≈ 20 Mo de texte) garde sa granularité.
    num, reason = effective_chunk_token_num(512, 10000, 8192)
    assert (num, reason) == (512, None)


def test_scales_up_to_fit_max_chunks():
    # 512 tokens × 20000 chunks avec max_chunks=4096 → il faut 2500 tokens,
    # borné par hard_cap explicite 2048 (test de la math, indépendant des défauts)
    num, reason = effective_chunk_token_num(512, 20000, 8192, max_chunks=4096, hard_cap=2048)
    assert num == 2048
    assert reason and "2048" in reason


def test_default_hard_cap_is_1024():
    # Révision 2026-08-14 : plafond qualité 1024 par défaut — même quand le
    # besoin calculé est plus haut (needed=2500 ici), on n'agrandit pas au-delà.
    num, reason = effective_chunk_token_num(512, 20000 * 4, 8192, max_chunks=16384)
    assert num == 1024
    assert reason and "1024" in reason


def test_capped_by_embedding_model_bge512():
    # bge-large 512 tokens → cap = 460 : on ne dépasse JAMAIS ce que le modèle encode
    num, reason = effective_chunk_token_num(128, 80000, 512, max_chunks=4096)
    assert num == 460
    assert reason


def test_bge_m3_room():
    # bge-m3 8192 → cap embedding 7372 ; besoin 1250 avec max_chunks=4096 et
    # hard_cap relevé → non borné par le modèle
    num, reason = effective_chunk_token_num(512, 10000, 8192, max_chunks=4096, hard_cap=2048)
    assert num == 1250
    assert reason


def test_configured_already_above_cap_no_regression():
    # configuré 600 sur bge-512 (cap 460) : on ne RÉDUIT pas (comportement historique
    # conservé), pas d'adaptation à la hausse possible → (600, None)
    num, reason = effective_chunk_token_num(600, 80000, 512)
    assert (num, reason) == (600, None)


def test_invalid_inputs_passthrough():
    assert effective_chunk_token_num(0, 99999, 8192) == (0, None)
    assert effective_chunk_token_num(512, 999999, 0)[1] is None


def test_reason_recommends_parent_child():
    _, reason = effective_chunk_token_num(512, 200000, 8192)
    assert reason and "parent-child" in reason


def test_resolve_settings_env_defaults(monkeypatch):
    monkeypatch.delenv("ADAPTIVE_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("MAX_CHUNKS_PER_DOC", raising=False)
    monkeypatch.delenv("ADAPTIVE_CHUNK_TOKEN_MAX", raising=False)
    assert adaptive_settings() == (True, 16384, 1024)
    # parser_config absent/None → env pur
    assert resolve_adaptive_settings(None) == (True, 16384, 1024)
    assert resolve_adaptive_settings({}) == (True, 16384, 1024)


def test_resolve_settings_kb_overrides(monkeypatch):
    monkeypatch.delenv("ADAPTIVE_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("MAX_CHUNKS_PER_DOC", raising=False)
    monkeypatch.delenv("ADAPTIVE_CHUNK_TOKEN_MAX", raising=False)
    cfg = {"adaptive_enabled": False, "adaptive_max_chunks": 4096, "adaptive_token_max": "2048"}
    assert resolve_adaptive_settings(cfg) == (False, 4096, 2048)


def test_resolve_settings_invalid_overrides_fall_back(monkeypatch):
    monkeypatch.delenv("ADAPTIVE_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("MAX_CHUNKS_PER_DOC", raising=False)
    monkeypatch.delenv("ADAPTIVE_CHUNK_TOKEN_MAX", raising=False)
    cfg = {"adaptive_enabled": "yes", "adaptive_max_chunks": "garbage", "adaptive_token_max": -5}
    # "yes" n'est pas un bool → ignoré ; "garbage" non castable → ignoré ; -5 <= 0 → ignoré
    assert resolve_adaptive_settings(cfg) == (True, 16384, 1024)
