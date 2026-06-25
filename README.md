# GovCrawler

## Overview
GovCrawler is a comprehensive application for discovering, scraping, and crawling Indian Government domains (`.gov.in`, `.nic.in`). It integrates a direct API scraper (`GovScraper`) for initial discovery and a powerful Playwright & HTTPX-based crawler engine for deep extraction (e.g., finding contact details, personnel, emails). It features a FastAPI portal for managing jobs and data via SQLite/PostgreSQL.

## Key Features
- **Centralized Portal:** A FastAPI web application (`portal/`) managing the database, APIs, and UI.
- **Domain Discovery:** The `GovScraper` module extracts domains directly from the `india.gov.in` Web Directory API without the need for CAPTCHA solving.
- **Deep Crawler Engine:** Asynchronous crawling using Playwright and HTTPX for executing JavaScript and robust scraping.
- **Data Extraction:** Configurable regex and rule-based extraction for emails, phone numbers, and key personnel designations.
- **Scalable Database:** Uses SQLAlchemy for ORM data management (SQLite by default, easily configurable to PostgreSQL).

## Project Structure
- `/portal/`: Core application (FastAPI server, DB models, API routers, frontend).
  - `/api/`: REST endpoints.
  - `/crawler/`: The heavy-duty async crawling engine.
  - `/db/`: Database configuration and SQLAlchemy models.
  - `/scraper/`: Handlers for integrating `GovScraper`.
  - `/data/`: Default path for local SQLite database and logs.
- `/GovScraper/`: Standalone scraper module for seeding the database with domains.
- `run.py`: Main entry point to start the server or trigger manual commands.
- `requirements.txt`: Python dependencies.

## Prerequisites
- Python 3.10+
- Playwright browsers (installed via `playwright install`)

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd GovCrawler
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright browsers:**
   ```bash
   playwright install chromium
   ```

## Usage

The application can be run via the new Graphical User Interface (GUI) or through the CLI directly.

### Graphical User Interface (GUI)
Run the launcher:
```bash
python run.py
```
This opens the **GovCrawler Control Panel** (`CrawlerLauncher`), which provides an easy way to:
1. Download Playwright Browsers (for the first time)
2. Start the Server
3. Open the Web Interface in your default browser
4. Stop the Server safely

### CLI Usage
- **Start the Server directly (bypassing GUI):**
  ```bash
  python -m portal
  ```
  The server starts at `http://127.0.0.1:8000` by default.

- **Import Domains (API Extraction):**
  Seeds the database with root domains via `GovScraper`.
  ```bash
  python -m portal import
  ```
  *Alternatively, import from JSON: `python -m portal import-json gov_domains.json`*

- **Run a Crawl Job:**
  ```bash
  python -m portal crawl <job_id>
  ```

## Building an Executable

You can build a standalone executable for GovCrawler using PyInstaller and the provided `GovCrawler.spec` file. 

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Build the project:
   ```bash
   pyinstaller GovCrawler.spec
   ```
The compiled executable will be located in the `dist/GovCrawler` directory.

## Configuration
The project uses `portal/default_config.yaml` out of the box. 
To override default settings (database URI, crawler workers, extraction rules), the application will look for `portal/config.yaml`. Copy the default config and make your changes there.

---

## 🤝 Team Workflow & Collaboration Guidelines

As this is a highly collaborative project, all team members are expected to strictly follow these guidelines to ensure code quality, readability, and stability.

### 1. Branching Strategy
We use a feature-branch workflow.
- `main`: Stable branch. Always production-ready.
- `develop`: Integration branch for features before they go to `main`.
- **Feature branches:** Created from `develop` using the naming convention `feature/<issue-number>-<brief-desc>` (e.g., `feature/42-fix-playwright-timeout`).
- **Bugfix branches:** `bugfix/<issue-number>-<brief-desc>`.

### 2. Development Workflow
1. **Pull the latest `develop` branch** before starting new work.
2. **Create a feature branch.**
3. **Commit often** using clear, descriptive commit messages.
   - *Good:* `feat(crawler): add regex for phone number extraction`
   - *Bad:* `fixed stuff` or `update`
4. **Push your branch** and open a Pull Request (PR) against `develop`.

### 3. Code Review & Pull Requests
- All PRs must be reviewed by at least **one other team member** before merging.
- Ensure all tests pass and code is locally tested before requesting a review.
- Provide a clear PR description: What does it fix? How was it tested? Any side effects?
- Do not merge your own PRs without approval.

### 4. Coding Standards
- **Python Style:** Follow [PEP 8](https://peps.python.org/pep-0008/). We recommend using `black` for formatting and `flake8` or `ruff` for linting.
- **Type Hinting:** Use standard Python type hints across all functions, arguments, and classes. This makes the codebase significantly easier to understand.
- **Docstrings:** Write descriptive docstrings for all modules, classes, and complex functions. Explain *why* something is done, not just *what*.
- **Logging:** **Do not use `print()`.** Always use the configured `logging` module (e.g., `log.info()`, `log.error()`, `log.debug()`). This is critical for debugging server environments.

### 5. Database Migrations
Changes to the database schema (in `portal/db/models.py`) must be communicated to the team. Ensure that backwards compatibility is considered or provide appropriate migration scripts, especially if moving from SQLite to PostgreSQL.

### 6. Async Patterns
The crawler engine heavily utilizes `asyncio` and `playwright`. 
- Be highly mindful of blocking operations inside `async` functions. 
- Use `asyncio.to_thread()` or thread pools for heavy CPU-bound tasks or synchronous I/O to avoid freezing the event loop.
- Gracefully handle network timeouts and exceptions so that a single page crash doesn't halt the entire crawler.
