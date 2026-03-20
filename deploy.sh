#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PATH="${VENV_PATH:-/Users/linwang/.venv}"
PUBLISH_DIR="${PUBLISH_DIR:-$PROJECT_DIR/published_site}"
WORK_DIR="${WORK_DIR:-$PROJECT_DIR/.deploy_work}"

mkdir -p "$WORK_DIR"
mkdir -p "$PROJECT_DIR/env_data"
mkdir -p "$PUBLISH_DIR"

source "$VENV_PATH/bin/activate"

run_export_for_env() {
  local env_key="$1"
  local export_dir="$PROJECT_DIR/env_data/$env_key"
  local temp_dir="$WORK_DIR/$env_key"

  local opik_base_url=""
  local opik_project=""
  local target_env=""

  case "$env_key" in
    qe)
      opik_base_url="${QE_OPIK_BASE_URL:-http://opik-nexa-01.us-east4.qe.gcp.conviva.com:5173/api}"
      opik_project="${QE_OPIK_PROJECT:-pa-fat3}"
      target_env="${QE_TARGET_ENV:-pa-fat3}"
      ;;
    prod)
      opik_base_url="${PROD_OPIK_BASE_URL:-http://opik.prod.conviva.com/api}"
      opik_project="${PROD_OPIK_PROJECT:-pa-prod}"
      target_env="${PROD_TARGET_ENV:-pa-prod}"
      ;;
    *)
      echo "Unsupported env: $env_key"
      exit 1
      ;;
  esac

  rm -rf "$temp_dir"
  mkdir -p "$temp_dir"
  mkdir -p "$export_dir"

  echo "Exporting $env_key ..."
  (
    cd "$temp_dir"
    OPIK_BASE_URL="$opik_base_url" \
    OPIK_PROJECT="$opik_project" \
    TARGET_ENV="$target_env" \
    OPIK_API_KEY="${OPIK_API_KEY:-}" \
    OPIK_WORKSPACE="${OPIK_WORKSPACE:-}" \
    OPIK_URL_OVERRIDE="${OPIK_URL_OVERRIDE:-}" \
    python3 "$PROJECT_DIR/export_opik_logs.py"
  )

  cp "$temp_dir/conversations.jsonl" "$export_dir/conversations.jsonl"
  cp "$temp_dir/conversations_summary.csv" "$export_dir/conversations_summary.csv"
  cp "$temp_dir/turns_flat.csv" "$export_dir/turns_flat.csv"

  printf '%s\n' \
    "{" \
    "  \"OPIK_BASE_URL\": \"$opik_base_url\"," \
    "  \"OPIK_PROJECT\": \"$opik_project\"," \
    "  \"TARGET_ENV\": \"$target_env\"," \
    "  \"OPIK_API_KEY\": \"\"," \
    "  \"OPIK_WORKSPACE\": \"${OPIK_WORKSPACE:-}\"," \
    "  \"OPIK_URL_OVERRIDE\": \"${OPIK_URL_OVERRIDE:-}\"" \
    "}" > "$export_dir/config.json"
}

run_export_for_env qe
run_export_for_env prod

echo "Generating dashboards ..."
python3 "$PROJECT_DIR/generate_analysis_dashboard.py"

echo "Publishing to $PUBLISH_DIR ..."
rm -rf "$PUBLISH_DIR/conversation_details_qe"
rm -rf "$PUBLISH_DIR/conversation_details_prod"
cp "$PROJECT_DIR/analysis_dashboard.html" "$PUBLISH_DIR/analysis_dashboard.html"
cp "$PROJECT_DIR/analysis_dashboard_qe.html" "$PUBLISH_DIR/analysis_dashboard_qe.html"
cp "$PROJECT_DIR/analysis_dashboard_prod.html" "$PUBLISH_DIR/analysis_dashboard_prod.html"
cp -R "$PROJECT_DIR/conversation_details_qe" "$PUBLISH_DIR/conversation_details_qe"
cp -R "$PROJECT_DIR/conversation_details_prod" "$PUBLISH_DIR/conversation_details_prod"

echo "Done."
echo "Published files:"
echo "  $PUBLISH_DIR/analysis_dashboard.html"
echo "  $PUBLISH_DIR/analysis_dashboard_qe.html"
echo "  $PUBLISH_DIR/analysis_dashboard_prod.html"
