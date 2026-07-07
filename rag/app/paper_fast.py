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
    """Extension du parser paper avec skip complet du flow auto-rotate tables.

    Override `__call__` pour appeler `_table_transformer_job(zoomin, auto_rotate=False)`
    au lieu du défaut `auto_rotate=True`. On bypasse ainsi 3 étapes coûteuses:

      1. `_evaluate_table_orientation` — 4 OCR calls par table pour choisir l'angle
      2. `_ocr_rotated_tables` — appelée systématiquement même si angle=0, elle
         POP + re-OCR + re-INSERT les boxes de chaque table (voir pdf_parser.py:672)
         Sur des tables où upstream détectait 90°/180° à tort, ça supprimait des
         boxes originales — d'où un chunking différent entre paper et paper_fast.
      3. La duplication de state (table_rotations, rotated_table_imgs)

    Résultat: paper_fast produit exactement le même nombre de sections/chunks
    que si upstream forçait `auto_rotate=False` — comportement propre supporté
    upstream (branche `else` de pdf_parser.py:474-477).
    """

    def __call__(self, filename, binary=None, from_page=0,
                 to_page=None, zoomin=3, callback=None):
        """Copie du parent paper.Pdf.__call__ (rag/app/paper.py:36-148) avec
        UNE seule ligne différente: `_table_transformer_job(zoomin, auto_rotate=False)`.
        Resync si upstream modifie paper.Pdf.__call__."""
        import copy
        import logging
        import re
        import numpy as np
        from timeit import default_timer as timer
        from common.constants import MAXIMUM_PAGE_NUMBER

        if to_page is None:
            to_page = MAXIMUM_PAGE_NUMBER

        start = timer()
        callback(msg="OCR started")
        self.__images__(
            filename if not binary else binary,
            zoomin,
            from_page,
            to_page,
            callback
        )
        callback(msg="OCR finished ({:.2f}s)".format(timer() - start))

        start = timer()
        self._layouts_rec(zoomin)
        callback(0.63, "Layout analysis ({:.2f}s)".format(timer() - start))

        start = timer()
        # CUSTOM B2B SaaS — la SEULE ligne changée vs paper.Pdf.__call__:
        # bypass complet du flow auto_rotate (voir docstring de la classe).
        self._table_transformer_job(zoomin, auto_rotate=False)
        callback(0.68, "Table analysis ({:.2f}s)".format(timer() - start))

        start = timer()
        self._text_merge()
        tbls = self._extract_table_figure(True, zoomin, True, True)
        column_width = np.median([b["x1"] - b["x0"] for b in self.boxes])
        self._concat_downward()
        self._filter_forpages()
        callback(0.75, "Text merged ({:.2f}s)".format(timer() - start))

        # clean mess
        if column_width < self.page_images[0].size[0] / zoomin / 2:
            self.boxes = self.sort_X_by_page(self.boxes, column_width / 2)
        for b in self.boxes:
            b["text"] = re.sub(r"([\t 　]|　){2,}", " ", b["text"].strip())

        def _begin(txt):
            return re.match(
                "[0-9. 一、i]*(introduction|abstract|摘要|引言|keywords|key words|关键词|background|背景|目录|前言|contents)",
                txt.lower().strip())

        if from_page > 0:
            return {
                "title": "",
                "authors": "",
                "abstract": "",
                "sections": [(b["text"] + self._line_tag(b, zoomin), b.get("layoutno", "")) for b in self.boxes if
                             re.match(r"(text|title)", b.get("layoutno", "text"))],
                "tables": tbls
            }

        # get title and authors
        title = ""
        authors = []
        i = 0
        while i < min(32, len(self.boxes) - 1):
            b = self.boxes[i]
            i += 1
            if b.get("layoutno", "").find("title") >= 0:
                title = b["text"]
                if _begin(title):
                    title = ""
                    break
                for j in range(3):
                    next_idx = i + j
                    if next_idx >= len(self.boxes):
                        break
                    candidate = self.boxes[next_idx]["text"]
                    if _begin(candidate):
                        break
                    if "@" in candidate:
                        break
                    authors.append(candidate)
                break

        # get abstract
        abstr = ""
        i = 0
        while i + 1 < min(32, len(self.boxes)):
            b = self.boxes[i]
            i += 1
            txt = b["text"].lower().strip()
            if re.match("(abstract|摘要)", txt):
                if len(txt.split()) > 32 or len(txt) > 64:
                    abstr = txt + self._line_tag(b, zoomin)
                    break
                txt = self.boxes[i]["text"].lower().strip()
                if len(txt.split()) > 32 or len(txt) > 64:
                    abstr = txt + self._line_tag(self.boxes[i], zoomin)
                i += 1
                break
        if not abstr:
            i = 0

        callback(0.8, "Page {}~{}: Text merging finished".format(
            from_page, min(to_page, self.total_page)))
        for b in self.boxes:
            logging.debug("{} {}".format(b["text"], b.get("layoutno")))

        return {
            "title": title,
            "authors": " ".join(authors),
            "abstract": abstr,
            "sections": [(b["text"] + self._line_tag(b, zoomin), b.get("layoutno", "")) for b in self.boxes[i:] if
                         re.match(r"(text|title)", b.get("layoutno", "text"))],
            "tables": tbls
        }


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
