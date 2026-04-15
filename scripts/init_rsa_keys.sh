#!/usr/bin/env bash
# init_rsa_keys.sh — Generate a unique RSA keypair for this deployment.
#
# Usage:
#   export RSA_PASSPHRASE="<strong-random-passphrase>"
#   bash scripts/init_rsa_keys.sh
#
# After running:
#   1. The new public key is printed — paste it into:
#      - web/src/utils/index.ts       (rsaPsw function, const pub = '...')
#      - management/web/src/utils/crypto.ts  (RAGFLOW_PUBLIC_KEY = '...')
#   2. Keep RSA_PASSPHRASE in your secrets manager (e.g. Vault, AWS Secrets Manager).
#      The RAGFlow backend reads it via the RSA_PASSPHRASE env var at startup.
#      Add it to your docker/.env or systemd unit.
#
# WARNING: Running this script invalidates all existing sessions. Users do not
#          need to reset their passwords — passwords are stored as scrypt hashes,
#          RSA is only used for transmission. After key rotation, the next login
#          will simply re-encrypt with the new key.

set -euo pipefail

CONF_DIR="$(cd "$(dirname "$0")/.." && pwd)/conf"
PRIVATE_KEY="$CONF_DIR/private.pem"
PUBLIC_KEY="$CONF_DIR/public.pem"

if [ -z "${RSA_PASSPHRASE:-}" ]; then
  echo "ERROR: RSA_PASSPHRASE env var is not set."
  echo "Generate one with: openssl rand -base64 32"
  exit 1
fi

echo "Generating 2048-bit RSA keypair..."
openssl genrsa -aes192 -passout env:RSA_PASSPHRASE -out "$PRIVATE_KEY" 2048
openssl rsa -in "$PRIVATE_KEY" -passin env:RSA_PASSPHRASE -pubout -out "$PUBLIC_KEY"

echo ""
echo "Keys written to:"
echo "  Private: $PRIVATE_KEY"
echo "  Public:  $PUBLIC_KEY"
echo ""
echo "Public key (one-liner for frontend hardcoding):"
# Collapse to single line without headers — matches RAGFlow frontend format
PUB_ONELINER=$(awk '!/^-----/' "$PUBLIC_KEY" | tr -d '\n')
echo "-----BEGIN PUBLIC KEY-----${PUB_ONELINER}-----END PUBLIC KEY-----"
echo ""
echo "Paste the line above into:"
echo "  web/src/utils/index.ts                → const pub = '<line above>'"
echo "  management/web/src/utils/crypto.ts    → const RAGFLOW_PUBLIC_KEY = '<line above>'"
