#!/usr/bin/env bash
# Test d'intégration du provider sandbox k8s sur kind.
# Usage: K8S_SANDBOX_TEST=1 bash scripts/sandbox_kind_test.sh
set -euo pipefail
[ "${K8S_SANDBOX_TEST:-}" = "1" ] || { echo "K8S_SANDBOX_TEST != 1 — skip"; exit 0; }

CLUSTER=ragflow-sbx-test
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s

if ! kind load docker-image sandbox-base-python:latest --name "$CLUSTER"; then
  echo "kind load docker-image failed — repli image-archive (podman)"
  kind load image-archive <(docker save sandbox-base-python:latest) --name "$CLUSTER"
fi

# Namespace + RBAC rendus par le chart (subjects sans importance ici : on
# teste la MÉCANIQUE provider avec le kubeconfig kind admin ; le RBAC réel
# est vérifié en prod Task 8 via kubectl auth can-i).
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml \
  --set sandbox.enabled=true \
  --set sandbox.networkPolicy.enabled=false \
  --set sandbox.image.repository=sandbox-base-python --set sandbox.image.tag=latest \
  --show-only templates/sandbox/namespace.yaml \
  --show-only templates/sandbox/resourcequota.yaml | kubectl --context "kind-$CLUSTER" apply -f -

# kubeconfig kind admin -> fichier temp, injecté dans le heredoc via la
# variable d'env SBX_KUBECONFIG (le heredoc Python la lit avec
# os.environ["SBX_KUBECONFIG"] — pas de placeholder en dur dans le script).
KIND_KUBECONFIG_FILE=$(mktemp)
trap 'rm -f "$KIND_KUBECONFIG_FILE"' EXIT
kind get kubeconfig --name "$CLUSTER" > "$KIND_KUBECONFIG_FILE"
export SBX_KUBECONFIG="$KIND_KUBECONFIG_FILE"

PYTHONPATH=. uv run --with kubernetes python - <<'PY'
import json
import os

from agent.sandbox.providers.k8s import K8sProvider

p = K8sProvider()
assert p.initialize({
    "namespace": "rag-sandbox",
    "image": "sandbox-base-python:latest",
    "kubeconfig_path": os.environ["SBX_KUBECONFIG"],
}), "initialize failed"
code = (
    "import famat_recipes as fr, json\n"
    "def main():\n"
    "    rows = [(i+1, f'P{i:02d}', 10, 'CORRECTION_X', 0.001*i, f'2026-01-01 10:{i:02d}:00') for i in range(25)]\n"
    "    d = fr.drift(fr.load_rows(rows), 'CORRECTION_X', 10)\n"
    "    fr.spc_chart(d, out_dir='artifacts')\n"
    "    return json.dumps({'n_parts': d['n_parts'], 'segments': len(d['segments'])})\n"
)
r = p.execute_code("it", code, "python", timeout=90)
print("exit_code:", r.exit_code)
print("stderr:", r.stderr[:500])
assert r.exit_code == 0, r.stderr
arts = r.metadata.get("artifacts", [])
assert any(a.get("name", "").endswith(".svg") for a in arts), f"pas d'artefact SVG: {[a.get('name') for a in arts]}"
print("OK — recette exécutée dans un Job kind, artefact SVG collecté")
PY
echo "=== SUCCÈS intégration kind ==="
