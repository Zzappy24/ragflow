#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# =============================================================================
# CUSTOM B2B SaaS — variante "paper_fast" du parser paper.
#
# Identique à `rag/app/paper.py` sauf que la classe `Pdf` override
# `_evaluate_table_orientation` pour retourner directement 0° au lieu de
# faire 4 OCR (une par angle) et choisir la meilleure orientation.
#
# Sur des docs occidentaux non-tournés (99%+ des cas Cyllene), 0° gagne
# toujours (voir le code upstream deepdoc/parser/pdf_parser.py:397-399 qui
# admet lui-même que non-0° n'est adopté que si le score dépasse 0° de 0.2).
# Gain observé: TSR passe de ~80s à ~30s sur un doc 11 pages avec ~3 tables.
#
# Trade-off: les tables dans des documents scannés retournés seront mal
# parsées (texte des cellules illisible). Pour ces docs, utiliser le mode
# "paper" standard.
#
# Register dans task_executor.py:
#   FACTORY["paper_fast"] = paper_fast
#
# Le mode "paper" standard reste inchangé et disponible en parallèle.
# =============================================================================
import copy
import logging
import re

from deepdoc.parser.figure_parser import vision_figure_parser_pdf_wrapper
from common.constants import ParserType, MAXIMUM_PAGE_NUMBER
from rag.nlp import rag_tokenizer, tokenize, tokenize_table, add_positions, bullets_category, title_frequency, \
    tokenize_chunks, attach_media_context
from rag.app import paper as _paper
from rag.app.naive import by_plaintext, PARSERS
from common.parser_config_utils import normalize_layout_recognizer


class Pdf(_paper.Pdf):
    """Extension du parser paper avec skip de l'auto-rotate tables.

    Override `_evaluate_table_orientation` pour retourner 0° immédiatement
    sans faire les 4 OCR de rotation. Voir le module header pour la
    justification et le trade-off.
    """

    def _evaluate_table_orientation(self, table_img, sample_ratio=0.3):
        return 0, table_img, {0: {"avg_confidence": 1.0, "total_regions": 0, "combined_score": 1.0}}


# =============================================================================
# `chunk()` — copie de rag/app/paper.py::chunk avec 2 modifications:
#   1. `pdf_parser = Pdf()` utilise notre Pdf (subclasse) au lieu de _paper.Pdf
#   2. `pdf_cls=Pdf` idem pour la branche mineru
#
# Si paper.chunk() est modifié upstream, ce fichier est à re-sync. La logique
# est stable (~1-2 changements par an chez upstream), risque faible.
# Grep 'CUSTOM B2B SaaS — variante "paper_fast"' pour retrouver.
# =============================================================================
def chunk(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER,
          lang="Chinese", callback=None, **kwargs):
    """
        Only pdf is supported.
        The abstract of the paper will be sliced as an entire chunk, and will not be sliced partly.
    """
    parser_config = kwargs.get(
        "parser_config", {
            "chunk_token_num": 512, "delimiter": "\n!?。；！？", "layout_recognize": "DeepDOC"})
    if re.search(r"\.pdf$", filename, re.IGNORECASE):
        layout_recognizer, parser_model_name = normalize_layout_recognizer(
            parser_config.get("layout_recognize", "DeepDOC")
        )

        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        name = layout_recognizer.strip().lower()
        pdf_parser = PARSERS.get(name, by_plaintext)
        callback(0.1, "Start to parse.")

        if name == "deepdoc":
            pdf_parser = Pdf()  # CUSTOM: subclasse avec skip rotation
            paper = pdf_parser(filename if not binary else binary,
                               from_page=from_page, to_page=to_page, callback=callback)
            sections = paper.get("sections", [])
        else:
            kwargs.pop("parse_method", None)
            kwargs.pop("mineru_llm_name", None)
            sections, tables, pdf_parser = pdf_parser(
                filename=filename,
                binary=binary,
                from_page=from_page,
                to_page=to_page,
                lang=lang,
                callback=callback,
                pdf_cls=Pdf,  # CUSTOM: subclasse avec skip rotation
                layout_recognizer=layout_recognizer,
                mineru_llm_name=parser_model_name,
                parse_method="paper",
                **kwargs
            )

            paper = {
                "title": filename,
                "authors": " ",
                "abstract": "",
                "sections": sections,
                "tables": tables
            }

        tbls = paper["tables"]
        tbls = vision_figure_parser_pdf_wrapper(
            tbls=tbls,
            sections=sections,
            callback=callback,
            **kwargs,
        )
        paper["tables"] = tbls
    else:
        raise NotImplementedError("file type not supported yet(pdf supported)")

    doc = {"docnm_kwd": filename, "authors_tks": rag_tokenizer.tokenize(paper["authors"]),
           "title_tks": rag_tokenizer.tokenize(paper["title"] if paper["title"] else filename)}
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    doc["authors_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["authors_tks"])
    eng = lang.lower() == "english"
    logging.debug("It's English.....{}".format(eng))

    res = tokenize_table(paper["tables"], doc, eng)

    if paper["abstract"]:
        d = copy.deepcopy(doc)
        txt = pdf_parser.remove_tag(paper["abstract"])
        d["important_kwd"] = ["abstract", "总结", "概括", "summary", "summarize"]
        d["important_tks"] = " ".join(d["important_kwd"])
        d["image"], poss = pdf_parser.crop(
            paper["abstract"], need_position=True)
        add_positions(d, poss)
        tokenize(d, txt, eng)
        res.append(d)

    sorted_sections = paper["sections"]
    bull = bullets_category([txt for txt, _ in sorted_sections])
    most_level, levels = title_frequency(bull, sorted_sections)
    assert len(sorted_sections) == len(levels)
    sec_ids = []
    sid = 0
    for i, lvl in enumerate(levels):
        if lvl <= most_level and i > 0 and lvl != levels[i - 1]:
            sid += 1
        sec_ids.append(sid)
        logging.debug("{} {} {} {}".format(lvl, sorted_sections[i][0], most_level, sid))

    chunks = []
    last_sid = -2
    for (txt, _), sec_id in zip(sorted_sections, sec_ids):
        if sec_id == last_sid:
            if chunks:
                chunks[-1] += "\n" + txt
                continue
        chunks.append(txt)
        last_sid = sec_id
    res.extend(tokenize_chunks(chunks, doc, eng, pdf_parser))
    table_ctx = max(0, int(parser_config.get("table_context_size", 0) or 0))
    image_ctx = max(0, int(parser_config.get("image_context_size", 0) or 0))
    if table_ctx or image_ctx:
        attach_media_context(res, table_ctx, image_ctx)

    return res
