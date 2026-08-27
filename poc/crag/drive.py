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
from concurrent.futures import ThreadPoolExecutor
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

# Prompt « souple » : encourage à répondre dès qu'une info raisonnablement
# pertinente est présente (même partielle ou implicite), tout en gardant le
# garde-fou anti-hallucination (ne rien inventer hors des références). Vise à
# convertir des « I don't know » en réponses correctes sans faire exploser
# l'hallucination. NON comparable au leaderboard (prompt ≠ officiel).
SOFT_SYSTEM_PROMPT = (
    "You are provided with a question and various references. Answer the "
    "question succinctly, using the fewest words possible. Use the references "
    "as your source: if they contain the answer — even partially, indirectly, "
    "or requiring simple inference — give your best answer. Only respond with "
    "'I don't know' when the references genuinely offer nothing relevant. Never "
    "invent facts that are not supported by the references. No explanations."
)
MAX_CONTEXT_REFERENCES_LENGTH = 4000  # même cap que l'officiel
MAX_ANSWER_WORDS = 75  # approx. des 75 tokens Llama2 de l'officiel


def rf_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def find_dataset(base: str, key: str, name: str) -> str:
    r = requests.get(f"{base}/api/v1/datasets", headers=rf_headers(key),
                     params={"name": name, "page_size": 10}, timeout=30)
    r.raise_for_status()
    for d in r.json().get("data") or []:
        if d["name"] == name:
            return d["id"]
    raise RuntimeError(f"dataset introuvable: {name}")


def list_documents(base: str, key: str, dataset_id: str, page_size: int = 30) -> dict:
    """name → document_id pour tout le dataset.

    Le listing réconcilie le nombre de chunks contre le moteur par doc et
    peut avoir des hangs intermittents sur gros dataset — petites pages +
    retry par page, tolérant (une page qui échoue N fois est sautée avec un
    warning plutôt que de faire échouer tout le run).
    """
    name_to_id = {}
    total = None
    page = 1
    while True:
        data = None
        for attempt in range(4):
            try:
                r = requests.get(
                    f"{base}/api/v1/datasets/{dataset_id}/documents",
                    headers=rf_headers(key),
                    params={"page": page, "page_size": page_size},
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()["data"]
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  ! page {page} sautée après 4 essais: {str(e)[:60]}", file=sys.stderr)
        docs = (data or {}).get("docs", [])
        if total is None and data is not None:
            total = data.get("total", 0)
        for d in docs:
            name_to_id[d["name"]] = d["id"]
        # Fin quand on a couvert toutes les pages (basé sur total, pas sur la
        # taille d'une page — une page sautée ne doit pas tronquer la liste).
        if total is not None and page * page_size >= total:
            return name_to_id
        if total is None:  # première page injoignable → on abandonne proprement
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


def _chat_once(llm_url: str, llm_key: str, llm_model: str,
               user_message: str, max_tokens: int,
               system_prompt: str = SYSTEM_PROMPT) -> str:
    r = requests.post(
        f"{llm_url}/chat/completions",
        headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
        json={
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        },
        timeout=180,
    )
    r.raise_for_status()
    # NB : on ne lit QUE content — jamais reasoning_content, qui fuite le
    # monologue interne dans la prédiction (vu run 1 : "We need answer
    # user's question using only references…" jugé hallucination).
    text = r.json()["choices"][0]["message"].get("content") or ""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def generate(llm_url: str, llm_key: str, llm_model: str,
             query: str, query_time: str, references: list,
             max_refs_chars: int = MAX_CONTEXT_REFERENCES_LENGTH,
             system_prompt: str = SYSTEM_PROMPT) -> str:
    refs = ""
    if references:
        refs = "# References \n" + "".join(f"- {s.strip()}\n" for s in references)
    refs = refs[:max_refs_chars]
    user_message = (
        f"{refs}\n------\n\n"
        "Using only the references listed above, answer the following question: \n"
        f"Current Time: {query_time}\n"
        f"Question: {query}\n"
    )
    # Les modèles reasoning (qwen) consomment leur budget dans <think> avant
    # de répondre — un cap trop bas rend un content vide. On retente une fois
    # avec un budget doublé ; toujours vide → abstention explicite (le
    # scoring la compte miss, jamais hallucination).
    text = _chat_once(llm_url, llm_key, llm_model, user_message, 2048, system_prompt)
    if not text:
        text = _chat_once(llm_url, llm_key, llm_model, user_message, 4096, system_prompt)
    if not text:
        return "I don't know"
    return " ".join(text.split()[:MAX_ANSWER_WORDS])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--ragflow-url", required=True)
    ap.add_argument("--ragflow-key", required=True)
    ap.add_argument("--dataset-id", default="", help="dataset unique")
    ap.add_argument("--per-domain", action="store_true",
                    help="datasets <prefix>-<domaine> (cf. ingest.py --per-domain)")
    ap.add_argument("--dataset-prefix", default="crag-v5")
    ap.add_argument("--llm-url", required=True)
    ap.add_argument("--llm-key", required=True)
    ap.add_argument("--llm-model", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--unscoped", action="store_true",
                    help="pas de filtre document_ids (mesure corpus entier)")
    ap.add_argument("--page-size", type=int, default=10, help="chunks retournés")
    ap.add_argument("--similarity-threshold", type=float, default=0.1)
    ap.add_argument("--max-refs-chars", type=int, default=MAX_CONTEXT_REFERENCES_LENGTH,
                    help="cap du contexte envoyé au LLM (défaut 4000 = protocole officiel ; "
                         "augmenter = contexte moins restrictif, non comparable au leaderboard)")
    ap.add_argument("--soft-prompt", action="store_true",
                    help="prompt moins strict (répond dès qu'une info pertinente est là, "
                         "sans inventer) — vise à réduire les abstentions ; non comparable au leaderboard")
    ap.add_argument("--workers", type=int, default=4, help="questions traitées en parallèle")
    args = ap.parse_args()

    if bool(args.dataset_id) == args.per_domain:
        ap.error("exactement un de --dataset-id / --per-domain")

    base = args.ragflow_url.rstrip("/")
    llm_url = args.llm_url.rstrip("/")

    rows = []
    with open(args.questions, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if args.limit and len(rows) >= args.limit:
                break
            rows.append(row)

    # domaine → dataset, et par dataset : nom de fichier → doc_id
    if args.dataset_id:
        ds_of = {dom: args.dataset_id for dom in {r["domain"] for r in rows}}
    else:
        ds_of = {dom: find_dataset(base, args.ragflow_key, f"{args.dataset_prefix}-{dom}")
                 for dom in sorted({r["domain"] for r in rows})}
    name_to_id = {}
    for ds in set(ds_of.values()):
        name_to_id[ds] = list_documents(base, args.ragflow_key, ds)
        print(f"{len(name_to_id[ds])} documents dans le dataset {ds}")

    # En mode scopé, ne garder que les questions dont AU MOINS une page est
    # réellement dans le dataset (sinon retrieve() sans document_ids
    # chercherait tout le dataset — faux scope). Le dataset prod ne contient
    # qu'un sous-ensemble des questions du questions.tsv.
    if not args.unscoped:
        before = len(rows)
        rows = [r for r in rows
                if any(p in name_to_id[ds_of[r["domain"]]]
                       for p in json.loads(r["page_files"]))]
        print(f"scopé : {len(rows)}/{before} questions ont leurs pages en prod")

    n_missing_docs = 0

    def process(row: dict) -> dict:
        dataset_id = ds_of[row["domain"]]
        page_files = json.loads(row["page_files"])
        doc_ids = [name_to_id[dataset_id][p] for p in page_files
                   if p in name_to_id[dataset_id]]
        missing = len(page_files) - len(doc_ids)
        t0 = time.time()
        try:
            refs = retrieve(base, args.ragflow_key, dataset_id, row["query"],
                            [] if args.unscoped else doc_ids,
                            args.page_size, args.similarity_threshold)
            prediction = generate(llm_url, args.llm_key, args.llm_model,
                                  row["query"], row["query_time"], refs,
                                  args.max_refs_chars,
                                  SOFT_SYSTEM_PROMPT if args.soft_prompt else SYSTEM_PROMPT)
            error = ""
        except Exception as e:
            # Une erreur transitoire ne doit pas tuer une run de plusieurs
            # heures : abstention explicite + trace, filtrable au scoring.
            refs, prediction, error = [], "I don't know", str(e)[:200]
        return {
            "interaction_id": row["interaction_id"],
            "domain": row["domain"],
            "question_type": row["question_type"],
            "query": row["query"],
            "query_time": row["query_time"],
            "answer": row["answer"],
            "alt_ans": json.loads(row["alt_ans"] or "[]"),
            "prediction": prediction,
            "n_refs": len(refs),
            "n_missing_pages": missing,
            "scoped": not args.unscoped,
            "error": error,
            "elapsed_s": round(time.time() - t0, 2),
        }

    n = n_errors = 0
    t_start = time.time()
    with open(args.out, "w", encoding="utf-8") as out, \
         ThreadPoolExecutor(max_workers=args.workers) as ex:
        for rec in ex.map(process, rows):
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n += 1
            n_missing_docs += 1 if rec["n_missing_pages"] else 0
            n_errors += 1 if rec["error"] else 0
            if n % 10 == 0 or n == len(rows):
                rate = n / max(time.time() - t_start, 1e-9)
                eta_min = (len(rows) - n) / max(rate, 1e-9) / 60
                print(f"[{n}/{len(rows)}] {rate:.2f} q/s, ETA {eta_min:.0f} min, "
                      f"{n_errors} erreurs")

    print(f"\n{n} prédictions écrites dans {args.out}"
          + (f" ({n_missing_docs} questions avec pages manquantes)" if n_missing_docs else "")
          + (f" ({n_errors} erreurs transitoires → abstention)" if n_errors else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
