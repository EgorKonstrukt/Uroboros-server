/* ===================== Backups plugin ===================== */

var bkInstances = [];
var bkRules = [];
var bkRecords = [];
var bkCurrent = null;
var bkEditingRule = null;

function bkTriggerLabel(t) {
    if (t === 'schedule') return 'Schedule';
    if (t === 'on_stop') return 'On stop';
    return 'Manual';
}

function bkFmt(dt) {
    if (!dt) return '\u2014';
    return String(dt).replace('T', ' ').slice(0, 19);
}

function bkIntervalLabel(sec) {
    sec = Number(sec) || 0;
    if (sec <= 0) return 'Manual only';
    if (sec === 3600) return 'Hourly';
    if (sec === 86400) return 'Daily';
    if (sec === 604800) return 'Weekly';
    if (sec % 86400 === 0) return 'Every ' + (sec / 86400) + ' days';
    if (sec % 3600 === 0) return 'Every ' + (sec / 3600) + ' hours';
    return 'Every ' + sec + ' s';
}

async function loadBackups() {
    if (typeof toast !== 'function') return;
    try {
        var r = await apiFetch('/admin/backups/instances');
        if (!r) return;
        bkInstances = await r.json();
        renderBackupInstances();
        if (bkInstances.length) {
            if (!bkCurrent || !bkInstances.some(function (i) { return i.id === bkCurrent; })) {
                bkCurrent = bkInstances[0].id;
                selectBackupInstance(bkCurrent);
            } else {
                loadBackupData(bkCurrent);
            }
        } else {
            document.getElementById('backupRulesList').innerHTML = '<div class="status-text" style="padding:16px 20px">No servers configured</div>';
            document.getElementById('backupRecordsBody').innerHTML = '';
        }
        var s = await apiFetch('/admin/backups/stats');
        if (s) renderBackupSummary(await s.json());
    } catch (e) {
        toast('Failed: ' + e.message, 'error');
    }
}

async function refreshBackupInstances() {
    var btn = event && event.target ? event.target : null;
    try {
        var r = await apiFetch('/admin/backups/instances');
        if (!r) return;
        bkInstances = await r.json();
        renderBackupInstances();
        if (bkCurrent && bkInstances.some(function (i) { return i.id === bkCurrent; })) {
            loadBackupData(bkCurrent);
        }
    } catch (e) {
        toast('Failed: ' + e.message, 'error');
    }
}

function renderBackupSummary(s) {
    if (!s) return;
    document.getElementById('backupsSummary').innerHTML =
        '<div class="md-card"><div class="backups-sum-val">' + s.rules + '</div><div class="backups-sum-label">Rules</div></div>' +
        '<div class="md-card"><div class="backups-sum-val">' + s.backups + '</div><div class="backups-sum-label">Backups</div></div>' +
        '<div class="md-card"><div class="backups-sum-val">' + (typeof formatSize === 'function' ? formatSize(s.total_size) : s.total_size) + '</div><div class="backups-sum-label">Total size</div></div>' +
        '<div class="md-card"><div class="backups-sum-val">' + (s.last_backup_at ? bkFmt(s.last_backup_at).slice(0, 10) : '\u2014') + '</div><div class="backups-sum-label">Last backup</div></div>';
}

function renderBackupInstances() {
    var list = document.getElementById('backupInstanceList');
    list.innerHTML = '';
    if (!bkInstances.length) {
        list.innerHTML = '<div class="status-text" style="padding:8px 10px">No servers</div>';
        return;
    }
    for (var i = 0; i < bkInstances.length; i++) {
        var inst = bkInstances[i];
        var btn = document.createElement('button');
        btn.className = 'backup-instance-item' + (inst.id === bkCurrent ? ' active' : '');
        btn.onclick = (function (id) { return function () { selectBackupInstance(id); }; })(inst.id);
        btn.innerHTML = '<span class="server-dot ' + (inst.running ? 'dot-on' : 'dot-off') + '"></span>' +
            '<span class="backup-inst-name">' + esc(inst.name) + '</span>' +
            '<span class="tag tag-file" style="font-size:10px">' + inst.backups + '</span>';
        list.appendChild(btn);
    }
}

function selectBackupInstance(id) {
    bkCurrent = id;
    renderBackupInstances();
    loadBackupData(id);
}

async function loadBackupData(instanceId) {
    try {
        var r1 = await apiFetch('/admin/backups/rules?instance_id=' + encodeURIComponent(instanceId));
        var r2 = await apiFetch('/admin/backups?instance_id=' + encodeURIComponent(instanceId));
        if (!r1 || !r2) return;
        bkRules = await r1.json();
        bkRecords = await r2.json();
        renderRules();
        renderRecords();
    } catch (e) {
        toast('Failed: ' + e.message, 'error');
    }
}

function renderRules() {
    var box = document.getElementById('backupRulesList');
    box.innerHTML = '';
    if (!bkRules.length) {
        box.innerHTML = '<div class="status-text" style="padding:8px 20px 16px">No rules. Create one to schedule backups.</div>';
        return;
    }
    for (var i = 0; i < bkRules.length; i++) {
        var r = bkRules[i];
        var div = document.createElement('div');
        div.className = 'backup-rule';
        var desc = bkIntervalLabel(r.interval_seconds);
        if (r.backup_on_stop) desc += ' \u00b7 on stop';
        if (r.retention_count > 0) desc += ' \u00b7 keep ' + r.retention_count;
        if (r.retention_days > 0) desc += ' \u00b7 ' + r.retention_days + 'd';
        var folders = (r.folders || []).join(', ');
        if (folders && folders !== '.') desc += ' \u00b7 ' + esc(folders);
        div.innerHTML =
            '<div class="backup-rule-name">' + esc(r.name || ('Rule #' + r.id)) + '<div class="backup-rule-desc">' + desc + '</div></div>' +
            (r.enabled
                ? '<button class="btn btn-secondary btn-sm" onclick="toggleRule(' + r.id + ',false)">Disable</button>'
                : '<button class="btn btn-start btn-sm" onclick="toggleRule(' + r.id + ',true)">Enable</button>') +
            '<button class="btn btn-secondary btn-sm" onclick="runRule(' + r.id + ')">Run</button>' +
            '<button class="btn btn-reload btn-sm" onclick="openRuleModal(' + r.id + ')">Edit</button>';
        box.appendChild(div);
    }
}

function renderRecords() {
    var tbody = document.getElementById('backupRecordsBody');
    tbody.innerHTML = '';
    if (!bkRecords.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="status-text">No backups yet</td></tr>';
        return;
    }
    for (var i = 0; i < bkRecords.length; i++) {
        var rec = bkRecords[i];
        var tr = document.createElement('tr');
        var status;
        if (rec.status === 'ok') status = '<span class="badge badge-running">OK</span>';
        else if (rec.status === 'running') status = '<span class="badge badge-stopping">RUNNING</span>';
        else status = '<span class="badge badge-stopped">FAILED</span>';
        var err = rec.error ? '<div class="status-sub" style="color:#c62828">' + esc(rec.error) + '</div>' : '';
        var actions = '';
        if (rec.status === 'ok') {
            actions =
                '<button class="btn btn-secondary btn-sm" onclick="downloadBackup(' + rec.id + ')">Download</button>' +
                '<button class="btn btn-restart btn-sm" onclick="restoreBackup(' + rec.id + ')">Restore</button>' +
                '<button class="btn btn-stop btn-sm" onclick="deleteBackup(' + rec.id + ')">Delete</button>';
        } else {
            actions = '<button class="btn btn-stop btn-sm" onclick="deleteBackup(' + rec.id + ')">Delete</button>';
        }
        tr.innerHTML =
            '<td>' + bkFmt(rec.created_at) + '</td>' +
            '<td>' + bkTriggerLabel(rec.trigger) + '</td>' +
            '<td>' + rec.file_count + '</td>' +
            '<td>' + (typeof formatSize === 'function' ? formatSize(rec.size_bytes || 0) : rec.size_bytes) + '</td>' +
            '<td>' + status + err + '</td>' +
            '<td><div class="backup-record-actions">' + actions + '</div></td>';
        tbody.appendChild(tr);
    }
}

async function toggleRule(id, enabled) {
    try {
        var r = await apiFetch('/admin/backups/rules/' + id, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled })
        });
        if (!r) return;
        if (!r.ok) { toast('Failed to update rule', 'error'); return; }
        if (bkCurrent) loadBackupData(bkCurrent);
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function runRule(id) {
    try {
        var r = await apiFetch('/admin/backups/rules/' + id + '/run', { method: 'POST' });
        if (!r) return;
        if (!r.ok) { toast('Failed to start backup', 'error'); return; }
        toast('Backup started', 'info');
        setTimeout(function () { if (bkCurrent) loadBackupData(bkCurrent); }, 2500);
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function backupNow() {
    if (!bkCurrent) { toast('Select a server first', 'error'); return; }
    try {
        var r = await apiFetch('/admin/backups/instances/' + encodeURIComponent(bkCurrent) + '/backup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folders: ['.'] })
        });
        if (!r) return;
        if (!r.ok) { toast('Failed to start backup', 'error'); return; }
        toast('Backup started', 'info');
        setTimeout(function () { if (bkCurrent) loadBackupData(bkCurrent); }, 2500);
        refreshBackupInstances();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

function openRuleModal(id) {
    bkEditingRule = null;
    document.getElementById('backupRuleModalTitle').textContent = 'New Backup Rule';
    document.getElementById('bkRuleDeleteBtn').style.display = 'none';
    document.getElementById('bkRuleName').value = '';
    document.getElementById('bkRuleFolders').value = '.';
    document.getElementById('bkRuleExclude').value = '';
    document.getElementById('bkRuleInterval').value = '0';
    document.getElementById('bkRuleRetentionCount').value = '10';
    document.getElementById('bkRuleRetentionDays').value = '0';
    document.getElementById('bkRuleOnStop').checked = false;
    document.getElementById('bkRuleEnabled').checked = true;
    if (id) {
        for (var i = 0; i < bkRules.length; i++) {
            if (bkRules[i].id === id) {
                bkEditingRule = bkRules[i];
                break;
            }
        }
        if (bkEditingRule) {
            document.getElementById('backupRuleModalTitle').textContent = 'Edit Backup Rule';
            document.getElementById('bkRuleDeleteBtn').style.display = 'inline-block';
            document.getElementById('bkRuleName').value = bkEditingRule.name || '';
            document.getElementById('bkRuleFolders').value = (bkEditingRule.folders || []).join('\n') || '.';
            document.getElementById('bkRuleExclude').value = (bkEditingRule.exclude || []).join('\n');
            document.getElementById('bkRuleInterval').value = String(bkEditingRule.interval_seconds || 0);
            document.getElementById('bkRuleRetentionCount').value = String(bkEditingRule.retention_count || 0);
            document.getElementById('bkRuleRetentionDays').value = String(bkEditingRule.retention_days || 0);
            document.getElementById('bkRuleOnStop').checked = !!bkEditingRule.backup_on_stop;
            document.getElementById('bkRuleEnabled').checked = bkEditingRule.enabled !== false;
        }
    }
    openModal('backupRuleModal');
}

function splitLines(value) {
    return value.split('\n').map(function (s) { return s.trim(); }).filter(function (s) { return s; });
}

async function saveRule() {
    if (!bkCurrent) { toast('Select a server first', 'error'); return; }
    var payload = {
        name: document.getElementById('bkRuleName').value.trim(),
        enabled: document.getElementById('bkRuleEnabled').checked,
        folders: splitLines(document.getElementById('bkRuleFolders').value),
        exclude: splitLines(document.getElementById('bkRuleExclude').value),
        interval_seconds: Number(document.getElementById('bkRuleInterval').value) || 0,
        retention_count: Number(document.getElementById('bkRuleRetentionCount').value) || 0,
        retention_days: Number(document.getElementById('bkRuleRetentionDays').value) || 0,
        backup_on_stop: document.getElementById('bkRuleOnStop').checked
    };
    if (!payload.folders.length) payload.folders = ['.'];
    try {
        var r;
        if (bkEditingRule) {
            r = await apiFetch('/admin/backups/rules/' + bkEditingRule.id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } else {
            payload.instance_id = bkCurrent;
            r = await apiFetch('/admin/backups/rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }
        if (!r) return;
        if (!r.ok) { toast('Failed to save rule', 'error'); return; }
        closeModal('backupRuleModal');
        loadBackupData(bkCurrent);
        refreshBackupInstances();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function deleteRule() {
    if (!bkEditingRule) return;
    if (!window.confirm('Delete this rule?')) return;
    try {
        var r = await apiFetch('/admin/backups/rules/' + bkEditingRule.id, { method: 'DELETE' });
        if (!r) return;
        closeModal('backupRuleModal');
        if (bkCurrent) loadBackupData(bkCurrent);
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

function downloadBackup(id) {
    downloadBlob('/admin/backups/' + id + '/download', 'backup-' + id + '.zip');
}

async function restoreBackup(id) {
    var doStop = window.confirm('Restore this backup into the server directory?\n\nWarning: existing files will be overwritten. The server should be stopped first.');
    if (!doStop) return;
    try {
        var r = await apiFetch('/admin/backups/' + id + '/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stop: true, start: false })
        });
        if (!r) return;
        if (!r.ok) {
            var d = await r.json().catch(function () { return {}; });
            toast(d.error || 'Restore failed', 'error');
            return;
        }
        toast('Restore complete (' + d.restored + ' files)', 'info');
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function deleteBackup(id) {
    if (!window.confirm('Delete this backup archive?')) return;
    try {
        var r = await apiFetch('/admin/backups/' + id, { method: 'DELETE' });
        if (!r) return;
        if (bkCurrent) loadBackupData(bkCurrent);
        refreshBackupInstances();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

Uroboros.on('tab', function (data) {
    if (data && data.tab === 'backups') loadBackups();
});
