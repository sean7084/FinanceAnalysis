#!/usr/bin/env bash
# One-time MinIO setup for FinanceAnalysis model artifacts.
#
# Run ON HomeNAS after `docker compose up -d`. It creates the bucket, enables
# versioning, and mints a scoped service-account key for the app. The key is
# printed ONCE -- put it in the FinanceAnalysis `.env`
# (ARTIFACT_S3_ACCESS_KEY_ID / ARTIFACT_S3_SECRET_ACCESS_KEY) and in the NAS
# secrets inventory. Never commit it.
#
#   MINIO_ROOT_USER=... MINIO_ROOT_PASSWORD=... ./init-minio.sh
#
# Uses `mc` from a throwaway container (no host install needed). mc command names
# are stable across recent MinIO releases; if a subcommand differs on your version,
# run `mc <cmd> --help` and adjust -- the intent is: bucket, versioning, scoped key.
set -euo pipefail

ENDPOINT="${ENDPOINT:-http://192.168.31.8:9000}"
BUCKET="${BUCKET:-finance-analysis-artifacts}"
# Service accounts inherit the parent (root) policy. On a LAN-only, single-bucket
# box that is acceptable; for least privilege, create a dedicated user with a
# bucket-scoped policy instead (see README.md "Least-privilege option").
PARENT_USER="${PARENT_USER:-${MINIO_ROOT_USER:?export MINIO_ROOT_USER first}}"

: "${MINIO_ROOT_PASSWORD:?export MINIO_ROOT_PASSWORD first}"

# mc alias via env (format: scheme://access:secret@host); no host mc install needed.
export MC_HOST_finance="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${ENDPOINT#http://}"
mc() { docker run --rm --network host -e MC_HOST_finance minio/mc "$@"; }

echo "## bucket: ${BUCKET} at ${ENDPOINT}"
mc mb --ignore-existing "finance/${BUCKET}"

echo "## enabling versioning (cheap rollback; matches the retain-every-family policy)"
mc version enable "finance/${BUCKET}"

echo "## minting a scoped service-account key for the app"
mc admin user svcacct add finance "${PARENT_USER}"

cat <<EOF

## DONE. Record these (the AccessKey/SecretKey printed just above) in:
##   FinanceAnalysis .env  ->  ARTIFACT_S3_ENDPOINT_URL=${ENDPOINT}
##                             ARTIFACT_S3_BUCKET=${BUCKET}
##                             ARTIFACT_S3_ACCESS_KEY_ID=<access key>
##                             ARTIFACT_S3_SECRET_ACCESS_KEY=<secret key>
##                             ARTIFACT_STORE_BACKEND=s3
##   NAS secrets inventory ->  docs/reference/secrets-inventory.md (location only)
EOF
