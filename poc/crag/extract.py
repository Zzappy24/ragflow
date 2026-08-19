#!/usr/bin/env python3
"""Extraction CRAG → fichiers HTML uploadables dans RAGFlow.

Suit le protocole officiel (github.com/facebookresearch/CRAG) : le corpus de
retrieval est constitué des pages HTML de `search_results` (5 par question en
Task 1/2). Le champ `answer` est le corrigé — local_evaluation.py:175 le
retire du batch AVANT toute génération (`batch.pop("answer")`). Uploader le
JSONL brut dans un dataset = indexer le corrigé (contamination).

Cet extracteur sépare physiquement les deux :
  out/html/       → les pages à uploader dans RAGFlow (JAMAIS de answer)
  out/questions.tsv → le corrigé (query + answer + alt_ans), pour le scoring
                     et les tests manuels — ne s'uploade PAS.

Usage :
  python extract.py <dataset.jsonl[.bz2]> <out_dir> [--limit N] [--domain D]
"""

import argparse
import bz2
import csv
import json
import sys
from pathlib import Path


def open_dataset(path: Path):
    if path.suffix == ".bz2":
        return bz2.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--limit", type=int, default=0, help="max questions (0 = toutes)")
    ap.add_argument("--domain", default="", help="filtrer un domaine (finance/music/movie/sports/open)")
    args = ap.parse_args()

    html_dir = args.out_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    n_q = n_pages = n_empty = 0
    total_bytes = 0
    rows = []

    with open_dataset(args.dataset) as f:
        for line in f:
            if args.limit and n_q >= args.limit:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                print("ligne illisible, ignorée", file=sys.stderr)
                continue
            if args.domain and item.get("domain") != args.domain:
                continue

            qid = item["interaction_id"]
            page_files = []
            for i, sr in enumerate(item.get("search_results") or []):
                html = sr.get("page_result") or ""
                if not html.strip():
                    n_empty += 1
                    continue
                name = f"{qid}__p{i}.html"
                (html_dir / name).write_text(html, encoding="utf-8")
                page_files.append(name)
                n_pages += 1
                total_bytes += len(html.encode("utf-8", errors="ignore"))

            rows.append({
                "interaction_id": qid,
                "domain": item.get("domain", ""),
                "question_type": item.get("question_type", ""),
                "static_or_dynamic": item.get("static_or_dynamic", ""),
                "query_time": item.get("query_time", ""),
                "query": item.get("query", ""),
                "answer": item.get("answer", ""),
                "alt_ans": json.dumps(item.get("alt_ans", []), ensure_ascii=False),
                "page_files": json.dumps(page_files),
            })
            n_q += 1

    with open(args.out_dir / "questions.tsv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    print(f"{n_q} questions, {n_pages} pages HTML écrites ({total_bytes / 1e6:.1f} Mo), "
          f"{n_empty} pages vides ignorées")
    print(f"→ à uploader dans RAGFlow : {html_dir}/")
    print(f"→ corrigé (NE PAS uploader) : {args.out_dir / 'questions.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
