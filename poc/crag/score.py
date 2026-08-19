#!/usr/bin/env python3
"""CRAG — scoring officiel des prédictions produites par drive.py.

Reproduit local_evaluation.py du repo facebookresearch/CRAG :
  - « i don't know » dans la prédiction → miss (0) ;
  - exact match (casse ignorée) → correct (+1) ;
  - règles « invalid question » croisées comme l'officiel ;
  - sinon, LLM judge avec les INSTRUCTIONS + IN_CONTEXT_EXAMPLES officiels
    (chargés verbatim depuis un checkout du repo CRAG — non vendorés ici) ;
  - score final = (2*n_correct + n_miss)/n − 1 = accuracy − hallucination.

Écart assumé vs l'officiel : le judge est le modèle passé en --llm-model au
lieu de gpt-4-0125-preview. Utiliser le MÊME judge pour toutes les runs
que l'on compare.

Usage :
  python score.py --predictions poc/crag/out/predictions.jsonl \
      --crag-repo <path du clone facebookresearch/CRAG> \
      --llm-url https://litellm.../v1 --llm-key sk-... --llm-model <model> \
      --out poc/crag/out/report.md
"""

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


def load_official_prompts(crag_repo: Path):
    """INSTRUCTIONS + IN_CONTEXT_EXAMPLES verbatim depuis prompts/templates.py."""
    path = crag_repo / "prompts" / "templates.py"
    spec = importlib.util.spec_from_file_location("crag_templates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.INSTRUCTIONS + "\n" + mod.IN_CONTEXT_EXAMPLES


def parse_judge_response(response: str) -> int:
    """Extraction du "score" 0/1 — même logique tolérante que l'officiel."""
    matches = re.findall(r"{([^}]*)}", response)
    text = ""
    for match in matches:
        text = "{" + match + "}"
    m = re.search(r'"score"\s*:\s*(\d+)', text)
    if not m:
        return -1
    score = int(m.group(1))
    return score if score in (0, 1) else -1


def judge(llm_url, llm_key, llm_model, system_message, query, ground_truth, prediction) -> int:
    r = requests.post(
        f"{llm_url}/chat/completions",
        headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
        json={
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": f"Question: {query}\n Ground truth: {ground_truth}\n Prediction: {prediction}\n"},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
        },
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"] or ""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return parse_judge_response(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--crag-repo", type=Path, required=True)
    ap.add_argument("--llm-url", required=True)
    ap.add_argument("--llm-key", required=True)
    ap.add_argument("--llm-model", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4, help="jugements en parallèle")
    args = ap.parse_args()

    system_message = load_official_prompts(args.crag_repo)
    llm_url = args.llm_url.rstrip("/")

    rows = [json.loads(line) for line in open(args.predictions, encoding="utf-8")]

    def verdict_for(row: dict) -> str:
        prediction = row["prediction"].strip()
        pred_low = prediction.lower()
        ground_truths = [row["answer"]] + list(row.get("alt_ans") or [])

        # tolérant aux apostrophes perdues ("I don know", "I dont know")
        if re.search(r"i don'?t? know|i do not know", pred_low) or not prediction:
            return "miss"
        for gt in ground_truths:
            gt = str(gt).strip()
            gt_low = gt.lower()
            if pred_low == gt_low:
                return "correct"
            if "invalid" in pred_low and "invalid" in gt_low:
                return "correct"
            if ("invalid" in pred_low) != ("invalid" in gt_low):
                continue  # hallucination pour cette GT, tenter la suivante
            try:
                score = judge(llm_url, args.llm_key, args.llm_model,
                              system_message, row["query"], gt, prediction)
            except Exception as e:
                print(f"  ! judge error ({row['interaction_id'][:8]}): {e}", file=sys.stderr)
                score = -1  # erreur judge → pas de crédit, la GT suivante peut encore matcher
            if score == 1:
                return "correct"
        return "hallucination"

    judged = []
    n_miss = n_correct = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (row, verdict) in enumerate(zip(rows, ex.map(verdict_for, rows)), 1):
            n_miss += verdict == "miss"
            n_correct += verdict == "correct"
            judged.append({**row, "verdict": verdict})
            if i % 10 == 0 or i == len(rows):
                print(f"[{i}/{len(rows)}] correct={n_correct} miss={n_miss}")

    n = len(judged)
    n_hallu = n - n_correct - n_miss
    results = {
        "score": round(((2 * n_correct + n_miss) / n - 1) * 100, 1),
        "accuracy": round(n_correct / n * 100, 1),
        "hallucination": round(n_hallu / n * 100, 1),
        "missing": round(n_miss / n * 100, 1),
        "n": n,
    }

    by = {}
    for dim in ("domain", "question_type"):
        agg = defaultdict(lambda: {"correct": 0, "miss": 0, "hallucination": 0})
        for row in judged:
            agg[row[dim]][row["verdict"] if row["verdict"] != "correct" else "correct"] += 1
        by[dim] = {k: dict(v) for k, v in sorted(agg.items())}

    judged_path = args.out.with_suffix(".judged.jsonl")
    with open(judged_path, "w", encoding="utf-8") as f:
        for row in judged:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    scoped = all(r.get("scoped") for r in rows)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# CRAG — rapport de scoring\n\n")
        f.write(f"- Mode : {'A (retrieval scopé aux pages de la question)' if scoped else 'corpus entier (NON comparable au leaderboard)'}\n")
        f.write(f"- Judge : {args.llm_model} (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)\n\n")
        f.write(f"| Score | Accuracy | Hallucination | Missing | n |\n|---|---|---|---|---|\n")
        f.write(f"| **{results['score']}** | {results['accuracy']}% | {results['hallucination']}% | {results['missing']}% | {results['n']} |\n\n")
        f.write("Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; "
                "hallucination < 20 % = bon signal.\n\n")
        for dim, table in by.items():
            f.write(f"## Par {dim}\n\n| {dim} | correct | miss | hallucination |\n|---|---|---|---|\n")
            for k, v in table.items():
                f.write(f"| {k} | {v.get('correct', 0)} | {v.get('miss', 0)} | {v.get('hallucination', 0)} |\n")
            f.write("\n")

    print(f"\n{json.dumps(results, indent=2)}")
    print(f"Rapport : {args.out}\nDétail : {judged_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
