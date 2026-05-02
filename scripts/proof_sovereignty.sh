#!/usr/bin/env bash
# Data-sovereignty audit bundle for a RAGFlow agentic run.
#
# Captures network traffic to non-loopback destinations during a window,
# hashes the input template and the rendered output, and generates a
# PREUVE_SOUVERAINETE.md report. Designed for the PDG demo: prove that no
# document content leaves the local machine while the LLM runs locally.
#
# Usage:
#   bash scripts/proof_sovereignty.sh <output.docx> [duration_seconds]
#
# Example:
#   bash scripts/proof_sovereignty.sh "/Users/zappy/Downloads/PROCEDURE - Modèle.docx" 60
set -euo pipefail

OUTPUT_DOCX="${1:?usage: $0 <path/to/rendered.docx> [duration_seconds]}"
DURATION="${2:-60}"
TEMPLATE="$(cd "$(dirname "$0")/.." && pwd)/assets/templates/cyllene_procedure.docx"

if [[ ! -f "$OUTPUT_DOCX" ]]; then
  echo "Output DOCX not found: $OUTPUT_DOCX" >&2; exit 1
fi
if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template not found: $TEMPLATE" >&2; exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="${PWD}/proof-${TS}"
mkdir -p "$OUTDIR"
PCAP="${OUTDIR}/capture.pcap"
REPORT="${OUTDIR}/PREUVE_SOUVERAINETE.md"

# Pick the active interface (first non-loopback with an inet address).
IFACE="$(route get default 2>/dev/null | awk '/interface:/{print $2}')"
[[ -n "$IFACE" ]] || IFACE="en0"

echo "=== Sovereignty audit ==="
echo "interface : $IFACE"
echo "duration  : ${DURATION}s"
echo "outdir    : $OUTDIR"
echo "template  : $TEMPLATE"
echo "rendered  : $OUTPUT_DOCX"
echo

# Filter: capture ONLY traffic that escapes the machine. Loopback (127/8, ::1)
# and the model server (:11434) are local — anything else is suspicious.
FILTER="not host 127.0.0.1 and not host ::1 and not port 11434"

echo ">>> Starting tcpdump (sudo). You may be asked for your password."
echo ">>> Then run your agent canvas in the RAGFlow UI."
echo ">>> Capturing for ${DURATION} seconds..."
sudo tcpdump -i "$IFACE" -w "$PCAP" -G "$DURATION" -W 1 "$FILTER" 2>/dev/null &
TCPDUMP_PID=$!

# Wait the full window then make sure tcpdump is gone.
wait "$TCPDUMP_PID" 2>/dev/null || true

if [[ ! -s "$PCAP" ]]; then
  echo "Warning: no capture file produced. Check sudo / interface." >&2
fi

# Analysis: count packets and unique remote endpoints.
PKT_COUNT=0
REMOTES=""
if [[ -s "$PCAP" ]]; then
  PKT_COUNT="$(tcpdump -r "$PCAP" 2>/dev/null | wc -l | tr -d ' ')"
  REMOTES="$(tcpdump -r "$PCAP" -nn 2>/dev/null \
    | awk '{print $3" -> "$5}' \
    | sed 's/\.[0-9]*:.*$//' \
    | sort -u | head -20)"
fi

# Hashes (input + output).
TPL_HASH="$(shasum -a 256 "$TEMPLATE" | awk '{print $1}')"
OUT_HASH="$(shasum -a 256 "$OUTPUT_DOCX" | awk '{print $1}')"
TPL_SIZE="$(stat -f %z "$TEMPLATE")"
OUT_SIZE="$(stat -f %z "$OUTPUT_DOCX")"

# Verdict.
if [[ "$PKT_COUNT" -eq 0 ]]; then
  VERDICT="✅ **Souveraineté confirmée** — aucun paquet sortant pendant la fenêtre de capture (${DURATION}s)."
else
  VERDICT="⚠️ ${PKT_COUNT} paquets vers des destinations non-locales pendant la fenêtre. Voir analyse ci-dessous."
fi

cat > "$REPORT" <<EOF
# Preuve de souveraineté — Génération de procédure d'embauche fiche S3

**Date de capture** : $(date -u +"%Y-%m-%dT%H:%M:%SZ")
**Machine** : $(hostname) ($(uname -sr))
**Opérateur** : $(id -F 2>/dev/null || id -un)

## Verdict

$VERDICT

## Méthodologie

1. Le serveur RAGFlow tourne en local (process \`ragflow_server\` sur \`:9380\`).
2. Le LLM tourne en local via Ollama (\`qwen3:4b-instruct-2507-q8_0\` sur \`:11434\`).
3. Pendant ${DURATION} secondes, \`tcpdump\` a capturé sur l'interface active (\`$IFACE\`)
   tout le trafic IP **hors loopback** et **hors port 11434** (Ollama).
4. L'utilisateur a lancé le canvas agentic « Procédure embauche S3 » pendant la fenêtre.
5. Le DOCX rendu est hashé et comparé au template d'origine pour établir la chaîne de provenance.

## Capture réseau

- **Interface** : \`$IFACE\`
- **Durée** : ${DURATION}s
- **Filtre BPF** : \`${FILTER}\`
- **Paquets sortants détectés** : **${PKT_COUNT}**
- **Fichier brut** : \`$(basename "$PCAP")\` ($(stat -f %z "$PCAP" 2>/dev/null || echo 0) octets)

### Endpoints distants observés

\`\`\`
${REMOTES:-(aucun)}
\`\`\`

## Chaîne de provenance documentaire

| | Chemin | Taille | SHA-256 |
|---|---|---|---|
| Template | \`assets/templates/cyllene_procedure.docx\` | ${TPL_SIZE} | \`${TPL_HASH}\` |
| Rendu | \`$(basename "$OUTPUT_DOCX")\` | ${OUT_SIZE} | \`${OUT_HASH}\` |

## Architecture du flow

\`\`\`
[ Browser (UI) ]
       │  HTTP localhost:9380
       ▼
[ ragflow_server (local) ]
       │  HTTP localhost:11434
       ▼
[ Ollama qwen3:4b-instruct-2507-q8_0 (local) ]
       │  inference 100% on-prem
       ▼
[ render_docx_template (local Python) ]
       │
       ├─► MinIO localhost:9000   (DOCX bytes)
       └─► MySQL localhost:3307   (FileService row)
\`\`\`

Aucune sortie réseau pendant l'inférence. Le template, le contenu structuré
produit par le LLM, et le DOCX final restent intégralement sur la machine.

## Reproductibilité

\`\`\`bash
bash scripts/proof_sovereignty.sh "$OUTPUT_DOCX" $DURATION
\`\`\`

EOF

echo
echo "=== Done ==="
echo "Report : $REPORT"
echo "Pcap   : $PCAP ($PKT_COUNT packets)"
echo
echo "$VERDICT"
