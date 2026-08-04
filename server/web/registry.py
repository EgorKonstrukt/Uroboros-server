from typing import List

from server.plugins import TabDef


BUILTIN_TABS = [
    TabDef(id="projects", title="Projects", group="GENERAL", order=10,
           fragment="projects.html", loader="loadProjects"),
    TabDef(id="players", title="Players", group="GENERAL", order=20,
           fragment="players.html", loader="loadPlayers"),
    TabDef(id="config", title="Config", group="GENERAL", order=30,
           fragment="config.html", loader="loadGlobalConfig"),
    TabDef(id="java", title="Java", group="GENERAL", order=40,
           fragment="java.html", loader="loadJava"),
    TabDef(id="update", title="Update", group="GENERAL", order=50,
           fragment="update.html", loader="loadUpdateStatus"),
    TabDef(id="plugins", title="Plugins", group="GENERAL", order=60,
           fragment="plugins.html", loader="loadPlugins"),
    TabDef(id="servers", title="Servers", group="SERVERS", order=10,
           fragment="servers.html", in_nav=False),
]


def get_all_tabs() -> List[TabDef]:
    from server.plugins import get_tabs

    tabs = list(BUILTIN_TABS) + get_tabs()
    return sorted(tabs, key=lambda t: (t.group, t.order, t.title))
