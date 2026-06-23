# MishaCrawler

An async web crawler for extracting email leads from Indian government websites (`.gov.in` / `.nic.in`). Built on
Playwright with concurrent workers, per-domain rate limiting, and a post-crawl classification pipeline.

## Features

- **Concurrent async crawling** — configurable worker pool with per-domain rate limiting (one request at a time per
  domain)
- **Three-tier seed generation** — Google CSE / Bing API → india.gov.in directory (httpx + playwright-stealth) →
  hardcoded seed list of 98 targeted contact pages
- **Smart link filtering** — from non-contact pages, only follows links matching contact / officer / tender keywords;
  prevents data and stats pages from flooding the queue
- **Depth-tapered link budget** — depth-0 seeds get 80 links, depth-1 gets 26, depth-2+ gets 15; avoids crawl sprawl
  while still following paginated officer directories
- **Cross-domain discovery** — follows `.gov.in` / `.nic.in` links across ministry boundaries, auto-discovering new
  domains without manual seeding
- **Email extraction** — normalises obfuscated addresses (`[at]`, `[dot]`, Unicode variants) before applying regex;
  filters to government suffixes only
- **SQLite persistence** — visited URLs and leads survive restarts; deduplication enforced at DB level
- **Post-crawl classification** — `classify.py` tags every lead with ministry name, tier (central / state / district /
  statutory / psu), state, and confidence level

## Project Structure

```text
MishaCrawler/
├── main.py          # Entry point — arg parsing, Playwright lifecycle, CSV export
├── classify.py      # Post-crawl classifier — reads DB, writes classified_leads.csv
├── config.yaml      # All tunable parameters
├── requirements.txt
└── src/
    ├── crawler.py   # Worker pool, link queuing, smart filtering, depth taper
    ├── seeder.py    # Three-tier seed generation, sitemap parsing
    ├── parser.py    # Email extraction and normalisation
    └── storage.py   # SQLite wrapper — visited URLs, leads, CSV export
```

## Setup

**Requirements:** Python 3.11+

```bash
# 1. Clone and enter the repo
git clone https://github.com/Jaguar000212/MishaCrawler.git
cd MishaCrawler

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Playwright browser (Chromium only)
playwright install chromium
```

## Configuration

All parameters live in `config.yaml`. Key settings:

| Parameter             | Default | Description                                                                  |
|-----------------------|---------|------------------------------------------------------------------------------|
| `max_depth`           | `4`     | Crawl depth. Seeds are depth 0; each link followed increments depth          |
| `max_links_per_page`  | `80`    | Link budget at depth 0; tapered automatically at deeper levels               |
| `num_workers`         | `55`    | Concurrent Playwright workers. Per-domain semaphore keeps rate limiting safe |
| `page_timeout`        | `30`    | Seconds before a page navigation is retried once, then abandoned             |
| `url_process_timeout` | `75`    | Hard outer timeout per URL — must exceed `page_timeout × 2 + 5`              |
| `request_delay`       | `1.5`   | Seconds between requests to the same domain                                  |

To enable Google CSE or Bing search seeding, add your API keys and set `enabled: true` under the relevant section in
`config.yaml`.

## Running

```bash
# Standard run — uses all config.yaml defaults
python3 main.py

# Override specific settings from the command line
python3 main.py --workers 80 --max_depth 4

# Full option list
python3 main.py --help
```

The crawler logs to both stdout and `crawler.log`. Press `Ctrl+C` to stop gracefully — leads collected so far are saved
to `leads.csv`.

## Output

**`leads.csv`** — raw leads, written at the end of every run:

| Column                     | Description                                         |
|----------------------------|-----------------------------------------------------|
| `Email`                    | Extracted email address                             |
| `Source URL`               | Page where it was found                             |
| `Page Title`               | HTML title of the source page                       |
| `Context/Surrounding Text` | ~100 chars around the email for manual verification |
| `Scraped At`               | ISO timestamp                                       |

**`crawler_session.db`** — SQLite database. Leads accumulate across runs; visited URLs are deduplicated so the same page
is never crawled twice in the same session.

## Post-crawl Classification

After a run completes, classify every lead by ministry, tier, and state:

```bash
python3 classify.py
# Output: classified_leads.csv
```

The classifier adds these columns to every lead:

| Column       | Description                                                                          |
|--------------|--------------------------------------------------------------------------------------|
| `ministry`   | Human-readable name (e.g. `Ministry of External Affairs`)                            |
| `tier`       | `central` / `state` / `district` / `statutory` / `psu`                               |
| `state`      | Populated for state and district tier rows                                           |
| `confidence` | `high` = exact domain map match; `low` = inferred from domain string                 |
| `nic_email`  | `True` if the email is `@nic.in` or `@gov.in` — generic infrastructure, not personal |

Terminal summary printed on completion:

```text
==================================================
  Total leads classified : 2011
  By tier:
    central        847
    state          612
    statutory      310
    district       180
    psu             62
  Confidence:
    high (exact domain map match) : 1890
    low  (inferred from domain)   : 121
==================================================
```

Rows with `confidence=low` are domains discovered via cross-domain link following that aren't in the built-in map. Add
them to `DOMAIN_MAP` in `classify.py` to get `high` confidence on future runs.

To classify a specific database file or write to a custom path:

```bash
python3 classify.py --db my_session.db --out my_results.csv
```
