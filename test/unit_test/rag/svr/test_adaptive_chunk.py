from rag.svr.adaptive_chunk import effective_chunk_token_num


def test_no_adaptation_under_threshold():
    num, reason = effective_chunk_token_num(512, 1000, 8192)
    assert (num, reason) == (512, None)


def test_scales_up_to_fit_max_chunks():
    # 512 tokens × 20000 chunks → il faut ~2442 tokens pour retomber à 4096 chunks,
    # borné par hard_cap 2048
    num, reason = effective_chunk_token_num(512, 20000, 8192)
    assert num == 2048
    assert reason and "2048" in reason


def test_capped_by_embedding_model_bge512():
    # bge-large 512 tokens → cap = 460 : on ne dépasse JAMAIS ce que le modèle encode
    num, reason = effective_chunk_token_num(128, 20000, 512)
    assert num == 460
    assert reason


def test_bge_m3_room():
    # bge-m3 8192 → cap 7372 ; besoin ~1250 → non borné
    num, reason = effective_chunk_token_num(512, 10000, 8192)
    assert num == 1250
    assert reason


def test_configured_already_above_cap_no_regression():
    # configuré 600 sur bge-512 (cap 460) : on ne RÉDUIT pas (comportement historique
    # conservé), pas d'adaptation à la hausse possible → (600, None)
    num, reason = effective_chunk_token_num(600, 20000, 512)
    assert (num, reason) == (600, None)


def test_invalid_inputs_passthrough():
    assert effective_chunk_token_num(0, 99999, 8192) == (0, None)
    assert effective_chunk_token_num(512, 99999, 0)[1] is None
