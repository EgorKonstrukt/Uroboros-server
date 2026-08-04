/* ===================== Navigation: tabs, sidebar, server selection ===================== */

function tabMeta(id) {
    var tabs = window.UROBOROS_TABS || [];
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].id === id) return tabs[i];
    }
    return null;
}

function switchTab(name) {
    clearServerPolling();
    document.getElementById('serverDetailView').style.display = 'none';
    document.getElementById('serverMiniBar').style.display = 'none';
    document.querySelectorAll('.sidebar-server-item').forEach(function (s) { s.classList.remove('active'); });
    document.querySelectorAll('.server-tab').forEach(function (t) { t.classList.remove('active'); });
    currentServerId = null;
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.remove('active'); });
    document.querySelectorAll('.nav-item').forEach(function (t) { t.classList.remove('active'); });
    var panel = document.getElementById(name + 'Panel');
    if (panel) panel.classList.add('active');
    var navItem = document.querySelector('.nav-item[data-tab="' + name + '"]');
    if (navItem) navItem.classList.add('active');
    var meta = tabMeta(name);
    var titleEl = document.getElementById('pageTitle');
    if (titleEl) titleEl.textContent = (meta && meta.title) || name;
    var actions = document.getElementById('topActions');
    if (actions) actions.innerHTML = '';
    if (meta && meta.loader && typeof window[meta.loader] === 'function') window[meta.loader]();
    Uroboros.emit('tab', { tab: name, config: meta || null });
}

function clearServerPolling() {
    if (serverPollTimer) { clearInterval(serverPollTimer); serverPollTimer = null; }
    if (typeof stopOverview === 'function') stopOverview();
    stopStatusPolling();
}

function selectServer(id) {
    clearServerPolling();
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.remove('active'); });
    document.querySelectorAll('.nav-item').forEach(function (t) { t.classList.remove('active'); });
    document.querySelectorAll('.sidebar-server-item').forEach(function (s) { s.classList.remove('active'); });
    document.querySelectorAll('.server-tab').forEach(function (t) { t.classList.remove('active'); });
    document.getElementById('serversPanel').classList.add('active');
    var sidebarItem = document.querySelector('.sidebar-server-item[data-server="' + id + '"]');
    if (sidebarItem) sidebarItem.classList.add('active');
    var tab = document.querySelector('.server-tab[data-server="' + id + '"]');
    if (tab) tab.classList.add('active');
    updateTabIndicator(document.getElementById('serverTabs'));
    document.getElementById('pageTitle').textContent = 'Server';
    document.getElementById('topActions').innerHTML = '';
    openServerDetail(id);
    Uroboros.emit('server', { id: id });
}

function updateTabIndicator(container) {
    if (!container) return;
    var ind = container.querySelector('.tab-indicator');
    if (!ind) {
        ind = document.createElement('div');
        ind.className = 'tab-indicator';
        container.appendChild(ind);
    }
    var active = container.querySelector('.server-tab.active, .sub-nav-item.active');
    if (active) {
        ind.style.transform = 'translateX(' + active.offsetLeft + 'px)';
        ind.style.width = active.offsetWidth + 'px';
    } else {
        ind.style.transform = 'translateX(0px)';
        ind.style.width = '0px';
    }
}

function renderServerNav() {
    apiFetch('/admin/instances').then(function (r) {
        if (!r) return;
        r.json().then(function (servers) {
            renderSidebarServers(servers);
            renderServerTabs(servers);
        });
    });
}

function serverDotClass(s) {
    if (s.starting || s.stopping) return 'dot-busy';
    return s.running ? 'dot-on' : 'dot-off';
}

function updateServerDot(id, s) {
    var cls = 'server-dot ' + serverDotClass(s);
    var sel = '.sidebar-server-item[data-server="' + id + '"] .server-dot';
    var sidebarDot = document.querySelector(sel);
    if (sidebarDot) sidebarDot.className = cls;
    var tabSel = '.server-tab[data-server="' + id + '"] .server-dot';
    var tabDot = document.querySelector(tabSel);
    if (tabDot) tabDot.className = cls;
}

function updateAllDots(servers) {
    for (var i = 0; i < servers.length; i++) {
        updateServerDot(servers[i].id, servers[i]);
    }
}

var statusPollTimer = null;

function startStatusPolling() {
    stopStatusPolling();
    statusPollTimer = setInterval(pollServerStates, 2500);
    pollServerStates();
}

function stopStatusPolling() {
    if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
}

async function pollServerStates() {
    try {
        var r = await apiFetch('/admin/instances');
        if (!r) return;
        var servers = await r.json();
        updateAllDots(servers);
        if (currentServerId) {
            for (var i = 0; i < servers.length; i++) {
                if (servers[i].id === currentServerId) {
                    if (typeof applyServerStatus === 'function') applyServerStatus(servers[i]);
                    break;
                }
            }
        }
    } catch (e) {}
}

function renderSidebarServers(servers) {
    var list = document.getElementById('sidebarServerList');
    list.innerHTML = '';
    if (!servers.length) {
        list.innerHTML = '<div class="nav-item nav-placeholder">No servers</div>';
        return;
    }
    var groups = {};
    for (var i = 0; i < servers.length; i++) {
        var s = servers[i];
        var pid = s.project_id || '';
        if (!groups[pid]) {
            groups[pid] = { name: s.project_name || (pid ? pid : 'No Project'), servers: [] };
        }
        groups[pid].servers.push(s);
    }
    var keys = Object.keys(groups).sort(function (a, b) {
        return groups[a].name.localeCompare(groups[b].name);
    });
    for (var k = 0; k < keys.length; k++) {
        var pid = keys[k];
        var g = groups[pid];
        var gid = pid || '_none';
        if (expandedProjects[gid] === undefined) expandedProjects[gid] = true;
        var group = document.createElement('div');
        group.className = 'tree-group';
        group.setAttribute('data-group', gid);
        var header = document.createElement('button');
        header.className = 'nav-item tree-group-header';
        header.onclick = (function (id) { return function () { toggleProjectGroup(id); }; })(gid);
        header.innerHTML = '<span class="tree-arrow">' + (expandedProjects[gid] ? '▾' : '▸') + '</span>' +
            '<span class="tree-group-name">' + esc(g.name) + '</span>' +
            '<span class="tree-count">' + g.servers.length + '</span>';
        group.appendChild(header);
        var children = document.createElement('div');
        children.className = 'tree-group-children' + (expandedProjects[gid] ? '' : ' collapsed');
        for (var j = 0; j < g.servers.length; j++) {
            var s = g.servers[j];
            var btn = document.createElement('button');
            btn.className = 'nav-item sidebar-server-item' + (currentServerId === s.id ? ' active' : '');
            btn.setAttribute('data-server', s.id);
            btn.onclick = (function (id) { return function () { selectServer(id); }; })(s.id);
            var modpackTag = s.modpack_name ? '<span class="tag tag-file" style="margin-left:6px;font-size:10px">' + esc(s.modpack_name) + '</span>' : '';
            btn.innerHTML = '<span class="server-dot ' + serverDotClass(s) + '"></span>' + esc(s.name || s.id) + modpackTag;
            children.appendChild(btn);
        }
        group.appendChild(children);
        list.appendChild(group);
    }
}

function toggleProjectGroup(gid) {
    expandedProjects[gid] = !expandedProjects[gid];
    var group = document.querySelector('.tree-group[data-group="' + gid + '"]');
    if (!group) return;
    var arrow = group.querySelector('.tree-arrow');
    if (arrow) arrow.textContent = expandedProjects[gid] ? '▾' : '▸';
    var children = group.querySelector('.tree-group-children');
    if (!children) return;
    if (expandedProjects[gid]) {
        children.style.height = '0px';
        children.classList.remove('collapsed');
        void children.offsetHeight;
        children.style.height = children.scrollHeight + 'px';
        var fin = function () { children.style.height = 'auto'; children.removeEventListener('transitionend', fin); };
        children.addEventListener('transitionend', fin);
        setTimeout(fin, 300);
    } else {
        children.style.height = children.scrollHeight + 'px';
        void children.offsetHeight;
        children.style.height = '0px';
        var done = function () { children.classList.add('collapsed'); children.removeEventListener('transitionend', done); };
        children.addEventListener('transitionend', done);
        setTimeout(done, 300);
    }
}

function renderServerTabs(servers) {
    var bar = document.getElementById('serverTabBar');
    var tabs = document.getElementById('serverTabs');
    tabs.innerHTML = '';
    if (!servers.length) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    for (var i = 0; i < servers.length; i++) {
        var s = servers[i];
        var tab = document.createElement('button');
        tab.className = 'server-tab' + (currentServerId === s.id ? ' active' : '');
        tab.setAttribute('data-server', s.id);
        tab.onclick = function (id) { return function () { selectServer(id); }; }(s.id);
        tab.innerHTML = '<span class="server-dot ' + serverDotClass(s) + '"></span>' + esc(s.name || s.id);
        tabs.appendChild(tab);
    }
    updateTabIndicator(tabs);
}

async function switchToServer(sid) {
    switchTab('servers');
    setTimeout(function() { selectServer(sid); }, 100);
}

window.addEventListener('resize', function () {
    updateTabIndicator(document.getElementById('serverTabs'));
    var nav = document.querySelector('#serverDetailView .server-sub-nav');
    if (nav) updateTabIndicator(nav);
    var mpNav = document.querySelector('#pdMpDetail .server-sub-nav');
    if (mpNav) updateTabIndicator(mpNav);
});
