// settings.js

let editTemplateId = null;

function switchTab(tabId) {
    document.querySelectorAll('.settings-nav li').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.settings-tab').forEach(el => el.style.display = 'none');
    
    document.getElementById(`nav-${tabId}`).classList.add('active');
    document.getElementById(`tab-${tabId}`).style.display = 'block';
}

// ── Credentials ──────────────────────────────────────────────────────────────

async function loadCredentials() {
    try {
        const res = await fetch('/api/credentials');
        const creds = await res.json();
        
        const tbody = document.getElementById('credentials-tbody');
        tbody.innerHTML = '';
        
        if (creds.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No credentials configured.</td></tr>`;
            return;
        }
        
        creds.forEach(c => {
            const status = c.is_active ? '<span style="color:var(--success)">Active</span>' : '<span style="color:var(--danger)">Disabled</span>';
            let cooldown = '—';
            if (c.cooldown_until) {
                const cdDate = new Date(c.cooldown_until + 'Z');
                if (cdDate > new Date()) {
                    cooldown = `<span style="color:var(--warning)">Cooldown til ${cdDate.toLocaleTimeString()}</span>`;
                }
            }
            
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${c.host}</td>
                <td>${c.port}</td>
                <td>${c.username}</td>
                <td>${status}</td>
                <td>${cooldown}</td>
                <td>
                    <button class="btn-secondary btn-sm" onclick="testCredential(${c.id}, this)">Test</button>
                    <button class="btn-secondary btn-sm" onclick="deleteCredential(${c.id})" style="color:var(--danger)">Del</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to load credentials", e);
    }
}

function openCredentialModal() {
    document.getElementById('cred-host').value = '';
    document.getElementById('cred-port').value = '';
    document.getElementById('cred-user').value = '';
    document.getElementById('cred-pass').value = '';
    document.getElementById('modal-credential').style.display = 'flex';
}

function closeCredentialModal() {
    document.getElementById('modal-credential').style.display = 'none';
}

async function saveCredential() {
    const payload = {
        host: document.getElementById('cred-host').value,
        port: parseInt(document.getElementById('cred-port').value) || 0,
        username: document.getElementById('cred-user').value,
        password: document.getElementById('cred-pass').value
    };
    
    try {
        const res = await fetch('/api/credentials', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            closeCredentialModal();
            loadCredentials();
        } else {
            alert("Failed to save credential.");
        }
    } catch (e) {
        console.error(e);
        alert("Network error saving credential.");
    }
}

async function deleteCredential(id) {
    if (!confirm("Delete this credential?")) return;
    await fetch(`/api/credentials/${id}`, { method: 'DELETE' });
    loadCredentials();
}

async function testCredential(id, btn) {
    const origText = btn.textContent;
    btn.textContent = "Testing...";
    btn.disabled = true;
    
    try {
        const res = await fetch(`/api/credentials/${id}/test`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            alert("✅ Connection successful!");
            loadCredentials();
        } else {
            alert("❌ Connection failed:\n" + data.error);
        }
    } catch (e) {
        alert("❌ Error calling test endpoint.");
    } finally {
        btn.textContent = origText;
        btn.disabled = false;
    }
}

// ── Templates ────────────────────────────────────────────────────────────────

async function loadTemplates() {
    try {
        const res = await fetch('/api/templates');
        const templates = await res.json();
        
        const tbody = document.getElementById('templates-tbody');
        tbody.innerHTML = '';
        
        if (templates.length === 0) {
            tbody.innerHTML = `<tr><td colspan="3" class="empty-state">No templates created.</td></tr>`;
            return;
        }
        
        templates.forEach(t => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${t.name}</strong></td>
                <td>${t.subject}</td>
                <td>
                    <button class="btn-secondary btn-sm" onclick="editTemplate(${t.id})">Edit</button>
                    <button class="btn-secondary btn-sm" onclick="deleteTemplate(${t.id})" style="color:var(--danger)">Del</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to load templates", e);
    }
}

function openTemplateModal() {
    editTemplateId = null;
    document.getElementById('tpl-modal-title').textContent = "Create Template";
    document.getElementById('tpl-error').style.display = 'none';
    document.getElementById('tpl-name').value = '';
    document.getElementById('tpl-subject').value = '';
    document.getElementById('tpl-body').value = '';
    document.getElementById('modal-template').style.display = 'flex';
}

function closeTemplateModal() {
    document.getElementById('modal-template').style.display = 'none';
}

async function editTemplate(id) {
    try {
        const res = await fetch(`/api/templates/${id}`);
        const tpl = await res.json();
        editTemplateId = id;
        document.getElementById('tpl-modal-title').textContent = "Edit Template";
        document.getElementById('tpl-error').style.display = 'none';
        document.getElementById('tpl-name').value = tpl.name;
        document.getElementById('tpl-subject').value = tpl.subject;
        document.getElementById('tpl-body').value = tpl.raw_body;
        document.getElementById('modal-template').style.display = 'flex';
    } catch (e) {
        console.error(e);
    }
}

async function saveTemplate() {
    const payload = {
        name: document.getElementById('tpl-name').value,
        subject: document.getElementById('tpl-subject').value,
        raw_body: document.getElementById('tpl-body').value
    };
    
    const method = editTemplateId ? 'PUT' : 'POST';
    const url = editTemplateId ? `/api/templates/${editTemplateId}` : '/api/templates';
    
    try {
        const res = await fetch(url, {
            method: method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            closeTemplateModal();
            loadTemplates();
        } else {
            const err = await res.json();
            const errDiv = document.getElementById('tpl-error');
            errDiv.textContent = err.detail || "Validation error";
            errDiv.style.display = 'block';
        }
    } catch (e) {
        console.error(e);
    }
}

async function deleteTemplate(id) {
    if (!confirm("Delete this template?")) return;
    await fetch(`/api/templates/${id}`, { method: 'DELETE' });
    loadTemplates();
}

// ── Blacklist ────────────────────────────────────────────────────────────────

async function loadBlacklist() {
    try {
        const res = await fetch('/api/blacklist');
        const data = await res.json();
        
        const tbody = document.getElementById('blacklist-tbody');
        tbody.innerHTML = '';
        
        if (data.entries.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" class="empty-state">Blacklist is empty.</td></tr>`;
            return;
        }
        
        data.entries.forEach(b => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${b.email}</td>
                <td>${b.domain}</td>
                <td>${b.reason || '—'}</td>
                <td>
                    <button class="btn-secondary btn-sm" onclick="removeBlacklist(${b.id})" style="color:var(--danger)">Unblock</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to load blacklist", e);
    }
}

function openBlacklistModal() {
    document.getElementById('blk-error').style.display = 'none';
    document.getElementById('blk-email').value = '';
    document.getElementById('blk-reason').value = '';
    document.getElementById('modal-blacklist').style.display = 'flex';
}

function closeBlacklistModal() {
    document.getElementById('modal-blacklist').style.display = 'none';
}

async function saveBlacklist() {
    const payload = {
        email: document.getElementById('blk-email').value,
        reason: document.getElementById('blk-reason').value || null
    };
    
    try {
        const res = await fetch('/api/blacklist', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            closeBlacklistModal();
            loadBlacklist();
        } else {
            const err = await res.json();
            const errDiv = document.getElementById('blk-error');
            errDiv.textContent = err.detail || "Error blocking email";
            errDiv.style.display = 'block';
        }
    } catch (e) {
        console.error(e);
    }
}

async function removeBlacklist(id) {
    if (!confirm("Unblock this email?")) return;
    await fetch(`/api/blacklist/${id}`, { method: 'DELETE' });
    loadBlacklist();
}
