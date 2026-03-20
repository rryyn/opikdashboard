# Opik Dashboard

This project exports Nexa conversation logs from Opik and generates a shareable HTML dashboard for internal analysis.

## What This Project Does

It does three things:

1. Export conversation logs from Opik
2. Analyze the exported conversations
3. Generate static HTML dashboards for `qe` and `prod`

The generated output is a static site, not a backend service.

## Main Files

- [`run_export.sh`](/Users/linwang/codex-test/run_export.sh)
  Exports the latest logs from Opik using the current environment config.
- [`export_opik_logs.py`](/Users/linwang/codex-test/export_opik_logs.py)
  Pulls raw traces and writes normalized export files.
- [`generate_analysis_dashboard.py`](/Users/linwang/codex-test/generate_analysis_dashboard.py)
  Builds the dashboard HTML and conversation detail pages.
- [`opik_config.json`](/Users/linwang/codex-test/opik_config.json)
  Local Opik config for base URL, project, and target env.

## Generated Output

These files are generated locally and are not committed:

- `analysis_dashboard.html`
- `analysis_dashboard_qe.html`
- `analysis_dashboard_prod.html`
- `conversation_details_qe/`
- `conversation_details_prod/`

## Local Setup

Create and activate a virtualenv:

```bash
python3 -m venv /Users/linwang/.venv
source /Users/linwang/.venv/bin/activate
pip install -r requirements.txt
```

## How To Run

### Export latest logs

By default, [`run_export.sh`](/Users/linwang/codex-test/run_export.sh) uses `qe`:

```bash
cd /Users/linwang/codex-test
source /Users/linwang/.venv/bin/activate
./run_export.sh
```

### Generate dashboard

```bash
python3 generate_analysis_dashboard.py
```

### Open dashboard

```bash
open analysis_dashboard.html
```

## Environment Switching

The project currently supports:

- `qe`
- `prod`

The generated dashboard has environment tabs at the top.  
The default [`analysis_dashboard.html`](/Users/linwang/codex-test/analysis_dashboard.html) currently points to `qe`.

Environment snapshots are stored under:

- `env_data/qe/`
- `env_data/prod/`

## Typical Workflow

When you update code locally:

```bash
cd /Users/linwang/codex-test
git add .
git commit -m "describe your change"
git push
```

When you want fresh dashboard data:

```bash
cd /Users/linwang/codex-test
source /Users/linwang/.venv/bin/activate
./run_export.sh
python3 generate_analysis_dashboard.py
open analysis_dashboard.html
```

## Deployment Idea

The simplest production setup is:

1. Put this repo on an internal machine that can access Opik
2. Run export + dashboard generation on a schedule
3. Publish the generated HTML and detail directories through a static file server

Required published artifacts:

- `analysis_dashboard.html`
- `analysis_dashboard_qe.html`
- `analysis_dashboard_prod.html`
- `conversation_details_qe/`
- `conversation_details_prod/`

## Notes

- Do not commit raw export data or generated HTML artifacts
- Do not commit secrets into `opik_config.json`
- If a thread link is missing, it usually means the raw conversation was not present in the current export
