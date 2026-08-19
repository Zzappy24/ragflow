#!/usr/bin/env python3
"""CRAG Mode A — driver « protocole officiel » sur un dataset RAGFlow.

Reproduit le baseline officiel (models/rag_llama_baseline.py du repo
facebookresearch/CRAG) avec RAGFlow comme moteur de retrieval :

  1. retrieval RAGFlow (/api/v1/retrieval) **scopé aux 5 pages de la
     question** via `document_ids` (protocole : chaque question ne voit que
     ses propres search_results — pas le corpus des autres questions) ;
  2. génération avec le prompt système officiel verbatim, incluant le
     `query_time` de la question ;
  3. réponse tronquée à ~75 tokens (approximation par mots — l'officiel
     utilise le tokenizer Llama2 ; écart documenté dans le rapport).

Usage :
  python drive.py --questions poc/crag/out/questions.tsv \
      --ragflow-url https://<ragflow> --ragflow-key <api-key> \
      --dataset-id <kb_id> \
      --llm-url https://litellm.../v1 --llm-key sk-... --llm-model <model> \
      --out poc/crag/out/predictions.jsonl [--limit N] [--unscoped]

`--unscoped` désactive le filtre document_ids (mesure « corpus entier »,
non comparable au leaderboard — utile pour quantifier l'écart de scoping).
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

# Prompt système du baseline officiel (rag_llama_baseline.py, verbatim).
SYSTEM_PROMPT = (
    "You are provided with a question and various references. Your task is to "
    "answer the question succinctly, using the fewest words possible. If the "
    "references do not contain the necessary information to answer the "
    "question, respond with 'I don't know'. There is no need to explain the "
    "reasoning behind your answers."
)
MAX_CONTEXT_REFERENCES_LENGTH = 4000  # même cap que l'officiel
MAX_ANSWER_WORDS = 75  # approx. des 75 tokens Llama2 de l'officiel


def rf_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def list_documents(base: str, key: str, dataset_id: str) -> dict:
    """name → document_id pour tout le dataset."""
    name_to_id = {}
    page = 1
    while True:
        r = requests.get(
            f"{base}/api/v1/datasets/{dataset_id}/documents",
            headers=rf_headers(key),
            params={"page": page, "page_size": 100},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()["data"]
        docs = data.get("docs", [])
        for d in docs:
            name_to_id[d["name"]] = d["id"]
        if len(docs) < 100:
            return name_to_id
        page += 1


def retrieve(base: str, key: str, dataset_id: str, question: str,
             doc_ids: list, page_size: int, similarity_threshold: float) -> list:
    payload = {
        "question": question,
        "dataset_ids": [dataset_id],
        "page_size": page_size,
        "similarity_threshold": similarity_threshold,
    }
    if doc_ids:
        payload["document_ids"] = doc_ids
    r = requests.post(f"{base}/api/v1/retrieval", headers=rf_headers(key),
                      json=payload, timeout=60)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (0, None):
        raise RuntimeError(f"retrieval error: {body}")
    chunks = body["data"]["chunks"]
    return [c.get("content") or c.get("content_with_weight") or "" for c in chunks]


def generate(llm_url: str, llm_key: str, llm_model: str,
             query: str, query_time: str, references: list) -> str:
    refs = ""
    if references:
        refs = "# References \n" + "".join(f"- {s.strip()}\n" for s in references)
    refs = refs[:MAX_CONTEXT_REFERENCES_LENGTH]
    user_message = (
        f"{refs}\n------\n\n"
        "Using only the references listed above, answer the following question: \n"
        f"Current Time: {query_time}\n"
        f"Question: {query}\n"
    )
    r = requests.post(
        f"{llm_url}/chat/completions",
        headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
        json={
            "model": llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
        },
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"] or ""
    # <think>…</think> éventuel des modèles reasoning : on ne garde que la réponse.
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    words = text.strip().split()
    return " ".join(words[:MAX_ANSWER_WORDS])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--ragflow-url", required=True)
    ap.add_argument("--ragflow-key", required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--llm-url", required=True)
    ap.add_argument("--llm-key", required=True)
    ap.add_argument("--llm-model", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--unscoped", action="store_true",
                    help="pas de filtre document_ids (mesure corpus entier)")
    ap.add_argument("--page-size", type=int, default=10, help="chunks retournés")
    ap.add_argument("--similarity-threshold", type=float, default=0.1)
    args = ap.parse_args()

    base = args.ragflow_url.rstrip("/")
    llm_url = args.llm_url.rstrip("/")

    name_to_id = list_documents(base, args.ragflow_key, args.dataset_id)
    print(f"{len(name_to_id)} documents dans le dataset {args.dataset_id}")

    n = n_missing_docs = 0
    with open(args.questions, encoding="utf-8") as f, open(args.out, "w", encoding="utf-8") as out:
        for row in csv.DictReader(f, delimiter="\t"):
            if args.limit and n >= args.limit:
                break
            page_files = json.loads(row["page_files"])
            doc_ids = [name_to_id[p] for p in page_files if p in name_to_id]
            if len(doc_ids) < len(page_files):
                n_missing_docs += 1
                print(f"  ! {row['interaction_id']}: {len(page_files) - len(doc_ids)} "
                      f"page(s) absente(s) du dataset", file=sys.stderr)

            t0 = time.time()
            refs = retrieve(base, args.ragflow_key, args.dataset_id, row["query"],
                            [] if args.unscoped else doc_ids,
                            args.page_size, args.similarity_threshold)
            prediction = generate(llm_url, args.llm_key, args.llm_model,
                                  row["query"], row["query_time"], refs)
            out.write(json.dumps({
                "interaction_id": row["interaction_id"],
                "domain": row["domain"],
                "question_type": row["question_type"],
                "query": row["query"],
                "query_time": row["query_time"],
                "answer": row["answer"],
                "alt_ans": json.loads(row["alt_ans"] or "[]"),
                "prediction": prediction,
                "n_refs": len(refs),
                "scoped": not args.unscoped,
                "elapsed_s": round(time.time() - t0, 2),
            }, ensure_ascii=False) + "\n")
            out.flush()
            n += 1
            print(f"[{n}] {row['query'][:70]}… → {prediction[:80]}")

    print(f"\n{n} prédictions écrites dans {args.out}"
          + (f" ({n_missing_docs} questions avec pages manquantes)" if n_missing_docs else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
