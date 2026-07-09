"""
Tkinter control panel for GovCrawler: state machine, live activity polling,
safe shutdown, and the sv-ttk UI.
"""

import httpx
import keyring
import logging
import os
import subprocess
import sv_ttk
import sys
import threading
import time
import tkinter as tk
import uvicorn
import webbrowser
from enum import Enum, auto
from tkinter import messagebox, simpledialog, ttk

from portal.paths import BROWSER_PATH, DATA_DIR, ICON_PATH
from .notifications import notify
from .tray import TrayController
from .. import identity, localdb

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "govcrawler"
_KEYRING_LAST_EMAIL_KEY = "_last_email"


def browsers_installed() -> bool:
    """Heuristic check: has Playwright already installed a chromium build under
    BROWSER_PATH? Checks for the chromium-<rev> folder rather than a specific
    executable name, since that differs per OS (chrome.exe / chrome / Chromium.app)."""
    return BROWSER_PATH.exists() and any(BROWSER_PATH.glob("chromium-*"))


class AppState(Enum):
    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    CHECKING = auto()  # briefly asking the server "is anything active?"
    CANCELLING = auto()  # cancel-all issued, waiting for it to take effect
    DRAINING = auto()  # waiting for active jobs to actually stop
    STOPPING = auto()  # uvicorn graceful shutdown in progress


STATE_LABELS = {
    AppState.IDLE: ("Idle", "#808080"),
    AppState.STARTING: ("Starting…", "#4fa8d8"),
    AppState.RUNNING: ("Running", "#4caf50"),
    AppState.CHECKING: ("Checking active jobs…", "#4fa8d8"),
    AppState.CANCELLING: ("Cancelling active work…", "#e0a030"),
    AppState.DRAINING: ("Stopping active work…", "#e0a030"),
    AppState.STOPPING: ("Stopping server…", "#e0a030"),
}

DRAIN_TIMEOUT_SECONDS = 180
POLL_INTERVAL_MS = 1500
CLOUD_HEALTH_POLL_MS = 15000


class LoginDialog(simpledialog.Dialog):
    """Blocking modal collecting email/password. `self.result` is
    (email, password) on OK, None on cancel."""

    def __init__(self, parent, initial_email: str = ""):
        self._initial_email = initial_email
        self.result = None
        super().__init__(parent, title="Sign in — GovCrawler")

    def body(self, master):
        ttk.Label(master, text="Email").grid(row=0, column=0, sticky="w", pady=(4, 2))
        self.email_var = tk.StringVar(value=self._initial_email)
        self.email_entry = ttk.Entry(master, textvariable=self.email_var, width=32)
        self.email_entry.grid(row=1, column=0, pady=(0, 8))

        ttk.Label(master, text="Password").grid(row=2, column=0, sticky="w", pady=(4, 2))
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(master, textvariable=self.password_var, show="*", width=32)
        self.password_entry.grid(row=3, column=0, pady=(0, 8))

        return self.password_entry if self._initial_email else self.email_entry

    def apply(self):
        self.result = (self.email_var.get().strip(), self.password_var.get())


class CrawlerLauncher:
    def __init__(self, root, config: dict, entry_script: str):
        self.root = root
        self.config = config
        self.entry_script = entry_script  # path re-invoked as a subprocess for INSTALL_BROWSERS

        self.root.title("GovCrawler Control Panel")
        self.root.geometry("440x560")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        sv_ttk.set_theme("dark")
        try:
            if ICON_PATH.exists():
                self.root.iconbitmap(default=str(ICON_PATH))
        except Exception as e:
            log.warning(f"Could not set window icon: {e}")

        self.state = AppState.IDLE
        self._browsers_ok = browsers_installed()
        self.server_thread: threading.Thread | None = None
        self.uvicorn_server: uvicorn.Server | None = None
        self.http: httpx.Client | None = None
        self.tray: TrayController | None = None
        self._full_quit_requested = False
        self._drain_deadline: float | None = None
        self._prev_jobs: set[int] = set()
        self._access_token: str | None = None
        self._login_email: str | None = None

        localdb.init(DATA_DIR / "agent_local.db")
        self._ensure_cloud_url_configured()

        self._build_ui()
        self._render_state()
        self._schedule_cloud_health_check()

    def _ensure_cloud_url_configured(self):
        """First-run only: plan.md §19.1 Phase 9 Part 2, 2.1 — the agent must
        be told which VPS to talk to before it can do anything real. Kept
        deliberately simple (a blocking prompt, no validation beyond
        non-empty) since this is a one-time-per-machine setup step, not a
        recurring flow."""
        if localdb.has_setting("cloud_api_base_url"):
            return
        url = simpledialog.askstring(
            "Cloud Server URL",
            "Enter your GovCrawler cloud server's base URL\n(e.g. https://govcrawler.example.com):",
            parent=self.root,
        )
        url = (url or "").strip().rstrip("/")
        if not url:
            messagebox.showerror("Setup required", "A cloud server URL is required to use GovCrawler.")
            sys.exit(1)
        localdb.set_setting("cloud_api_base_url", url)

    def _cloud_base_url(self) -> str:
        return localdb.get_setting("cloud_api_base_url")

    # --- Cloud URL: view/change + connection status --------------------------

    def change_cloud_url(self):
        """Only allowed while IDLE — changing the cloud mid-session would
        invalidate the current login/identity and any in-flight job's cloud
        target, per agent/identity.py's single cached session."""
        current = self._cloud_base_url()
        url = simpledialog.askstring(
            "Cloud Server URL",
            "Enter your GovCrawler cloud server's base URL\n(e.g. https://govcrawler.example.com):",
            initialvalue=current,
            parent=self.root,
        )
        url = (url or "").strip().rstrip("/")
        if not url or url == current:
            return
        localdb.set_setting("cloud_api_base_url", url)
        self.cloud_url_lbl.config(text=url)
        self._check_cloud_health_now()

    def _schedule_cloud_health_check(self):
        self._check_cloud_health_now()
        self.root.after(CLOUD_HEALTH_POLL_MS, self._schedule_cloud_health_check)

    def _check_cloud_health_now(self):
        url = self._cloud_base_url()
        threading.Thread(target=self._check_cloud_health_task, args=(url,), daemon=True).start()

    def _check_cloud_health_task(self, url: str):
        try:
            resp = httpx.get(f"{url}/healthz", timeout=5)
            ok = resp.status_code == 200
        except Exception:
            ok = False
        self.root.after(0, self._on_cloud_health_result, url, ok)

    def _on_cloud_health_result(self, url: str, ok: bool):
        if url != self._cloud_base_url():
            return  # stale result from before a URL change
        if ok:
            self.cloud_status_dot.config(foreground="#4caf50")
            self.cloud_status_lbl.config(text="Reachable", foreground="#4caf50")
        else:
            self.cloud_status_dot.config(foreground="#e05252")
            self.cloud_status_lbl.config(text="Unreachable — check the URL or your connection", foreground="#e05252")

    # --- UI construction ------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 16, "pady": 8}

        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=16, pady=(16, 4))
        ttk.Label(header, text="GovCrawler", font=("Segoe UI", 16, "bold")).pack(side="left")
        status_frame = ttk.Frame(header)
        status_frame.pack(side="right")
        self.status_dot = ttk.Label(status_frame, text="●", foreground="#808080", font=("Segoe UI", 12))
        self.status_dot.pack(side="left", padx=(0, 4))
        self.status_text = ttk.Label(status_frame, text="Idle")
        self.status_text.pack(side="left")

        cloud_frame = ttk.LabelFrame(self.root, text="Cloud Server")
        cloud_frame.pack(fill="x", **pad)
        cloud_url_row = ttk.Frame(cloud_frame)
        cloud_url_row.pack(fill="x", padx=10, pady=(8, 4))
        self.cloud_status_dot = ttk.Label(cloud_url_row, text="●", foreground="#808080", font=("Segoe UI", 10))
        self.cloud_status_dot.pack(side="left", padx=(0, 6))
        self.cloud_url_lbl = ttk.Label(cloud_url_row, text=self._cloud_base_url(), wraplength=280)
        self.cloud_url_lbl.pack(side="left", fill="x", expand=True)
        self.cloud_status_lbl = ttk.Label(cloud_frame, text="Checking…", foreground="#808080")
        self.cloud_status_lbl.pack(anchor="w", padx=10, pady=(0, 4))
        self.btn_change_cloud_url = ttk.Button(cloud_frame, text="Change…", command=self.change_cloud_url)
        self.btn_change_cloud_url.pack(anchor="w", padx=10, pady=(0, 2))
        ttk.Label(
            cloud_frame, text="Stop the server to change the cloud URL.", foreground="#808080", font=("Segoe UI", 8)
        ).pack(anchor="w", padx=10, pady=(0, 10))

        pw_frame = ttk.LabelFrame(self.root, text="Playwright Browsers")
        pw_frame.pack(fill="x", **pad)
        self.pw_status_lbl = ttk.Label(pw_frame, text="")
        self.pw_status_lbl.pack(anchor="w", padx=10, pady=(8, 4))
        self.btn_download = ttk.Button(pw_frame, text="Download Browsers", command=self.trigger_download)
        self.btn_download.pack(anchor="w", padx=10, pady=(0, 10))

        server_frame = ttk.LabelFrame(self.root, text="Server")
        server_frame.pack(fill="x", **pad)
        btn_row = ttk.Frame(server_frame)
        btn_row.pack(fill="x", padx=10, pady=10)
        self.btn_toggle = ttk.Button(
            btn_row, text="Start Server", command=self.on_toggle_server, style="Accent.TButton"
        )
        self.btn_toggle.pack(side="left")
        self.btn_browser = ttk.Button(btn_row, text="Open Web Interface", command=self.open_browser, state=tk.DISABLED)
        self.btn_browser.pack(side="left", padx=(8, 0))

        activity_frame = ttk.LabelFrame(self.root, text="Activity")
        activity_frame.pack(fill="x", **pad)
        self.activity_lbl = ttk.Label(activity_frame, text="Server not running")
        self.activity_lbl.pack(anchor="w", padx=10, pady=8)
        self.status_detail_lbl = ttk.Label(activity_frame, text="", wraplength=380, foreground="#e0a030")
        self.status_detail_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        ttk.Label(
            self.root,
            text="Closing this window minimizes to the tray. Use Stop Server to fully quit.",
            foreground="#808080",
            wraplength=400,
            justify="left",
        ).pack(fill="x", padx=16, pady=(8, 16))

    def _render_state(self):
        text, color = STATE_LABELS[self.state]
        self.status_text.config(text=text)
        self.status_dot.config(foreground=color)

        self.btn_change_cloud_url.config(state=tk.NORMAL if self.state == AppState.IDLE else tk.DISABLED)

        if self._browsers_ok:
            self.pw_status_lbl.config(text="Browsers installed", foreground="#4caf50")
            self.btn_download.config(text="Re-download")
        else:
            self.pw_status_lbl.config(text="Required before starting the server", foreground="#e0a030")
            self.btn_download.config(text="Download Browsers (~600MB)")
        self.btn_download.config(state=tk.NORMAL if self.state == AppState.IDLE else tk.DISABLED)

        can_toggle = self.state in (AppState.IDLE, AppState.RUNNING) and (
                self.state != AppState.IDLE or self._browsers_ok
        )
        self.btn_toggle.config(
            text="Start Server" if self.state == AppState.IDLE else "Stop Server",
            state=tk.NORMAL if can_toggle else tk.DISABLED,
        )

        browsable_states = (AppState.RUNNING, AppState.CHECKING, AppState.CANCELLING, AppState.DRAINING)
        self.btn_browser.config(state=tk.NORMAL if self.state in browsable_states else tk.DISABLED)

        if self.state == AppState.IDLE:
            self.activity_lbl.config(text="Server not running")
            self.status_detail_lbl.config(text="")
        elif self.state == AppState.STARTING:
            self.activity_lbl.config(text="Starting…")
            self.status_detail_lbl.config(text="")

    # --- Notifications ----------------------------------------------------

    def _toast(self, title: str, msg: str):
        notify(title, msg, ICON_PATH)

    # --- HTTP helper --------------------------------------------------------

    def _base_url(self) -> str:
        host = self.config["api"]["host"]
        display_host = "127.0.0.1" if host == "0.0.0.0" else host
        return f"http://{display_host}:{self.config['api']['port']}"

    def _auth_headers(self) -> dict:
        # Authorization is unused by the local BFF's own routes (they check
        # the session cookie, not this header) but IS needed by proxied
        # cloud calls. X-CSRF-Token mirrors what frontend/shared/static/js/
        # http.js's fetch-patch does for the browser — self.http never runs
        # that JS, so without this every mutating local-BFF call (e.g.
        # cancel-all) would 403 on verify_local_csrf despite already
        # carrying the csrf cookie.
        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        if self.http is not None:
            csrf = self.http.cookies.get("csrf")
            if csrf:
                headers["X-CSRF-Token"] = csrf
        return headers

    def _api_async(self, method: str, path: str, on_done, **kwargs):
        extra_headers = kwargs.pop("headers", {})

        def task():
            try:
                resp = self.http.request(
                    method, path, timeout=5, headers={**self._auth_headers(), **extra_headers}, **kwargs
                )
                if resp.status_code == 401 and self._try_refresh_sync():
                    resp = self.http.request(
                        method, path, timeout=5, headers={**self._auth_headers(), **extra_headers}, **kwargs
                    )
                resp.raise_for_status()
                data = resp.json()
                self.root.after(0, on_done, data, None)
            except Exception as e:
                self.root.after(0, on_done, None, e)

        threading.Thread(target=task, daemon=True).start()

    def _try_refresh_sync(self) -> bool:
        """Best-effort access-token refresh using the keyring-stored refresh
        token. Runs synchronously on the calling (background) thread — called
        only from inside an _api_async task, never the Tk main thread."""
        if not self._login_email:
            return False
        refresh_token = keyring.get_password(_KEYRING_SERVICE, self._login_email)
        if not refresh_token:
            return False
        try:
            resp = httpx.post(
                f"{self._cloud_base_url()}/auth/refresh", json={"refresh_token": refresh_token}, timeout=5
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            keyring.set_password(_KEYRING_SERVICE, self._login_email, data["refresh_token"])
            identity.update_access_token(
                data["access_token"], permissions=data["user"]["permissions"], is_admin=data["user"]["is_admin"]
            )
            return True
        except Exception as e:
            log.warning(f"Token refresh failed: {e}")
            return False

    # --- ACTION: Download Browsers ------------------------------------------

    def trigger_download(self):
        self.btn_download.config(state=tk.DISABLED)
        self.status_detail_lbl.config(
            text="Downloading Playwright browsers (~600MB)… please wait.", foreground="#4fa8d8"
        )
        threading.Thread(target=self._download_browsers_task, daemon=True).start()

    def _download_browsers_task(self):
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "INSTALL_BROWSERS"]
            else:
                cmd = [sys.executable, self.entry_script, "INSTALL_BROWSERS"]

            BROWSER_PATH.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_PATH)

            subprocess.run(cmd, env=env, check=True)
            self.root.after(0, self._on_download_done, True, None)
        except subprocess.CalledProcessError as e:
            self.root.after(0, self._on_download_done, False, f"Download failed. Check your firewall.\n{e}")
        except Exception as e:
            self.root.after(0, self._on_download_done, False, f"Unexpected error:\n{e}")

    def _on_download_done(self, ok: bool, error: str | None):
        self._browsers_ok = browsers_installed()
        self._render_state()
        if ok:
            self.status_detail_lbl.config(text="Browsers downloaded successfully.", foreground="#4caf50")
            self._toast("GovCrawler", "Playwright browsers downloaded.")
        else:
            self.status_detail_lbl.config(text="")
            messagebox.showerror("Download failed", error)
            self._toast("GovCrawler", "Browser download failed.")

    # --- ACTION: Start Server ------------------------------------------------

    def on_toggle_server(self):
        if self.state == AppState.IDLE:
            self.trigger_start_server()
        elif self.state == AppState.RUNNING:
            self._request_quit(full_quit=False)

    def trigger_start_server(self):
        if not self._browsers_ok:
            messagebox.showwarning(
                "Playwright required", "Download the Playwright browsers before starting the server."
            )
            return

        self.state = AppState.STARTING
        self._render_state()

        self.http = httpx.Client(base_url=self._base_url())
        self._prev_jobs = set()

        if self.tray is None:
            self._setup_tray()

        self.server_thread = threading.Thread(target=self._run_server_task, daemon=True)
        self.server_thread.start()
        self._wait_for_server_ready(attempts=15)

    def _run_server_task(self):
        from ..bff.app import create_app

        try:
            app = create_app(self.config)

            u_config = uvicorn.Config(
                app=app,
                host=self.config["api"]["host"],
                port=self.config["api"]["port"],
                log_level="info",
            )
            self.uvicorn_server = uvicorn.Server(u_config)
            self.uvicorn_server.run()
        except Exception as e:
            log.error(f"Server crashed: {e}", exc_info=True)
            self.root.after(0, self._on_server_crashed, str(e))

    def _on_server_crashed(self, error: str):
        self.uvicorn_server = None
        self.http = None
        self.state = AppState.IDLE
        self._render_state()
        messagebox.showerror("Server error", f"The server stopped unexpectedly:\n{error}")
        self._toast("GovCrawler", "Server crashed unexpectedly.")

    def _wait_for_server_ready(self, attempts: int):
        def task():
            try:
                self.http.get("/ping", timeout=2)
                self.root.after(0, self._on_server_ready)
            except Exception:
                if attempts > 1:
                    self.root.after(300, lambda: self._wait_for_server_ready(attempts - 1))
                else:
                    self.root.after(0, self._on_server_ready)

        threading.Thread(target=task, daemon=True).start()

    def _on_server_ready(self):
        if self.state != AppState.STARTING:
            return
        self._prompt_login()

    # --- Login (keyring-backed) -----------------------------------------------

    def _prompt_login(self):
        remembered_email = keyring.get_password(_KEYRING_SERVICE, _KEYRING_LAST_EMAIL_KEY) or ""
        dialog = LoginDialog(self.root, initial_email=remembered_email)
        if not dialog.result or not dialog.result[0] or not dialog.result[1]:
            self._abort_start("Sign-in is required to start the server.")
            return
        email, password = dialog.result
        threading.Thread(target=self._login_task, args=(email, password), daemon=True).start()

    def _login_task(self, email: str, password: str):
        try:
            resp = httpx.post(
                f"{self._cloud_base_url()}/auth/login", json={"email": email, "password": password}, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            self.root.after(0, self._on_login_success, email, data)
        except Exception as e:
            self.root.after(0, self._on_login_failed, str(e))

    def _on_login_success(self, email: str, data: dict):
        self._access_token = data["access_token"]
        self._login_email = email
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_LAST_EMAIL_KEY, email)
        keyring.set_password(_KEYRING_SERVICE, email, data["refresh_token"])
        identity.set_session(
            email,
            data["access_token"],
            self._cloud_base_url(),
            permissions=data["user"]["permissions"],
            is_admin=data["user"]["is_admin"],
        )
        try:
            # self.http is this launcher's OWN client for polling its local BFF
            # (/api/system/activity, cancel-all) — it never went through the
            # BFF's own /auth/login (login above hits the cloud directly), so
            # without this it would never carry the local session cookie
            # require_local_session checks for, and every poll would 401.
            self.http.get("/local-bootstrap")
        except Exception as e:
            log.warning(f"Failed to seed local session cookie for launcher's own API client: {e}")

        self.state = AppState.RUNNING
        self._render_state()
        self._toast("GovCrawler", "Server started.")
        self._schedule_poll()

    def _on_login_failed(self, error: str):
        messagebox.showerror("Sign-in failed", f"Could not sign in:\n{error}")
        self._prompt_login()

    def _abort_start(self, message: str):
        messagebox.showwarning("Sign-in required", message)
        self._begin_graceful_shutdown()

    # --- Live activity polling ----------------------------------------------

    def _schedule_poll(self):
        if self.state != AppState.RUNNING:
            return
        self._api_async("GET", "/api/system/activity", self._on_activity)

    def _on_activity(self, data, err):
        if err is not None:
            log.debug(f"activity poll failed: {err}")
        else:
            self._update_activity_ui(data)
            self._check_for_completions(data)
        if self.state == AppState.RUNNING:
            self.root.after(POLL_INTERVAL_MS, self._schedule_poll)

    def _update_activity_ui(self, data: dict):
        n = data["total_active"]
        if n == 0:
            self.activity_lbl.config(text="No active jobs")
            return
        self.activity_lbl.config(text=f"Active: {len(data['crawl_jobs'])} crawl job(s)")

    def _check_for_completions(self, data: dict):
        cur_jobs = {j["id"] for j in data["crawl_jobs"]}

        for job_id in self._prev_jobs - cur_jobs:
            self._api_async("GET", f"/api/jobs/{job_id}", lambda d, e, jid=job_id: self._notify_job_done(jid, d, e))

        self._prev_jobs = cur_jobs

    def _notify_job_done(self, job_id: int, data, err):
        if err is not None or not data:
            return
        self._toast("GovCrawler", f"Crawl job #{job_id} {data['status']} — {data['leads_found']} leads found.")

    # --- ACTION: Safe shutdown ------------------------------------------------

    def _request_quit(self, full_quit: bool):
        if self.state == AppState.IDLE:
            self._hard_exit()
            return
        if self.state != AppState.RUNNING:
            return

        self._full_quit_requested = full_quit
        self.state = AppState.CHECKING
        self._render_state()
        self._api_async("GET", "/api/system/activity", self._on_activity_for_shutdown)

    def _on_activity_for_shutdown(self, data, err):
        if err is not None:
            log.warning(f"Could not check activity before shutdown: {err}")
            self._begin_graceful_shutdown()
            return

        if data["total_active"] == 0:
            self._begin_graceful_shutdown()
            return

        self.state = AppState.RUNNING
        self._render_state()

        labels = [j["label"] for j in data["crawl_jobs"]]
        preview = "\n".join(f"• {label}" for label in labels[:6])
        if len(labels) > 6:
            preview += f"\n… and {len(labels) - 6} more"

        proceed = messagebox.askyesno(
            "Active work in progress",
            f"{data['total_active']} job(s) are currently running:\n\n{preview}\n\n"
            "Stop them and shut down the server?",
        )
        if proceed:
            self._begin_cancel_and_drain()
        else:
            self._schedule_poll()

    def _begin_cancel_and_drain(self):
        self.state = AppState.CANCELLING
        self._render_state()
        self._api_async("POST", "/api/system/cancel-all", self._on_cancel_all_issued)

    def _on_cancel_all_issued(self, data, err):
        if err is not None:
            messagebox.showerror("Error", f"Failed to cancel active work:\n{err}")
            self.state = AppState.RUNNING
            self._render_state()
            self._schedule_poll()
            return

        self._drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        self.state = AppState.DRAINING
        self._render_state()
        self.status_detail_lbl.config(text="Stopping active job(s)…")
        self._poll_drain()

    def _poll_drain(self):
        self._api_async("GET", "/api/system/activity", self._on_drain_activity)

    def _on_drain_activity(self, data, err):
        if self.state != AppState.DRAINING:
            return

        if err is None and data["total_active"] == 0:
            self._begin_graceful_shutdown()
            return

        remaining = data["total_active"] if data else "an unknown number of"
        self.status_detail_lbl.config(text=f"Stopping {remaining} active job(s)… this can take up to ~90s.")

        if time.monotonic() >= self._drain_deadline:
            proceed = messagebox.askyesno(
                "Still stopping",
                "Some jobs haven't stopped yet.\n\nForce-stop the server anyway?",
            )
            if proceed:
                self._begin_graceful_shutdown()
                return
            self._drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS

        self.root.after(POLL_INTERVAL_MS, self._poll_drain)

    def _begin_graceful_shutdown(self):
        self.state = AppState.STOPPING
        self._render_state()
        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True
            self.root.after(200, self._check_shutdown_complete)
        else:
            self._on_server_stopped()

    def _check_shutdown_complete(self):
        if self.server_thread and self.server_thread.is_alive():
            self.root.after(200, self._check_shutdown_complete)
        else:
            self._on_server_stopped()

    def _on_server_stopped(self):
        self.uvicorn_server = None
        self.http = None
        self._access_token = None
        self._login_email = None
        identity.clear_session()
        self._toast("GovCrawler", "Server stopped.")
        if self._full_quit_requested:
            self._hard_exit()
        else:
            self.state = AppState.IDLE
            self._render_state()

    def _hard_exit(self):
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()
        sys.exit(0)

    # --- ACTION: Open Browser ------------------------------------------------

    def open_browser(self):
        uri = self._base_url()
        if self._access_token:
            # Hands the browser tab a local session cookie so the operator
            # isn't asked to log in a second time in-browser — the agent's
            # own /local-bootstrap, not the old cross-process token hand-off.
            uri = f"{uri}/local-bootstrap"

        if sys.platform.startswith("linux"):
            # PyInstaller overrides LD_LIBRARY_PATH with bundled libs; child processes
            # like xdg-open inherit it and /bin/sh crashes with a readline symbol error.
            # Restore the original value the bootloader saved before spawning.
            env = os.environ.copy()
            orig = env.get("LD_LIBRARY_PATH_ORIG")
            if orig is not None:
                env["LD_LIBRARY_PATH"] = orig
            else:
                env.pop("LD_LIBRARY_PATH", None)
            subprocess.Popen(["xdg-open", uri], env=env)
        else:
            webbrowser.open(uri)

    # --- Tray icon -------------------------------------------------------

    def _setup_tray(self):
        self.tray = TrayController(
            icon_path=ICON_PATH,
            on_show=lambda: self.root.after(0, self._restore_window),
            on_open_browser=lambda: self.root.after(0, self.open_browser),
            on_quit=lambda: self.root.after(0, self._request_quit, True),
            is_running=lambda: self.state == AppState.RUNNING,
        )
        self.tray.start()

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    # --- ACTION: Window Close Intercept ---------------------------------------

    def on_window_close(self):
        if self.state == AppState.IDLE:
            self._hard_exit()
        else:
            self.root.withdraw()
