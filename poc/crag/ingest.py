#!/usr/bin/env python3
"""CRAG — ingestion scriptée des pages HTML dans un dataset RAGFlow.

Reprenable : les fichiers déjà présents dans le dataset (même nom) sont
sautés, on peut relancer après une coupure (token expiré, réseau…).
Uploads séquentiels (1 POST par fichier — pattern CIA-10), parse déclenché
par lots, attente de fin optionnelle.

Usage :
  python ingest.py --questions <out_dir>/questions.tsv --html-dir <out_dir>/html \
      --ragflow-url https://... --ragflow-key <token> --dataset-id <kb_id> \
      [--limit N] [--domain finance] [--no-wait]
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

PARSE_BATCH = 50
POLL_INTERVAL_S = 10


def rf_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def list_documents(base, key, dataset_id) -> dict:
    """name → {id, run} pour tout le dataset."""
    out = {}
    page = 1
    while True:
        r = requests.get(f"{base}/api/v1/datasets/{dataset_id}/documents",
                         headers=rf_headers(key),
                         params={"page": page, "page_size": 100}, timeout=60)
        r.raise_for_status()
        docs = r.json()["data"].get("docs", [])
        for d in docs:
            out[d["name"]] = {"id": d["id"], "run": d.get("run", "")}
        if len(docs) < 100:
            return out
        page += 1


def upload(base, key, dataset_id, path: Path) -> str:
    with open(path, "rb") as f:
        r = requests.post(f"{base}/api/v1/datasets/{dataset_id}/documents",
                          headers=rf_headers(key),
                          files={"file": (path.name, f, "text/html")}, timeout=120)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (0, None):
        raise RuntimeError(f"upload {path.name}: {body}")
    return body["data"][0]["id"]


def trigger_parse(base, key, dataset_id, doc_ids: list):
    r = requests.post(f"{base}/api/v1/datasets/{dataset_id}/chunks",
                      headers={**rf_headers(key), "Content-Type": "application/json"},
                      json={"document_ids": doc_ids}, timeout=60)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (0, None):
        raise RuntimeError(f"parse trigger: {body}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--html-dir", type=Path, required=True)
    ap.add_argument("--ragflow-url", required=True)
    ap.add_argument("--ragflow-key", required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--limit", type=int, default=0, help="max questions (0 = toutes)")
    ap.add_argument("--domain", default="", help="filtrer un domaine")
    ap.add_argument("--no-wait", action="store_true", help="ne pas attendre la fin du parsing")
    args = ap.parse_args()

    base = args.ragflow_url.rstrip("/")

    wanted = []  # noms de fichiers à ingérer, dans l'ordre des questions
    n_q = 0
    with open(args.questions, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if args.domain and row["domain"] != args.domain:
                continue
            if args.limit and n_q >= args.limit:
                break
            wanted.extend(json.loads(row["page_files"]))
            n_q += 1

    existing = list_documents(base, args.ragflow_key, args.dataset_id)
    todo = [n for n in wanted if n not in existing]
    print(f"{n_q} questions → {len(wanted)} pages ; {len(existing)} déjà dans le "
          f"dataset, {len(todo)} à uploader")

    new_ids, failures = [], []
    t0 = time.time()
    for i, name in enumerate(todo, 1):
        try:
            new_ids.append(upload(base, args.ragflow_key, args.dataset_id, args.html_dir / name))
        except Exception as e:
            failures.append((name, str(e)))
            print(f"  ! {name}: {e}", file=sys.stderr)
        if i % 50 == 0 or i == len(todo):
            rate = i / max(time.time() - t0, 1e-9)
            eta_min = (len(todo) - i) / max(rate, 1e-9) / 60
            print(f"  upload {i}/{len(todo)} ({rate:.1f}/s, ETA {eta_min:.0f} min)")
        # Déclenche le parse par lots pendant l'upload : le cluster parse en
        # parallèle au lieu d'attendre la fin du transfert.
        if len(new_ids) >= PARSE_BATCH:
            trigger_parse(base, args.ragflow_key, args.dataset_id, new_ids)
            new_ids = []
    if new_ids:
        trigger_parse(base, args.ragflow_key, args.dataset_id, new_ids)

    # Les docs déjà uploadés mais jamais parsés (run UNSTART) d'une session
    # précédente sont relancés aussi.
    unparsed = [v["id"] for n, v in existing.items()
                if n in set(wanted) and v["run"] in ("UNSTART", "0")]
    if unparsed:
        print(f"{len(unparsed)} docs d'une session précédente jamais parsés → re-trigger")
        for i in range(0, len(unparsed), PARSE_BATCH):
            trigger_parse(base, args.ragflow_key, args.dataset_id, unparsed[i:i + PARSE_BATCH])

    if failures:
        print(f"\n{len(failures)} uploads en échec — relancer le script pour reprendre.")

    if args.no_wait:
        return 0

    wanted_set = set(wanted)
    while True:
        time.sleep(POLL_INTERVAL_S)
        docs = list_documents(base, args.ragflow_key, args.dataset_id)
        mine = {n: v for n, v in docs.items() if n in wanted_set}
        counts = {}
        for v in mine.values():
            counts[v["run"]] = counts.get(v["run"], 0) + 1
        done = counts.get("DONE", 0)
        fail = counts.get("FAIL", 0)
        print(f"  parsing: {done}/{len(mine)} DONE, {fail} FAIL, états={counts}")
        if done + fail >= len(mine):
            break

    if fail:
        failed_names = [n for n, v in mine.items() if v["run"] == "FAIL"][:20]
        print(f"\nDocs en échec ({fail}) — premiers : {failed_names}")
    print("Ingestion terminée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
