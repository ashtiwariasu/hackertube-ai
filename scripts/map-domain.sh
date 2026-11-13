#!/usr/bin/env bash
# Map your domain (and its www) to the Cloud Run service once the domain is verified in Search Console; prints the DNS
# records to add. Reads PROJECT, REGION, SERVICE and BASE_URL from deploy.env.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./deploy.env; set +a
apex=${BASE_URL#*://}; apex=${apex%%/*}; apex=${apex#www.}
for d in "$apex" "www.$apex"; do
  gcloud beta run domain-mappings describe --domain "$d" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud beta run domain-mappings create --service "$SERVICE" --domain "$d" --region "$REGION" --project "$PROJECT" --quiet
done
for d in "$apex" "www.$apex"; do
  echo "== $d"; gcloud beta run domain-mappings describe --domain "$d" --region "$REGION" --project "$PROJECT" \
    --format="table(status.resourceRecords[].type,status.resourceRecords[].name,status.resourceRecords[].rrdata)"
done
