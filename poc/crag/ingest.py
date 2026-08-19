#!/usr/bin/env python3
"""CRAG — ingestion scriptée des pages HTML dans RAGFlow.

Reprenable : les fichiers déjà présents dans le dataset (même nom) sont
sautés, on peut relancer après une coupure (token expiré, réseau…).
Uploads séquentiels (1 POST par fichier — pattern CIA-10), parse déclenché
par lots, attente de fin optionnelle.

Deux modes de destination :
  --dataset-id <kb_id>       tout dans un dataset existant (calibration) ;
  --per-domain               un dataset par domaine CRAG (créés au besoin,
                             nommés <prefix>-<domaine>). Répartit les inserts
                             Infinity sur 5 tables au lieu d'une — indispensable
                             au run complet : une seule table sature la
                             compaction sous 12 threads d'insert (2026-08-19).

Usage :
  python ingest.py --questions <out>/questions.tsv --html-dir <out>/html \
      --ragflow-url https://... --ragflow-key <token> \
      (--dataset-id <kb_id> | --per-domain [--dataset-prefix crag-v5]) \
      [--limit N] [--domain finance] [--no-wait]
"""

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

PARSE_BATCH = 50
POLL_INTERVAL_S = 10
DATASET_CONFIG = {
    "chunk_method": "naive",
    "parser_config": {"chunk_token_num": 512, "delimiter": "\n",
                      "html4excel": False, "layout_recognize": "DeepDOC"},
}


def rf_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def find_or_create_dataset(base, key, name) -> str:
    r = requests.get(f"{base}/api/v1/datasets",
                     headers=rf_headers(key),
                     params={"name": name, "page_size": 10}, timeout=30)
    r.raise_for_status()
    for d in r.json().get("data") or []:
        if d["name"] == name:
            return d["id"]
    r = requests.post(f"{base}/api/v1/datasets",
                      headers={**rf_headers(key), "Content-Type": "application/json"},
                      json={"name": name, **DATASET_CONFIG}, timeout=30)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (0, None):
        raise RuntimeError(f"create dataset {name}: {body}")
    print(f"dataset créé : {name} ({body['data']['id']})")
    return body["data"]["id"]


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
    ap.add_argument("--dataset-id", default="", help="dataset unique existant")
    ap.add_argument("--per-domain", action="store_true",
                    help="un dataset par domaine (<prefix>-<domaine>)")
    ap.add_argument("--dataset-prefix", default="crag-v5")
    ap.add_argument("--limit", type=int, default=0, help="max questions (0 = toutes)")
    ap.add_argument("--domain", default="", help="filtrer un domaine")
    ap.add_argument("--no-wait", action="store_true", help="ne pas attendre la fin du parsing")
    args = ap.parse_args()
    if bool(args.dataset_id) == args.per_domain:
        ap.error("exactement un de --dataset-id / --per-domain")

    base = args.ragflow_url.rstrip("/")

    # fichiers voulus, groupés par domaine, dans l'ordre des questions
    wanted_by_domain = defaultdict(list)
    n_q = 0
    with open(args.questions, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if args.domain and row["domain"] != args.domain:
                continue
            if args.limit and n_q >= args.limit:
                break
            wanted_by_domain[row["domain"]].extend(json.loads(row["page_files"]))
            n_q += 1

    # domaine → dataset_id
    if args.dataset_id:
        ds_of = {dom: args.dataset_id for dom in wanted_by_domain}
    else:
        ds_of = {dom: find_or_create_dataset(base, args.ragflow_key,
                                             f"{args.dataset_prefix}-{dom}")
                 for dom in sorted(wanted_by_domain)}

    datasets = sorted(set(ds_of.values()))
    existing = {ds: list_documents(base, args.ragflow_key, ds) for ds in datasets}

    todo = []  # (name, dataset_id)
    for dom, names in wanted_by_domain.items():
        ds = ds_of[dom]
        todo.extend((n, ds) for n in names if n not in existing[ds])
    n_wanted = sum(len(v) for v in wanted_by_domain.values())
    print(f"{n_q} questions → {n_wanted} pages sur {len(datasets)} dataset(s) ; "
          f"{n_wanted - len(todo)} déjà présentes, {len(todo)} à uploader")

    new_ids = defaultdict(list)
    failures = []
    t0 = time.time()
    for i, (name, ds) in enumerate(todo, 1):
        try:
            new_ids[ds].append(upload(base, args.ragflow_key, ds, args.html_dir / name))
        except Exception as e:
            failures.append((name, str(e)))
            print(f"  ! {name}: {e}", file=sys.stderr)
        if i % 50 == 0 or i == len(todo):
            rate = i / max(time.time() - t0, 1e-9)
            eta_min = (len(todo) - i) / max(rate, 1e-9) / 60
            print(f"  upload {i}/{len(todo)} ({rate:.1f}/s, ETA {eta_min:.0f} min)", flush=True)
        # Parse par lots pendant l'upload : le cluster travaille en parallèle.
        for ds_id, ids in list(new_ids.items()):
            if len(ids) >= PARSE_BATCH:
                trigger_parse(base, args.ragflow_key, ds_id, ids)
                new_ids[ds_id] = []
    for ds_id, ids in new_ids.items():
        if ids:
            trigger_parse(base, args.ragflow_key, ds_id, ids)

    # docs de sessions précédentes jamais parsés
    for ds in datasets:
        wanted_names = {n for dom, names in wanted_by_domain.items()
                        if ds_of[dom] == ds for n in names}
        unparsed = [v["id"] for n, v in existing[ds].items()
                    if n in wanted_names and v["run"] in ("UNSTART", "0")]
        if unparsed:
            print(f"{len(unparsed)} docs jamais parsés dans {ds} → re-trigger")
            for i in range(0, len(unparsed), PARSE_BATCH):
                trigger_parse(base, args.ragflow_key, ds, unparsed[i:i + PARSE_BATCH])

    if failures:
        print(f"\n{len(failures)} uploads en échec — relancer le script pour reprendre.")

    if args.no_wait:
        return 0

    all_wanted = {ds: {n for dom, names in wanted_by_domain.items()
                       if ds_of[dom] == ds for n in names} for ds in datasets}
    while True:
        time.sleep(POLL_INTERVAL_S)
        done = fail = total = 0
        counts = defaultdict(int)
        mine_all = {}
        for ds in datasets:
            docs = list_documents(base, args.ragflow_key, ds)
            mine = {n: v for n, v in docs.items() if n in all_wanted[ds]}
            mine_all.update(mine)
            for v in mine.values():
                counts[v["run"]] += 1
            done += sum(v["run"] == "DONE" for v in mine.values())
            fail += sum(v["run"] == "FAIL" for v in mine.values())
            total += len(mine)
        print(f"  parsing: {done}/{total} DONE, {fail} FAIL, états={dict(counts)}", flush=True)
        if done + fail >= total:
            break

    if fail:
        failed_names = [n for n, v in mine_all.items() if v["run"] == "FAIL"][:20]
        print(f"\nDocs en échec ({fail}) — premiers : {failed_names}")
    print("Ingestion terminée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
