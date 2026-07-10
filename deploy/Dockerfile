# Server image for the Postgres/VPS deployment (deploy/docker-compose.yml).
# The cloud tier is genuinely crawler-free (plan.md §19.1 Phase 9 Part 2) —
# no Playwright/Chromium, no agent/ code. Crawling only ever happens on an
# operator's own machine via the agent's standalone BFF (agent/bff/app.py,
# a PyInstaller build — see GovCrawler.spec), never inside this container.
#
# No config.yaml is baked into the image — portal/config.py's _CLOUD_DEFAULTS
# provide the full baseline; all security-sensitive values (DATABASE_URL*,
# JWT_SECRET, CREDENTIAL_ENC_KEY, …) come in via env vars at runtime.
# portal/paths.bootstrap() creates portal/data/ on first start.
FROM python:3.11-slim

WORKDIR /app

COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/cloud.txt

COPY portal/ portal/
COPY cloud/ cloud/
COPY shared/ shared/
COPY frontend/ frontend/
COPY alembic/ alembic/
COPY alembic.ini .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "portal", "serve"]
