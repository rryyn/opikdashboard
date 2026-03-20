# Deployment

This project is easiest to deploy as a static internal site.

## What "deployment" means here

This project is not a long-running backend service.

Deployment means:

1. Run exports against Opik
2. Generate the HTML dashboards
3. Copy the generated files to a directory served by nginx or another static file server

## What You Need On The Deployment Machine

- A machine that can access both Opik environments
- Python 3
- A virtualenv with project dependencies installed
- Access to a static publish directory

## First-Time Setup

Clone the repo:

```bash
git clone git@github.com:rryyn/opikdashboard.git
cd opikdashboard
```

Create a virtualenv and install dependencies:

```bash
python3 -m venv /Users/linwang/.venv
source /Users/linwang/.venv/bin/activate
pip install -r requirements.txt
```

Make the deploy script executable:

```bash
chmod +x deploy.sh
```

## One-Command Deploy

By default, the deploy script:

- exports `qe`
- exports `prod`
- refreshes `env_data/qe`
- refreshes `env_data/prod`
- regenerates the dashboards
- publishes the output into `published_site/`

Run:

```bash
./deploy.sh
```

## Publish Directory

Default publish directory:

```bash
./published_site
```

You can override it:

```bash
PUBLISH_DIR=/var/www/nexa-dashboard ./deploy.sh
```

## Optional Environment Overrides

If your deployment machine needs different values, override them when running:

```bash
QE_OPIK_BASE_URL=http://opik-nexa-01.us-east4.qe.gcp.conviva.com:5173/api \
QE_OPIK_PROJECT=pa-fat3 \
QE_TARGET_ENV=pa-fat3 \
PROD_OPIK_BASE_URL=http://opik.prod.conviva.com/api \
PROD_OPIK_PROJECT=pa-prod \
PROD_TARGET_ENV=pa-prod \
PUBLISH_DIR=/var/www/nexa-dashboard \
./deploy.sh
```

## Files That Must Be Published

- `analysis_dashboard.html`
- `analysis_dashboard_qe.html`
- `analysis_dashboard_prod.html`
- `conversation_details_qe/`
- `conversation_details_prod/`

If the detail directories are missing, the thread links in the dashboard will break.

## Updating After Code Changes

When you update code:

```bash
git pull
./deploy.sh
```

That is the full refresh flow.

## Optional Cron Job

Example: refresh every hour

```cron
0 * * * * cd /path/to/opikdashboard && ./deploy.sh >> /tmp/opikdashboard-deploy.log 2>&1
```

## Optional nginx Setup

If you publish to `/var/www/nexa-dashboard`, a minimal nginx site looks like:

```nginx
server {
    listen 80;
    server_name your-internal-host;

    root /var/www/nexa-dashboard;
    index analysis_dashboard.html;

    location / {
        try_files $uri $uri/ /analysis_dashboard.html;
    }
}
```
