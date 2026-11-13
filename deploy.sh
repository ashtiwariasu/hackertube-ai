#!/usr/bin/env bash
# Deploy HackerTube AI to Google Cloud Run. Every setting comes from deploy.env (copy deploy.env.example and fill it in);
# nothing about a particular deployment lives in this file. Secrets come from Secret Manager in the same project and are
# attached only if they exist there.
set -euo pipefail
cd "$(dirname "$0")"
[ -f deploy.env ] || { echo "No deploy.env: copy deploy.env.example to deploy.env and fill it in." >&2; exit 1; }
set -a; . ./deploy.env; set +a
: "${PROJECT:?set PROJECT in deploy.env}" "${REGION:?set REGION in deploy.env}" "${SERVICE:?set SERVICE in deploy.env}"
: "${BASE_URL:?set BASE_URL in deploy.env}"

has() { gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1; }
SECRETS="SESSION_SECRET=hackertube-session-secret:latest"
has STRIPE_SECRET_KEY && SECRETS+=",STRIPE_SECRET_KEY=STRIPE_SECRET_KEY:latest"
has ANTHROPIC_API_KEY && SECRETS+=",ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"
has hackertube-youtube-api-key && SECRETS+=",YOUTUBE_API_KEY=hackertube-youtube-api-key:latest"
has hackertube-stripe-webhook-secret && SECRETS+=",STRIPE_WEBHOOK_SECRET=hackertube-stripe-webhook-secret:latest"
has hackertube-google-client-id && SECRETS+=",GOOGLE_CLIENT_ID=hackertube-google-client-id:latest,GOOGLE_CLIENT_SECRET=hackertube-google-client-secret:latest"
# Partner Program: handles stay stable if the session secret is ever rotated; admins come from a secret, not a file
has hackertube-handle-salt && SECRETS+=",HANDLE_SALT=hackertube-handle-salt:latest"
has hackertube-admin-uids && SECRETS+=",ADMIN_UIDS=hackertube-admin-uids:latest"

# Plain settings, joined with | so values may contain commas. Empty optional values are left out.
ENV="BASE_URL=$BASE_URL|STORE=firestore|GCP_PROJECT=$PROJECT"
for k in DATA_BUCKET SOURCE_URL STRIPE_PRICE_ID POSTHOG_KEY POSTHOG_HOST GA_MEASUREMENT_ID OPERATOR_NAME OPERATOR_PLACE OPERATOR_CONTACT \
         HT_NOTES PILOT_TOPIC HACKER_AUTO_APPROVE PARTNER_CAP_USD; do
  [ -n "${!k:-}" ] && ENV+="|$k=${!k}"
done

gcloud run deploy "$SERVICE" --source . --project "$PROJECT" --region "$REGION" \
  --allow-unauthenticated --memory "${MEMORY:-4Gi}" --cpu 1 --min-instances "${MIN_INSTANCES:-1}" --max-instances 10 \
  --concurrency 40 --set-env-vars "^|^$ENV" --set-secrets "$SECRETS" --quiet
