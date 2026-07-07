# Server image for the Postgres/VPS deployment (deploy/docker-compose.yml).
# The crawler engine hasn't been split into a separate local agent yet, so
# this same image runs migrations, serves the API, and executes crawls
# in-process — it still needs Playwright + a Chromium browser.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY portal/ portal/
COPY cloud/ cloud/
COPY agent/ agent/
COPY shared/ shared/
COPY GovScraper/ GovScraper/
COPY alembic/ alembic/
COPY alembic.ini .
COPY deploy/config.docker.yaml portal/config.yaml

RUN mkdir -p portal/data

ENV PYTHONUNBUFFERED=1

# portal/paths.py's bootstrap() unconditionally sets PLAYWRIGHT_BROWSERS_PATH
# to <app_dir>/playwright_browsers (needed for the PyInstaller desktop build,
# which ships browsers next to the .exe). That runs on every container start
# too, so pin browsers install to the exact same path here — otherwise the
# app looks for browsers at a location `playwright install` never wrote to.
ENV PLAYWRIGHT_BROWSERS_PATH=/app/playwright_browsers
RUN playwright install --with-deps chromium

CMD ["python", "-m", "portal", "serve"]
