/* ===================== Plugins tab ===================== */

async function loadPlugins() {
    var body = document.getElementById('pluginsBody');
    body.innerHTML = '<tr><td colspan="5">Loading...</td></tr>';
    try {
        var r = await apiFetch('/admin/plugins');
        if (!r) return;
        renderPlugins(await r.json());
    } catch (e) {
        body.innerHTML = '<tr><td colspan="5">Failed to load plugins</td></tr>';
        toast('Failed: ' + e.message, 'error');
    }
}

function renderPlugins(plugins) {
    var body = document.getElementById('pluginsBody');
    body.innerHTML = '';
    if (!plugins.length) {
        body.innerHTML = '<tr><td colspan="5"><span class="status-text">No plugins installed</span></td></tr>';
        return;
    }
    for (var i = 0; i < plugins.length; i++) {
        var p = plugins[i];
        var tr = document.createElement('tr');
        var status;
        if (!p.enabled) status = '<span class="badge badge-stopped">DISABLED</span>';
        else if (p.loaded) status = '<span class="badge badge-running">LOADED</span>';
        else status = '<span class="badge badge-stopping">FAILED</span>';
        var err = p.error ? '<div class="status-sub" style="color:#c62828">' + esc(p.error) + '</div>' : '';
        var deps = p.requirements && p.requirements.length
            ? '<div class="status-sub" style="color:#888">deps: ' + esc(p.requirements.join(', ')) + '</div>'
            : '';
        var action = p.enabled
            ? '<button class="btn btn-stop btn-sm" onclick="togglePlugin(\'' + p.id + '\',false)">Disable</button>'
            : '<button class="btn btn-start btn-sm" onclick="togglePlugin(\'' + p.id + '\',true)">Enable</button>';
        var uninstall = '<button class="btn btn-secondary btn-sm" onclick="uninstallPlugin(\'' + p.id + '\')">Uninstall</button>';
        var restart = p.needs_restart
            ? '<div class="status-sub" style="color:#e65100">restart required</div>'
            : '';
        tr.innerHTML = '<td><strong>' + esc(p.name) + '</strong><div class="status-sub">' + esc(p.id) + '</div>' + deps + '</td>' +
            '<td>' + esc(p.version) + '</td>' +
            '<td>' + esc(p.author || '\u2014') + '</td>' +
            '<td>' + status + err + restart + '</td>' +
            '<td class="players-actions">' + action + ' ' + uninstall + '</td>';
        body.appendChild(tr);
    }
}

function openPluginInstallModal() {
    document.getElementById('pluginInstallFile').value = '';
    document.getElementById('pluginInstallProgress').style.display = 'none';
    openModal('pluginInstallModal');
}

async function installPluginSubmit() {
    var input = document.getElementById('pluginInstallFile');
    var file = input.files && input.files[0];
    if (!file) {
        toast('Choose a plugin archive first', 'error');
        return;
    }
    var progress = document.getElementById('pluginInstallProgress');
    progress.style.display = 'block';
    progress.textContent = 'Uploading and installing ' + file.name + '...';
    var fd = new FormData();
    fd.append('file', file);
    try {
        var r = await apiFetch('/admin/plugins/install', { method: 'POST', body: fd });
        if (!r) return;
        var d = await r.json().catch(function () { return {}; });
        if (!r.ok) {
            progress.textContent = '';
            toast(d.error || 'Install failed', 'error');
            return;
        }
        progress.textContent = '';
        if (d.loaded) {
            toast('Plugin ' + d.id + ' installed. Restart required.', 'success');
        } else {
            toast('Plugin ' + d.id + ' installed but failed to load: ' + (d.error || 'unknown error'), 'error', 6000);
        }
        closeModal('pluginInstallModal');
        loadPlugins();
    } catch (e) {
        progress.textContent = '';
        toast('Failed: ' + e.message, 'error');
    }
}

async function uninstallPlugin(id) {
    if (!confirm('Uninstall plugin "' + id + '"? This cannot be undone.')) return;
    try {
        var r = await apiFetch('/admin/plugins/' + encodeURIComponent(id) + '/uninstall', { method: 'POST' });
        if (!r) return;
        var d = await r.json().catch(function () { return {}; });
        if (!r.ok) {
            toast(d.error || 'Failed to uninstall', 'error');
            return;
        }
        toast('Plugin ' + id + ' uninstalled.', 'success');
        loadPlugins();
    } catch (e) {
        toast('Failed: ' + e.message, 'error');
    }
}

async function togglePlugin(id, enabled) {
    try {
        var r = await apiFetch('/admin/plugins/' + encodeURIComponent(id) + '/' + (enabled ? 'enable' : 'disable'), { method: 'POST' });
        if (!r) return;
        if (!r.ok) {
            var d = await r.json().catch(function () { return {}; });
            toast(d.error || 'Failed', 'error');
            return;
        }
        toast('Plugin ' + (enabled ? 'enabled' : 'disabled') + '. Restart required.', 'info');
        loadPlugins();
    } catch (e) {
        toast('Failed: ' + e.message, 'error');
    }
}
