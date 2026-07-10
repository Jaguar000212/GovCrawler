// Shared fetch layer — loaded by both the agent and cloud UIs, before any
// tier-specific script. See .docs/architecture.md.

// ── CSRF (double-submit cookie) ─────────────────────────────────────────────
// Single chokepoint for every fetch() call across every page script — patching
// window.fetch here covers all of them without touching each call site.
// See cloud/api/deps.py's verify_csrf for the server-side check.
(function () {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (input, init = {}) {
        const method = (init.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD') {
            const match = document.cookie.match(/(?:^|; )csrf=([^;]*)/);
            if (match) {
                init = {...init, headers: {...(init.headers || {}), 'X-CSRF-Token': decodeURIComponent(match[1])}};
            }
        }
        return nativeFetch(input, init);
    };
})();

class ApiError extends Error {
    constructor(status, code, message) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.code = code;
    }
}

// A 401 means the session is gone (expired, or the account was disabled/
// deactivated mid-session — see cloud/api/auth.py's revoke_session_family).
// Centralized here, not left to each call site's catch block: most poll
// loops across the app (dashboard filters, job/import status polling) only
// console.error() their failures, so without this a killed session just
// spins silently forever instead of sending the operator back to /login.
// Guarded so concurrent failures (a poll storm all 401-ing at once) only
// trigger one toast + one redirect.
let _sessionExpiredHandled = false;

function handleSessionExpired() {
    if (_sessionExpiredHandled) return;
    _sessionExpiredHandled = true;
    if (typeof showToast === 'function') {
        showToast('Your session has expired — please log in again.', {type: 'error'});
    }
    setTimeout(() => {
        window.location.href = '/login';
    }, 1200);
}

// Every backend error response is guaranteed a string `detail` (see
// cloud/api/server.py's RequestValidationError/Exception handlers and
// agent/bff/app.py's equivalents) — agent/bff/proxy.py additionally sets
// `code: "cloud_unreachable"` when it can't reach the configured cloud
// server at all.
async function apiFetch(url, opts = {}) {
    let r;
    try {
        r = await fetch(url, opts);
    } catch (e) {
        throw new ApiError(0, 'network_error', "Can't reach the server — check your connection.");
    }
    if (!r.ok) {
        let detail = r.statusText || `HTTP ${r.status}`;
        let code = null;
        try {
            const body = await r.json();
            if (typeof body.detail === 'string' && body.detail) detail = body.detail;
            if (typeof body.code === 'string') code = body.code;
        } catch {
            // Non-JSON error body — keep the statusText fallback.
        }
        if (r.status === 401) handleSessionExpired();
        throw new ApiError(r.status, code, detail);
    }
    if (r.status === 204) return null;
    return r.json();
}

// Escapes text for safe interpolation into innerHTML — every page's
// hand-built row/card templates rely on this.
function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Maps a thrown error (ideally an ApiError from apiFetch) to one readable
// sentence — never a raw status code or a stringified JSON array.
function friendlyMessage(err) {
    if (!(err instanceof ApiError)) {
        return (err && err.message) ? err.message : 'Something went wrong.';
    }
    if (err.code === 'network_error' || err.code === 'cloud_unreachable') {
        return err.message;
    }
    switch (err.status) {
        case 401:
            return 'Your session has expired — please log in again.';
        case 403:
            return "You don't have permission to do this.";
        case 404:
            return 'That item no longer exists — it may have already been deleted.';
        default:
            return err.message;
    }
}
