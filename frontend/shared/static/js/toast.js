// Dismissible toast/banner component — replaces raw alert()/confirm()-for-
// errors across every page. See frontend/shared/static/js/http.js for the
// ApiError/friendlyMessage() this pairs with.

function _toastStack() {
    let el = document.getElementById('toast-stack');
    if (!el) {
        el = document.createElement('div');
        el.id = 'toast-stack';
        document.body.appendChild(el);
    }
    return el;
}

function showToast(message, opts = {}) {
    const type = opts.type || 'info';
    const stack = _toastStack();
    const card = document.createElement('div');
    card.className = `toast toast-${type}`;

    const msgEl = document.createElement('span');
    msgEl.className = 'toast-message';
    msgEl.textContent = message;

    const closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.setAttribute('aria-label', 'Dismiss');
    closeBtn.textContent = '✕';
    closeBtn.onclick = () => card.remove();

    card.appendChild(msgEl);
    card.appendChild(closeBtn);
    stack.appendChild(card);

    // Errors/warnings stay until dismissed; success/info clear themselves.
    if (type === 'success' || type === 'info') {
        setTimeout(() => card.remove(), 5000);
    }
    return card;
}

// Renders friendlyMessage(err) as an error toast. A 401 is handled entirely
// by http.js's handleSessionExpired() instead (apiFetch calls it as soon as
// the response comes back, before this ever runs) — deferring to it here
// rather than also toasting+redirecting avoids showing the same "session
// expired" message twice for call sites that use both.
function showApiError(err) {
    if (typeof ApiError !== 'undefined' && err instanceof ApiError && err.status === 401) {
        if (typeof handleSessionExpired === 'function') handleSessionExpired();
        return (typeof friendlyMessage === 'function') ? friendlyMessage(err) : (err?.message || String(err));
    }
    const message = (typeof friendlyMessage === 'function') ? friendlyMessage(err) : (err?.message || String(err));
    showToast(message, {type: 'error'});
    return message;
}
