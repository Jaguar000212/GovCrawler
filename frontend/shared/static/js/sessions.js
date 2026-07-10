// Self-service "My Sessions" panel — shared by both the cloud and agent
// frontends (both load /assets/js/* from frontend/shared/static). Lists the
// logged-in user's own active refresh-token sessions and lets them revoke
// one or all-but-the-current-one. See cloud/api/auth.py's /auth/sessions*.

function openSessionsModal() {
    const overlay = document.getElementById('modal-sessions');
    if (!overlay) return;
    overlay.style.display = 'flex';
    loadSessions();
}

function closeSessionsModal() {
    const overlay = document.getElementById('modal-sessions');
    if (overlay) overlay.style.display = 'none';
}

async function loadSessions() {
    const list = document.getElementById('sessions-list');
    if (!list) return;
    list.innerHTML = '<div class="empty-state">Loading...</div>';
    let sessions;
    try {
        sessions = await apiFetch('/auth/sessions');
    } catch (e) {
        list.innerHTML = '<div class="empty-state">Failed to load sessions.</div>';
        return;
    }
    if (!sessions.length) {
        list.innerHTML = '<div class="empty-state">No active sessions.</div>';
        return;
    }
    list.innerHTML = sessions.map(sessionRowHtml).join('');
}

function sessionRowHtml(s) {
    const lastUsed = s.last_used_at ? new Date(s.last_used_at).toLocaleString() : 'Never';
    const currentBadge = s.is_current ? '<span class="badge badge-green">This device</span>' : '';
    const revokeBtn = s.is_current
        ? ''
        : `<button class="btn-danger btn-sm" onclick="revokeSession(${s.id})">Revoke</button>`;
    return `<div style="display:flex; align-items:center; justify-content:space-between; gap:12px; border-bottom:1px solid var(--border, #30363d); padding:8px 0;">
        <div>
            <div style="font-size:13px;">${esc(s.user_agent || 'Unknown device')} ${currentBadge}</div>
            <div style="font-size:11px; color:var(--muted);">${esc(s.ip || 'Unknown IP')} · Last used ${esc(lastUsed)}</div>
        </div>
        ${revokeBtn}
    </div>`;
}

async function revokeSession(sessionId) {
    try {
        await apiFetch(`/auth/sessions/${sessionId}`, {method: 'DELETE'});
        showToast('Session revoked.', {type: 'success'});
        loadSessions();
    } catch (e) {
        showApiError(e);
    }
}

async function revokeOtherSessions() {
    if (!confirm('Sign out every other session? This device stays signed in.')) return;
    try {
        const res = await apiFetch('/auth/sessions/revoke-others', {method: 'POST'});
        showToast(res.message || 'Other sessions revoked.', {type: 'success'});
        loadSessions();
    } catch (e) {
        showApiError(e);
    }
}
