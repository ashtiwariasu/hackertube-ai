#!/usr/bin/env bash
# Store the Google OAuth web client (the downloaded JSON) in Secret Manager and switch sign-in on.
# Usage: scripts/set-oauth.sh path/to/client_secret_XXXX.json   (reads PROJECT, REGION, SERVICE from deploy.env)
set -euo pipefail
JSON="$1"
cd "$(dirname "$0")/.."
set -a; . ./deploy.env; set +a
ID=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print((d.get('web') or d['installed'])['client_id'])" "$JSON")
SECRET=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print((d.get('web') or d['installed'])['client_secret'])" "$JSON")
for pair in "hackertube-google-client-id:$ID" "hackertube-google-client-secret:$SECRET"; do
  name=${pair%%:*}; val=${pair#*:}
  gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1 || gcloud secrets create "$name" --project "$PROJECT" --replication-policy automatic >/dev/null
  printf '%s' "$val" | gcloud secrets versions add "$name" --project "$PROJECT" --data-file=- >/dev/null
done
gcloud run services update "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --update-secrets GOOGLE_CLIENT_ID=hackertube-google-client-id:latest,GOOGLE_CLIENT_SECRET=hackertube-google-client-secret:latest --quiet
echo "Sign-in enabled."
