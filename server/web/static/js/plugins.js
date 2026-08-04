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
        tr.innerHTML = '<td><strong>' + esc(p.name) + '</strong><div class="status-sub">' + esc(p.id) + '</div>' + deps + '</td>' +
            '<td>' + esc(p.version) + '</td>' +
            '<td>' + esc(p.author || '\u2014') + '</td>' +
            '<td>' + status + err + '</td>' +
            '<td class="players-actions">' + action + '</td>';
        body.appendChild(tr);
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
