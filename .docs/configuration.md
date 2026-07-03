# Configuration Reference

Config lives in `portal/config.yaml` (live, user-editable) and `portal/default_config.yaml` (shipped defaults, read-only
in the compiled `.exe`).

On first run, `default_config.yaml` is copied to `config.yaml` if it does not exist. Changes to `config.yaml` are picked
up by the Settings page in the UI (`POST /api/config`) or by restarting the server.

> **Crawler settings** (workers, depth, timeouts, keywords) take effect on the **next** job created after saving.
> In-flight jobs continue with their original settings.

---

## Full Default Configuration

```yaml
# ── Database ───────────────────────────────────────────────────────────────────
database:
  uri: sqlite:///portal/data/govcrawler.db
  # PostgreSQL alternative:
  # uri: postgresql://user:password@localhost:5432/govcrawler

# ── API Server ─────────────────────────────────────────────────────────────────
api:
  host: 127.0.0.1  # bind address; GUI opens browser on the same host
  port: 8001

# ── GovScraper (domain import from india.gov.in) ───────────────────────────────
scraper:
  category_filter: ''    # e.g. 'ug' — import only this category; empty = all
  org_type_filter: ''    # e.g. 'dept' — filter by org type; empty = all

# ── Crawler Engine ─────────────────────────────────────────────────────────────
crawler:
  workers: 10            # concurrent async worker coroutines
  max_depth: 4           # 0 = seed page only; 4 = seed + 4 levels deep
  recrawl_days: 30       # skip URLs visited in any job within last N days

  # Fetch strategy
  httpx_first: true      # try plain HTTP before launching browser
  playwright_fallback: false  # enable Playwright for JS-heavy sites

  # Timeouts
  httpx_timeout:
    connect: 10          # TCP connect timeout (seconds)
    read: 30             # HTTP read timeout (seconds)
  playwright_timeout: 45 # page.goto() timeout (seconds)
  js_settle_time: 3.0    # extra wait after domcontentloaded for JS (seconds)
  per_url_timeout: 100   # hard watchdog per URL — kills stalled workers (seconds)

  # Politeness
  request_delay: 1.5     # minimum seconds between requests to the same netloc

  # Filtering
  target_suffixes:
    - .gov.in
    - .nic.in
  # Only crawl URLs whose netloc ends in one of these.
  # Empty list = accept all domains (not recommended).
  # Ignored entirely for custom-URL jobs (crawler.max_custom_urls below) — a caller
  # who supplies explicit URLs has already chosen them deliberately.

  max_custom_urls: 50
  # Cap on how many ad-hoc URLs a single POST /api/jobs {custom_urls: [...]} request
  # may supply, as an alternative to domain_ids-based seeding. See crawler.md.

  priority_keywords:
    - contact
    - officer
    - directory
    - whos-who
    - who-is-who
    - staff
    - personnel
    - secretariat
    - about-us
    - division
    - minister
    - committee
    - administration
    - team
    - telephone
    - tele-directory
    - phone-directory
    - email
  # URLs containing any of these are assigned priority 0 (crawled first).
  # Empty list = no prioritization (all URLs treated equally).

  skip_extensions:
    - .pdf
    - .doc
    - .docx
    - .xls
    - .xlsx
    - .ppt
    - .pptx
    - .zip
    - .rar
    - .7z
    - .tar
    - .gz
    - .jpg
    - .jpeg
    - .png
    - .gif
    - .svg
    - .ico
    - .mp4
    - .mp3
    - .avi
    - .mov
  # URLs whose path ends in these extensions are never enqueued.

  js_indicators:
    - '<div id="__next"'
    - '<div id="root"'
    - 'Please enable JavaScript'
    - 'You need to enable JavaScript'
    - 'This page requires JavaScript'
  # If any indicator appears in the HTTPX response body, Playwright is used instead.

  user_agent: >-
    Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36
    (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36

  # Max links to follow per page, keyed by depth level.
  # Depth 0 = seed page, depth 1 = first hop, etc.
  max_links_per_page:
    0: 100
    1: 50
    2: 40
    default: 20   # used for depths not explicitly listed

  # Pagination-aware crawling — see crawler.md for the full detection/election
  # algorithm. A page's single elected "next page" link bypasses max_links_per_page
  # and priority_keywords entirely and is followed up to max_pagination_pages hops,
  # sharing one max_chain_children budget for non-pagination children across the chain.
  pagination:
    enabled: true
    max_pagination_pages: 50    # max hops followed down one pagination chain
    max_chain_children: 100     # shared cap on non-pagination children per chain
    text_signals:                # anchor text that marks a "next page" link (fallback)
      - next
      - "»"
      - "›"
      - more
      - last
    param_signals:                # query-param names checked first (must be a plain int)
      - page
      - pageno
      - start
      - offset
      - p

# ── Extraction ─────────────────────────────────────────────────────────────────
extraction:
  email:
    enabled: true
    regex: '[a-zA-Z0-9._%+\-]+@(?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,6}'
    valid_suffixes:
      - .gov.in
      - .nic.in
      - .res.in
      - .ac.in
      - .com
    context_chars: 200        # snippet length around each email (chars each side)
    obfuscation:
      - ['\s*\[at\]\s*', '@']
      - ['\s*\(at\)\s*', '@']
      - ['\s*\[dot\]\s*', '.']
      - ['\s*\(dot\)\s*', '.']
      - ['\s*\[hyphen\]\s*', '-']
      - ['\s*\(hyphen\)\s*', '-']
    # Each pair: [regex_pattern, replacement]. Applied before email scanning.

  max_input_chars: 200000
  # Hard cap on HTML characters scanned per page — protects against pathological
  # single-page bundles (e.g. SPA payloads) blowing up extraction time.

  role_local_parts:
    - webmaster
    - info
    - admin
    - contact
    - support
    - helpdesk
    - grievance
  # Email local-parts (the part before @) treated as role accounts rather than a
  # named person — affects name/designation proximity matching, not extraction itself.

  confidence:
    high_rungs:
      - mailto_tel
      - microdata
    mid_rungs:
      - table_block
      - proximity_text
  # Provenance tiers stamped on each lead as `confidence_band` (HIGH/MID). Feeds the
  # lead-scoring email weight (see "lead_score" below and database-schema.md).

  person:
    enabled: true
    title_prefixes:
      - Shri
      - Smt
      - Dr
      - Mr
      - Mrs
      - Ms
      - Prof
      - Sh
      - Shrimati
      - Km
    # Pattern: <prefix> <Capitalized Words> (1–4 words) near the email

    designation_keywords:
      - Secretary
      - Director
      - Commissioner
      - Collector
      - Superintendent
      - Inspector
      - Officer
      - Manager
      - Chairman
      - President
      - Minister
      - Deputy
      - Additional
      - Principal
      - Chief
      - Joint
      - Under Secretary
      - IAS
      - IPS
      - IFS
      - IRS
      - Jt
      - Jr
      - Junior
    # First matching keyword + next 60 chars used as designation

    proximity_chars: 300
    # Window (chars on each side of the email) searched for name + designation

# ── Lead Scoring ───────────────────────────────────────────────────────────────
lead_score:
  weights:
    email_high: 20   # email confidence_band == HIGH (mailto/tel, microdata)
    email_low: 10    # any other email provenance (table/proximity-text scrape)
    person_name: 40
    designation: 30
    phone: 10
  # Points summed into leads.lead_score (0-100 max). Manual (CSV-imported) leads
  # always score 0 regardless of these weights — see database-schema.md. Changing
  # these weights and restarting the server recomputes every lead's score in place
  # (Database._recompute_lead_scores(), run from _ensure_columns() on every startup).
```

---

## Key Decisions and Trade-offs

### `workers`

Higher values increase throughput but also server load on crawled sites. The shipped default (10) is conservative for
a local machine; raise to 30–50 on a faster connection if you're not worried about rate-sensitive targets.

### `max_depth`

Depth 0 = only the seed URL (fastest, but may miss contact pages). The shipped default (4) reaches most `/contact`,
`/about`, `/staff` pages a few hops from the home page, including through one pagination chain. Deeper crawls grow
exponentially in URL count.

### `max_custom_urls`

Caps how many ad-hoc URLs a single crawl job may seed with via `POST /api/jobs {custom_urls: [...]}`, as an
alternative to selecting known `domain_ids`. Custom-URL jobs bypass `target_suffixes` entirely — see
[crawler.md](crawler.md#job-seeding-domains-vs-custom-urls).

### `pagination`

Governs the pagination-aware crawling feature (`crawler.md`). `param_signals` are checked first and must resolve to a
plain integer or the link is rejected outright (anti session-URL-trap); `text_signals` are only consulted as a
fallback when no `param_signals` match. Disable with `enabled: false` to fall back to treating pager links like any
other link (subject to `max_links_per_page` and `priority_keywords` as usual).

### `recrawl_days`

Set to 0 to always re-crawl everything (useful for development). A higher value is more conservative but prevents
re-extracting the same leads.

### `httpx_first` + `playwright_fallback`

- `httpx_first: true, playwright_fallback: false` — Fastest; skips all JS sites silently.
- `httpx_first: true, playwright_fallback: true` — Recommended for production; handles JS after plain HTML fails.
- `httpx_first: false` — Not recommended; launches a browser page for every URL.

### `max_links_per_page`

Tighter limits reduce crawl scope and duration. Seed pages (depth 0) are typically the home page with many nav links,
hence a higher limit of 100. By depth 2, you're usually on specific subpages with fewer relevant links.

### `valid_suffixes` (extraction)

Extend this list if you want to capture emails from `.edu.in`, `.ac.in`, `.res.in`, or other government-adjacent domains
that are cross-linked from `.gov.in` pages. `.com` is included by default to catch officials who list a personal/Gmail
address alongside their official one.

### `lead_score.weights`

Points summed into each lead's 0–100 `lead_score` (see [database-schema.md](database-schema.md#leads)). Manual
(CSV-imported) leads are always scored 0 regardless of these weights — the score exists to help you prioritize
crawled leads, not to grade manually-entered contacts. Editing this section and restarting the server recomputes
every existing lead's score in place.

---

## Editing via the Settings UI

The Settings page (`/settings`) renders all the above fields in an editable form. Multiline fields (keyword lists,
extensions) are newline-separated text areas. On save, `POST /api/config` (`portal/api/config.py`) writes the new
`config.yaml` and updates the in-memory config dict shared via `portal/api/deps.py`. The browser reloads the form to
confirm the saved values.

---

## PostgreSQL Setup

1. Install the driver:
   ```bash
   pip install psycopg2-binary
   ```

2. Create the database and user in PostgreSQL.

3. Update `portal/config.yaml`:
   ```yaml
   database:
     uri: postgresql://govcrawler_user:password@localhost:5432/govcrawler
   ```

4. Start the server. **No manual migration step is needed** — `Database.__init__`
   (`portal/db/database.py`) calls `run_migrations()` (`portal/db/migrations.py`)
   automatically on every startup, which stamps a pre-Alembic database at `head`
   (first run only) and then runs `alembic upgrade head` unconditionally. You can
   still run `alembic upgrade head` manually from the project root if you want to
   migrate a database file without starting the app.

SQLAlchemy will use PostgreSQL instead of SQLite transparently. The WAL pragmas applied for SQLite are no-ops on
PostgreSQL.
