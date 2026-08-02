/* ===================== Uroboros self-update ===================== */

var updPollTimer = null;

async function loadUpdateStatus() {
    try {
        var r = await apiFetch('/admin/update/status');
        if (!r) return null;
        var d = await r.json();
        renderUpdateStatus(d);
        return d;
    } catch (e) {
        toast('Update status failed: ' + e.message, 'error');
        return null;
    }
}

function formatUpdateDate(s) {
    if (!s) return '-';
    var dt = new Date(s);
    if (isNaN(dt.getTime())) return s;
    return dt.toLocaleString();
}

function renderUpdateStatus(d) {
    document.getElementById('updInstalled').textContent = d.installed || '-';
    document.getElementById('updCheckStatus').textContent = '';
    var notice = document.getElementById('updNotice');
    var notes = document.getElementById('updReleaseNotes');

    if (d.checking) {
        document.getElementById('updLatest').textContent = 'Checking...';
        document.getElementById('updPublished').textContent = '-';
        document.getElementById('updKind').textContent = '-';
        document.getElementById('updCheckStatus').textContent = 'Checking for updates...';
        notice.innerHTML = '';
        notes.style.display = 'none';
        return;
    }

    var lc = d.last_check;
    if (lc && lc.error) {
        document.getElementById('updLatest').textContent = 'Error';
        document.getElementById('updPublished').textContent = '-';
        document.getElementById('updKind').textContent = '-';
        notice.innerHTML = '<div class="upd-error">' + esc(lc.error) + '</div>';
        notes.style.display = 'none';
    } else if (lc && lc.latest) {
        var lt = lc.latest;
        document.getElementById('updLatest').textContent = lt.version;
        document.getElementById('updPublished').textContent = formatUpdateDate(lt.published_at);
        document.getElementById('updKind').textContent = (lt.kind === 'release' ? 'Release ' : 'Branch ') + (lt.ref || '');
        if (lc.update_available) {
            notice.innerHTML = '<div class="upd-avail">Update available: <b>' + esc(lt.version) + '</b></div>';
            notes.style.display = 'block';
            notes.textContent = lt.body || 'No release notes for this version.';
        } else {
            notice.innerHTML = '<div class="upd-ok">You are up to date.</div>';
            notes.style.display = 'none';
        }
    } else if (d.check_error) {
        notice.innerHTML = '<div class="upd-error">' + esc(d.check_error) + '</div>';
        notes.style.display = 'none';
    } else {
        notice.innerHTML = '<div class="upd-muted">Press "Check for updates" to query GitHub.</div>';
        notes.style.display = 'none';
    }

    renderRunOptions(d);
    renderUpdateProgress(d.run);
    renderUpdateBackups(d.backups);
}

function renderRunOptions(d) {
    var running = d.running_servers || [];
    var warn = document.getElementById('updRunningServers');
    if (running.length) {
        var names = running.map(function (s) { return esc(s.name || s.id); }).join(', ');
        warn.style.display = 'block';
        warn.innerHTML = 'Running servers: <b>' + names + '</b>. They will be stopped before the update when enabled.';
    } else {
        warn.style.display = 'none';
        warn.innerHTML = '';
    }
    var run = d.run;
    var runBtn = document.getElementById('updRunBtn');
    var cancelBtn = document.getElementById('updCancelBtn');
    var options = document.getElementById('updRunOptions');
    var active = run && ['starting', 'working', 'cancelling', 'downloading'].indexOf(run.status) !== -1;
    if (active) {
        runBtn.disabled = true;
        cancelBtn.style.display = 'inline-flex';
        options.style.display = 'none';
    } else {
        runBtn.disabled = false;
        cancelBtn.style.display = 'none';
        options.style.display = 'block';
    }
}

function renderUpdateProgress(run) {
    var prog = document.getElementById('updProgress');
    var fill = document.getElementById('updFill');
    var txt = document.getElementById('updProgressText');
    var log = document.getElementById('updLog');
    if (!run) {
        prog.style.display = 'none';
        log.style.display = 'none';
        return;
    }
    prog.style.display = 'block';
    fill.style.width = '0%';
    if (run.total > 0) {
        fill.style.width = Math.min(100, Math.round(run.current / run.total * 100)) + '%';
    }
    txt.textContent = run.message || run.status;
    if (run.status === 'done') {
        fill.style.width = '100%';
        txt.innerHTML = '<span style="color:#4caf50">' + esc(run.message || 'Update complete') + '</span>';
        showUpdateLog(run, log);
    } else if (run.status === 'error') {
        txt.innerHTML = '<span style="color:#d32f2f">Failed: ' + esc(run.error || run.message || 'Unknown error') + '</span>';
        showUpdateLog(run, log);
    } else if (run.status === 'cancelled') {
        txt.innerHTML = '<span style="color:#6a737d">Update cancelled.</span>';
        log.style.display = 'none';
    } else {
        log.style.display = 'none';
    }
}

function showUpdateLog(run, el) {
    el.style.display = 'block';
    var lines = [
        'New version: ' + (run.new_version || '-'),
        'Backup: ' + (run.backup || 'none'),
        'Stopped servers: ' + (run.stopped && run.stopped.length ? run.stopped.join(', ') : 'none'),
        'Restarted servers: ' + (run.restarted && run.restarted.length ? run.restarted.join(', ') : 'none')
    ];
    el.innerHTML = lines.map(function (l) { return '<div>' + esc(l) + '</div>'; }).join('');
}

function renderUpdateBackups(backups) {
    var el = document.getElementById('updBackups');
    if (!backups || !backups.length) {
        el.textContent = 'None';
        return;
    }
    el.innerHTML = backups.map(function (b) { return '<div class="upd-backup">' + esc(b) + '</div>'; }).join('');
}

async function checkUpdateNow() {
    var btn = document.getElementById('updCheckBtn');
    btn.disabled = true;
    document.getElementById('updCheckStatus').textContent = 'Checking...';
    try {
        var r = await apiFetch('/admin/update/check', { method: 'POST' });
        if (!r) return;
        btn.disabled = false;
        var d = await r.json();
        if (d.error) {
            document.getElementById('updCheckStatus').textContent = '';
            toast(d.error, 'error');
            return;
        }
        startUpdatePolling();
    } catch (e) {
        btn.disabled = false;
        document.getElementById('updCheckStatus').textContent = '';
        toast('Check failed: ' + e.message, 'error');
    }
}

function startUpdatePolling() {
    stopUpdatePolling();
    updPollTimer = setInterval(function () {
        loadUpdateStatus().then(function (d) {
            if (d) {
                var done = !d.checking && (!d.run || ['done', 'error', 'cancelled'].indexOf(d.run.status) !== -1);
                if (done) {
                    stopUpdatePolling();
                    if (d.run && d.run.status === 'done') toast('Update applied to disk. Restart Uroboros Server to finish.', 'success');
                }
            }
        });
    }, 1500);
}

function stopUpdatePolling() {
    if (updPollTimer) { clearInterval(updPollTimer); updPollTimer = null; }
}

async function runUroborosUpdate() {
    if (!confirm('Run the Uroboros Server update now?')) return;
    var body = {
        stop_servers: document.getElementById('updStopServers').checked,
        restart_servers: document.getElementById('updRestartServers').checked,
        install_requirements: document.getElementById('updInstallReq').checked,
        force: document.getElementById('updForce').checked
    };
    try {
        var r = await apiFetch('/admin/update/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!r) return;
        var d = await r.json();
        if (d.error) { toast(d.error, 'error'); return; }
        toast('Update started', 'info');
        document.getElementById('updRunBtn').disabled = true;
        loadUpdateStatus();
        startUpdatePolling();
    } catch (e) { toast('Update failed: ' + e.message, 'error'); }
}

async function cancelUroborosUpdate() {
    try {
        var r = await apiFetch('/admin/update/cancel', { method: 'POST' });
        if (!r) return;
        var d = await r.json();
        if (d.error) { toast(d.error, 'error'); return; }
        document.getElementById('updProgressText').textContent = 'Cancelling...';
        toast('Cancelling update...', 'info');
    } catch (e) { toast('Cancel failed: ' + e.message, 'error'); }
}
