/* ===================== Backups plugin ===================== */

var bkInstances = [];
var bkRules = [];
var bkRecords = [];
var bkCurrent = null;
var bkEditingRule = null;

var bkTreeCache = {};
var bkExpanded = { '__root__': true };
var bkNodeEls = {};
var bkSelFolders = ['.'];
var bkPollTimer = null;

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
        var results = await Promise.all([
            apiFetch('/admin/backups/instances'),
            apiFetch('/admin/backups/stats')
        ]);
        if (!results[0]) return;
        bkInstances = await results[0].json();
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
        if (results[1]) renderBackupSummary(await results[1].json());
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
        var results = await Promise.all([
            apiFetch('/admin/backups/rules?instance_id=' + encodeURIComponent(instanceId)),
            apiFetch('/admin/backups?instance_id=' + encodeURIComponent(instanceId))
        ]);
        if (!results[0] || !results[1]) return;
        bkRules = await results[0].json();
        bkRecords = await results[1].json();
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
        if (r.destination_dir) desc += ' \u00b7 \u2192 ' + esc(r.destination_dir);
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
        else if (rec.status === 'running') status = bkRunningHtml(rec);
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

function bkRunningHtml(rec) {
    var pct = rec.progress_percent || 0;
    var sub = (rec.phase === 'scan' ? 'Scanning\u2026' : pct + '%');
    if (rec.done_files != null && rec.total_files != null) sub += ' \u00b7 ' + rec.done_files + '/' + rec.total_files + ' files';
    return '<span class="badge badge-stopping">RUNNING</span>' +
        '<div class="bk-progress" id="bk-progress-' + rec.id + '">' +
        '<div class="bk-progress-fill" style="width:' + pct + '%"></div>' +
        '<div class="bk-progress-sub">' + sub + '</div></div>';
}

function pollProgress(recordId) {
    if (bkPollTimer) clearInterval(bkPollTimer);
    bkPollTimer = setInterval(function () {
        apiFetch('/admin/backups/progress/' + recordId).then(function (r) {
            if (!r) return;
            r.json().then(function (p) {
                if (p.running) {
                    var wrap = document.getElementById('bk-progress-' + recordId);
                    if (wrap) {
                        var pct = p.percent || 0;
                        wrap.querySelector('.bk-progress-fill').style.width = pct + '%';
                        var sub = (p.phase === 'scan' ? 'Scanning\u2026' : pct + '%');
                        if (p.done_files != null && p.total_files != null) sub += ' \u00b7 ' + p.done_files + '/' + p.total_files + ' files';
                        wrap.querySelector('.bk-progress-sub').textContent = sub;
                    }
                } else {
                    clearInterval(bkPollTimer);
                    bkPollTimer = null;
                    if (bkCurrent) loadBackupData(bkCurrent);
                    refreshBackupInstances();
                }
            });
        }).catch(function () {});
    }, 1000);
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
        var data = await r.json().catch(function () { return {}; });
        toast('Backup started', 'info');
        if (data.record_id) pollProgress(data.record_id);
        if (bkCurrent) loadBackupData(bkCurrent);
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function backupNow() {
    if (!bkCurrent) { toast('Select a server first', 'error'); return; }
    try {
        var dest = document.getElementById('bkRuleDest') ? document.getElementById('bkRuleDest').value.trim() : '';
        var r = await apiFetch('/admin/backups/instances/' + encodeURIComponent(bkCurrent) + '/backup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folders: ['.'], destination_dir: dest })
        });
        if (!r) return;
        if (!r.ok) { toast('Failed to start backup', 'error'); return; }
        var data = await r.json().catch(function () { return {}; });
        toast('Backup started', 'info');
        if (data.record_id) pollProgress(data.record_id);
        if (bkCurrent) loadBackupData(bkCurrent);
        refreshBackupInstances();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

function openRuleModal(id) {
    bkEditingRule = null;
    document.getElementById('backupRuleModalTitle').textContent = 'New Backup Rule';
    document.getElementById('bkRuleDeleteBtn').style.display = 'none';
    document.getElementById('bkRuleName').value = '';
    document.getElementById('bkRuleExclude').value = '';
    document.getElementById('bkRuleInterval').value = '0';
    document.getElementById('bkRuleRetentionCount').value = '10';
    document.getElementById('bkRuleRetentionDays').value = '0';
    document.getElementById('bkRuleOnStop').checked = false;
    document.getElementById('bkRuleEnabled').checked = true;
    document.getElementById('bkRuleDest').value = '';
    document.getElementById('bkRuleAddPath').value = '';
    bkSelFolders = ['.'];
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
            document.getElementById('bkRuleExclude').value = (bkEditingRule.exclude || []).join('\n');
            document.getElementById('bkRuleInterval').value = String(bkEditingRule.interval_seconds || 0);
            document.getElementById('bkRuleRetentionCount').value = String(bkEditingRule.retention_count || 0);
            document.getElementById('bkRuleRetentionDays').value = String(bkEditingRule.retention_days || 0);
            document.getElementById('bkRuleOnStop').checked = !!bkEditingRule.backup_on_stop;
            document.getElementById('bkRuleEnabled').checked = bkEditingRule.enabled !== false;
            document.getElementById('bkRuleDest').value = bkEditingRule.destination_dir || '';
            bkSelFolders = (bkEditingRule.folders && bkEditingRule.folders.length) ? bkEditingRule.folders.slice() : ['.'];
        }
    }
    renderSelFolders();
    renderTree();
    openModal('backupRuleModal');
}

/* ---- folder tree ---- */

function bkRootLabel() {
    for (var i = 0; i < bkInstances.length; i++) {
        if (bkInstances[i].id === bkCurrent) {
            var d = bkInstances[i].server_dir || '';
            d = d.replace(/[\\/]+$/, '');
            var parts = d.split(/[\\/]/);
            var name = parts[parts.length - 1];
            if (name) return name;
        }
    }
    return 'server';
}

function renderTree() {
    var box = document.getElementById('bkRuleTree');
    box.innerHTML = '';
    bkTreeCache = {};
    bkNodeEls = {};
    if (!bkExpanded['__root__']) bkExpanded['__root__'] = true;
    box.appendChild(treeNodeEl('', bkRootLabel()));
    renderTreeChildren('', box.firstChild.querySelector('.bk-tree-children'));
}

function treeNodeEl(path, name) {
    var node = document.createElement('div');
    node.className = 'bk-tree-node';
    var expanded = !!bkExpanded[path === '' ? '__root__' : path];
    var toggle = document.createElement('span');
    toggle.className = 'bk-tree-toggle' + (expanded ? ' open' : '');
    toggle.textContent = expanded ? '\u25be' : '\u25b8';
    toggle.onclick = function () { bkToggleDir(path); };
    var label = document.createElement('label');
    label.className = 'bk-tree-label';
    label.dataset.path = path;
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = bkSelFolders.indexOf(path) >= 0;
    cb.onchange = function () { bkToggleSel(path, cb.checked); };
    var span = document.createElement('span');
    span.className = 'bk-tree-name';
    span.textContent = name;
    label.appendChild(cb);
    label.appendChild(span);
    var children = document.createElement('div');
    children.className = 'bk-tree-children';
    node.appendChild(toggle);
    node.appendChild(label);
    node.appendChild(children);
    bkNodeEls[path === '' ? '__root__' : path] = { toggle: toggle, children: children };
    return node;
}

async function renderTreeChildren(path, container) {
    var key = path === '' ? '__root__' : path;
    var data = bkTreeCache[key];
    if (!data) {
        container.innerHTML = '<div class="bk-tree-loading">Loading\u2026</div>';
        try {
            var r = await apiFetch('/admin/backups/instances/' + encodeURIComponent(bkCurrent) +
                '/tree?path=' + encodeURIComponent(path));
            if (!r) { container.innerHTML = ''; return; }
            data = await r.json();
            bkTreeCache[key] = data;
        } catch (e) {
            container.innerHTML = '<div class="bk-tree-loading">Failed to load</div>';
            return;
        }
    }
    container.innerHTML = '';
    if (!data.dirs || !data.dirs.length) {
        container.innerHTML = '<div class="bk-tree-loading">(empty)</div>';
        return;
    }
    for (var i = 0; i < data.dirs.length; i++) {
        var d = data.dirs[i];
        container.appendChild(treeNodeEl(d.path, d.name));
    }
}

async function bkToggleDir(path) {
    var rec = bkNodeEls[path === '' ? '__root__' : path];
    if (!rec) return;
    var expanded = !bkExpanded[path === '' ? '__root__' : path];
    bkExpanded[path === '' ? '__root__' : path] = expanded;
    rec.toggle.className = 'bk-tree-toggle' + (expanded ? ' open' : '');
    rec.toggle.textContent = expanded ? '\u25be' : '\u25b8';
    if (expanded) {
        await renderTreeChildren(path, rec.children);
    } else {
        rec.children.innerHTML = '';
    }
}

function bkToggleSel(path, checked) {
    if (path === '') {
        bkSelFolders = checked ? ['.'] : [];
    } else if (checked) {
        bkSelFolders = bkSelFolders.filter(function (p) { return p !== '.'; });
        if (bkSelFolders.indexOf(path) < 0) bkSelFolders.push(path);
    } else {
        bkSelFolders = bkSelFolders.filter(function (p) { return p !== path; });
    }
    renderSelFolders();
    syncTreeCheckboxes();
}

function syncTreeCheckboxes() {
    var box = document.getElementById('bkRuleTree');
    var labels = box.querySelectorAll('.bk-tree-label');
    for (var i = 0; i < labels.length; i++) {
        var p = labels[i].dataset.path;
        var cb = labels[i].querySelector('input[type=checkbox]');
        cb.checked = bkSelFolders.indexOf(p) >= 0;
    }
}

function bkAddPath() {
    var input = document.getElementById('bkRuleAddPath');
    var v = (input.value || '').trim().replace(/\\/g, '/').replace(/^\/+/, '').replace(/\/+$/, '');
    if (!v) return;
    if (v === '.') {
        bkSelFolders = ['.'];
    } else {
        bkSelFolders = bkSelFolders.filter(function (p) { return p !== '.'; });
        if (bkSelFolders.indexOf(v) < 0) bkSelFolders.push(v);
    }
    input.value = '';
    renderSelFolders();
    syncTreeCheckboxes();
}

function renderSelFolders() {
    var box = document.getElementById('bkRuleSelected');
    box.innerHTML = '';
    if (!bkSelFolders.length) {
        box.innerHTML = '<div class="bk-hint">Nothing selected \u2014 the whole server folder will be backed up.</div>';
        return;
    }
    for (var i = 0; i < bkSelFolders.length; i++) {
        (function (p) {
            var chip = document.createElement('span');
            chip.className = 'bk-chip';
            chip.textContent = p === '.' ? 'entire server (.)' : p;
            var x = document.createElement('button');
            x.className = 'bk-chip-x';
            x.textContent = '\u00d7';
            x.onclick = function () { bkToggleSel(p, false); };
            chip.appendChild(x);
            box.appendChild(chip);
        })(bkSelFolders[i]);
    }
}

function splitLines(value) {
    return value.split('\n').map(function (s) { return s.trim(); }).filter(function (s) { return s; });
}

async function saveRule() {
    if (!bkCurrent) { toast('Select a server first', 'error'); return; }
    var payload = {
        name: document.getElementById('bkRuleName').value.trim(),
        enabled: document.getElementById('bkRuleEnabled').checked,
        folders: bkSelFolders.length ? bkSelFolders.slice() : ['.'],
        exclude: splitLines(document.getElementById('bkRuleExclude').value),
        interval_seconds: Number(document.getElementById('bkRuleInterval').value) || 0,
        retention_count: Number(document.getElementById('bkRuleRetentionCount').value) || 0,
        retention_days: Number(document.getElementById('bkRuleRetentionDays').value) || 0,
        backup_on_stop: document.getElementById('bkRuleOnStop').checked,
        destination_dir: document.getElementById('bkRuleDest').value.trim()
    };
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
        toast('Restore complete', 'info');
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
