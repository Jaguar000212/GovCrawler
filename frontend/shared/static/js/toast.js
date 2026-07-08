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

// Renders friendlyMessage(err) as an error toast, and — for an expired
// session (401) — follows up with a redirect to /login so the operator
// isn't left staring at a page that can no longer do anything.
function showApiError(err) {
    const message = (typeof friendlyMessage === 'function') ? friendlyMessage(err) : (err?.message || String(err));
    showToast(message, {type: 'error'});
    if (typeof ApiError !== 'undefined' && err instanceof ApiError && err.status === 401) {
        setTimeout(() => {
            window.location.href = '/login';
        }, 1200);
    }
    return message;
}
