/* ===================== Java runtimes ===================== */

async function loadJavaRuntimes() {
    try {
        var r = await apiFetch('/admin/java');
        if (!r) return;
        var runtimes = await r.json();
        var tbody = document.getElementById('javaBody');
        var installedBody = document.getElementById('javaInstalledBody');
        tbody.innerHTML = '';
        installedBody.innerHTML = '';
        var installedCount = 0;
        for (var i = 0; i < runtimes.length; i++) {
            var j = runtimes[i];
            var row = '<td>' + j.major + '</td><td>' + esc(j.version) + '</td><td>' + esc(j.vendor || '') + '</td><td>' + esc(j.arch || '') + '</td><td><code>' + esc(j.path) + '</code></td>';
            var tr = document.createElement('tr');
            tr.innerHTML = row;
            if (j.source === 'installed') {
                var action = document.createElement('td');
                action.innerHTML = '<button class="btn btn-stop btn-sm" onclick="uninstallJava(' + "'" + escAttr(j.path) + "'" + ')">Remove</button>';
                tr.appendChild(action);
                installedBody.appendChild(tr);
                installedCount++;
            } else {
                tbody.appendChild(tr);
            }
        }
        if (!installedCount) {
            installedBody.innerHTML = '<tr><td colspan="6" style="color:#888;text-align:center;padding:20px">No installed runtimes</td></tr>';
        }
    } catch (e) { console.error('loadJavaRuntimes failed', e); }
}

var javaVendorVersions = {};

async function loadJavaAvailable() {
    try {
        var r = await apiFetch('/admin/java/available');
        if (!r) return;
        var d = await r.json();
        var sel = document.getElementById('javaVendorSelect');
        sel.innerHTML = '';
        if (d.platform) {
            document.getElementById('javaInstallPlatform').textContent = 'Platform: ' + d.platform.display;
        }
        if (d.error) {
            sel.innerHTML = '<option value="">' + esc(d.error) + '</option>';
            return;
        }
        javaVendorVersions = {};
        for (var i = 0; i < d.vendors.length; i++) {
            var v = d.vendors[i];
            javaVendorVersions[v.id] = v.versions;
            var opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.label;
            sel.appendChild(opt);
        }
        loadJavaVersions();
    } catch (e) {}
}

function loadJavaVersions() {
    var vendor = document.getElementById('javaVendorSelect').value;
    var sel = document.getElementById('javaVersionSelect');
    sel.innerHTML = '';
    var versions = (javaVendorVersions[vendor] || []).slice().sort(function (a, b) { return a - b; });
    var lts = [25, 21, 17, 11, 8];
    for (var i = 0; i < versions.length; i++) {
        var v = versions[i];
        var opt = document.createElement('option');
        opt.value = v;
        opt.textContent = 'Java ' + v + (lts.indexOf(v) !== -1 ? ' (LTS)' : '');
        sel.appendChild(opt);
    }
    if (versions.length) {
        var pref = -1;
        for (var k = versions.length - 1; k >= 0; k--) {
            if (lts.indexOf(versions[k]) !== -1) { pref = versions[k]; break; }
        }
        if (pref !== -1) sel.value = pref;
    } else {
        sel.innerHTML = '<option value="">No versions</option>';
    }
}

async function scanJava() {
    var btn = document.getElementById('javaScanBtn');
    btn.disabled = true;
    document.getElementById('javaScanStatus').textContent = 'Scanning...';
    try {
        var r = await apiFetch('/admin/java/scan', { method: 'POST' });
        if (!r) return;
        var d = await r.json();
        if (d.error) { toast(d.error, 'error'); }
        else { toast('Found ' + d.found + ' runtimes', 'success'); }
        loadJavaRuntimes();
    } catch (e) { toast('Scan failed: ' + e.message, 'error'); }
    btn.disabled = false;
    document.getElementById('javaScanStatus').textContent = '';
}

async function installJava() {
    var sel = document.getElementById('javaVersionSelect');
    var vendor = document.getElementById('javaVendorSelect').value;
    var version = sel.value;
    if (!vendor) { toast('Select a vendor', 'error'); return; }
    if (!version) { toast('Select a Java version', 'error'); return; }
    var btn = document.getElementById('javaInstallBtn');
    var progress = document.getElementById('javaInstallProgress');
    btn.disabled = true;
    progress.style.display = 'block';
    var fill = document.getElementById('javaInstallFill');
    var txt = document.getElementById('javaInstallText');
    fill.style.width = '0%';
    txt.textContent = 'Starting...';
    try {
        var r = await apiFetch('/admin/java/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version: version, vendor: vendor })
        });
        if (!r) { btn.disabled = false; progress.style.display = 'none'; return; }
        var d = await r.json();
        if (d.error) {
            txt.innerHTML = '<span style="color:#d32f2f">Error: ' + esc(d.error) + '</span>';
            toast('Install failed', 'error');
            btn.disabled = false;
            setTimeout(function () { progress.style.display = 'none'; }, 5000);
            return;
        }
        var taskId = d.task_id;
        var pollUrl = '/admin/java/install/progress/' + taskId;
        var startedAt = Date.now();
        var done = false;
        var finish = function (successMsg) {
            if (done) return;
            done = true;
            clearInterval(pollTimer);
            btn.disabled = false;
            loadJavaRuntimes();
            if (successMsg) {
                fill.style.width = '100%';
                txt.innerHTML = '<span style="color:#4caf50">' + esc(successMsg) + '</span>';
                setTimeout(function () { progress.style.display = 'none'; }, 2500);
            }
        };
        var fail = function (msg) {
            if (done) return;
            done = true;
            clearInterval(pollTimer);
            btn.disabled = false;
            txt.innerHTML = '<span style="color:#d32f2f">' + esc(msg) + '</span>';
            setTimeout(function () { progress.style.display = 'none'; }, 5000);
        };
        var pollTimer = setInterval(async function() {
            if (Date.now() - startedAt > 60 * 60 * 1000) { fail('Install timed out after 60 minutes'); return; }
            try {
                var pr = await apiFetch(pollUrl);
                if (!pr) { fail('Connection lost'); return; }
                var ps = await pr.json();
                if (ps.error) {
                    if (ps.error === 'Task not found') { finish('Java ' + esc(String(version)) + ' installed'); toast('Java ' + version + ' installed', 'success'); }
                    else { fail('Error: ' + ps.error); }
                    return;
                }
                txt.textContent = ps.message || ps.status;
                if (ps.total > 0) {
                    fill.style.width = Math.min(100, Math.round(ps.current / ps.total * 100)) + '%';
                }
                if (ps.status === 'done') {
                    toast('Java ' + version + ' installed', 'success');
                    finish('Java ' + esc(String(version)) + ' installed successfully');
                } else if (ps.status === 'error') {
                    fail('Install failed: ' + (ps.error || 'Unknown error'));
                }
            } catch (e) {
                fail('Poll error: ' + e.message);
            }
        }, 800);
    } catch (e) {
        txt.innerHTML = '<span style="color:#d32f2f">Error: ' + esc(e.message) + '</span>';
        btn.disabled = false;
        setTimeout(function () { progress.style.display = 'none'; }, 5000);
    }
}

async function uninstallJava(path) {
    if (!confirm('Remove this Java runtime?\n' + path)) return;
    try {
        var r = await apiFetch('/admin/java/uninstall', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path })
        });
        if (!r) return;
        var d = await r.json();
        if (d.error) { toast(d.error, 'error'); return; }
        toast('Java runtime removed', 'success');
        loadJavaRuntimes();
    } catch (e) { toast('Remove failed: ' + e.message, 'error'); }
}
