"""
PorySuite-Z Porymap Patcher — applies source modifications to Porymap.

Instead of fragile .patch files that break on upstream line changes, this
script uses search-and-replace to inject our additions into the C++ source.
Each patch function finds a known anchor string and inserts code relative
to it, making the patches resilient to unrelated changes in Porymap.

Patches applied:
    1. New callback types (11) in scripting.h and scripting.cpp
    2. writeBridgeFile() and query functions in scriptutility.h/.cpp
    3. Callback invocation hooks in mainwindow.cpp and editor.cpp

Usage:
    python apply_patches.py <porymap_source_dir>

The script is idempotent — running it twice won't double-apply patches.
Each patch checks for a sentinel string before applying.
"""

import os
import sys

SENTINEL = "// PORYSUITE-Z PATCHED"


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _insert_after(content: str, anchor: str, insertion: str, *, label: str = "") -> str:
    """Insert text after the first occurrence of anchor."""
    idx = content.find(anchor)
    if idx < 0:
        raise RuntimeError(f"Anchor not found{f' ({label})' if label else ''}: {anchor[:80]!r}")
    pos = idx + len(anchor)
    return content[:pos] + insertion + content[pos:]


def _insert_before(content: str, anchor: str, insertion: str, *, label: str = "") -> str:
    """Insert text before the first occurrence of anchor."""
    idx = content.find(anchor)
    if idx < 0:
        raise RuntimeError(f"Anchor not found{f' ({label})' if label else ''}: {anchor[:80]!r}")
    return content[:idx] + insertion + content[idx:]


def _is_patched(content: str) -> bool:
    return SENTINEL in content


# ═════════════════════════════════════════════════════════════════════════════
# Patch 1: Add callback types to scripting.h
# ═════════════════════════════════════════════════════════════════════════════

def patch_scripting_h(src_dir: str):
    path = os.path.join(src_dir, "include", "scripting.h")
    content = _read(path)
    if _is_patched(content):
        return

    # ── Add new enum values ─────────────────────────────────────────────────
    content = _insert_after(
        content,
        "OnBorderVisibilityToggled,",
        f"""
    {SENTINEL}
    OnEventSelected,
    OnEventCreated,
    OnEventDeleted,
    OnEventMoved,
    OnMapSaved,
    OnLayoutSaved,
    OnConnectionChanged,
    OnWildEncountersSaved,
    OnHealLocationChanged,
    OnMapHeaderChanged,
    OnTilesetChanged,""",
        label="CallbackType enum",
    )

    # ── Add callback declarations (QT_QML_LIB section) ─────────────────────
    content = _insert_after(
        content,
        "static void cb_BorderVisibilityToggled(bool visible);",
        """
    // PorySuite-Z callbacks
    static void cb_EventSelected(const QString &eventType, int eventIndex, const QString &scriptLabel, int x, int y);
    static void cb_EventCreated(const QString &eventType, int eventIndex);
    static void cb_EventDeleted(const QString &eventType, int eventIndex);
    static void cb_EventMoved(const QString &eventType, int eventIndex, int oldX, int oldY, int newX, int newY);
    static void cb_MapSaved(const QString &mapName);
    static void cb_LayoutSaved(const QString &layoutId);
    static void cb_ConnectionChanged(const QString &mapName, const QString &direction, const QString &targetMap);
    static void cb_WildEncountersSaved(const QString &mapName);
    static void cb_HealLocationChanged(const QString &mapName, int x, int y);
    static void cb_MapHeaderChanged(const QString &mapName, const QString &property, const QString &value);
    static void cb_TilesetChanged(const QString &primaryTileset, const QString &secondaryTileset);""",
        label="callback declarations",
    )

    # ── Add no-op stubs (non-QML section) ───────────────────────────────────
    content = _insert_after(
        content,
        "static void cb_BorderVisibilityToggled(bool) {};",
        """
    static void cb_EventSelected(const QString &, int, const QString &, int, int) {};
    static void cb_EventCreated(const QString &, int) {};
    static void cb_EventDeleted(const QString &, int) {};
    static void cb_EventMoved(const QString &, int, int, int, int, int) {};
    static void cb_MapSaved(const QString &) {};
    static void cb_LayoutSaved(const QString &) {};
    static void cb_ConnectionChanged(const QString &, const QString &, const QString &) {};
    static void cb_WildEncountersSaved(const QString &) {};
    static void cb_HealLocationChanged(const QString &, int, int) {};
    static void cb_MapHeaderChanged(const QString &, const QString &, const QString &) {};
    static void cb_TilesetChanged(const QString &, const QString &) {};""",
        label="no-op stubs",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 2: Add callback implementations to scripting.cpp
# ═════════════════════════════════════════════════════════════════════════════

def patch_scripting_cpp(src_dir: str):
    path = os.path.join(src_dir, "src", "scriptapi", "scripting.cpp")
    content = _read(path)
    if _is_patched(content):
        return

    # ── Add callback function name mappings ─────────────────────────────────
    content = _insert_after(
        content,
        '{OnBorderVisibilityToggled, "onBorderVisibilityToggled"},',
        f"""
    {SENTINEL}
    {{OnEventSelected, "onEventSelected"}},
    {{OnEventCreated, "onEventCreated"}},
    {{OnEventDeleted, "onEventDeleted"}},
    {{OnEventMoved, "onEventMoved"}},
    {{OnMapSaved, "onMapSaved"}},
    {{OnLayoutSaved, "onLayoutSaved"}},
    {{OnConnectionChanged, "onConnectionChanged"}},
    {{OnWildEncountersSaved, "onWildEncountersSaved"}},
    {{OnHealLocationChanged, "onHealLocationChanged"}},
    {{OnMapHeaderChanged, "onMapHeaderChanged"}},
    {{OnTilesetChanged, "onTilesetChanged"}},""",
        label="callbackFunctions map",
    )

    # ── Add callback implementations (before fromBlock) ─────────────────────
    content = _insert_before(
        content,
        "QJSValue Scripting::fromBlock(Block block) {",
        """
// ═══════════════════════════════════════════════════════════════════════════
// PorySuite-Z event & save callbacks
// ═══════════════════════════════════════════════════════════════════════════

void Scripting::cb_EventSelected(const QString &eventType, int eventIndex,
                                  const QString &scriptLabel, int x, int y) {
    if (!instance) return;
    QJSValueList args {eventType, eventIndex, scriptLabel, x, y};
    instance->invokeCallback(OnEventSelected, args);
}

void Scripting::cb_EventCreated(const QString &eventType, int eventIndex) {
    if (!instance) return;
    QJSValueList args {eventType, eventIndex};
    instance->invokeCallback(OnEventCreated, args);
}

void Scripting::cb_EventDeleted(const QString &eventType, int eventIndex) {
    if (!instance) return;
    QJSValueList args {eventType, eventIndex};
    instance->invokeCallback(OnEventDeleted, args);
}

void Scripting::cb_EventMoved(const QString &eventType, int eventIndex,
                               int oldX, int oldY, int newX, int newY) {
    if (!instance) return;
    QJSValueList args {eventType, eventIndex, oldX, oldY, newX, newY};
    instance->invokeCallback(OnEventMoved, args);
}

void Scripting::cb_MapSaved(const QString &mapName) {
    if (!instance) return;
    QJSValueList args {mapName};
    instance->invokeCallback(OnMapSaved, args);
}

void Scripting::cb_LayoutSaved(const QString &layoutId) {
    if (!instance) return;
    QJSValueList args {layoutId};
    instance->invokeCallback(OnLayoutSaved, args);
}

void Scripting::cb_ConnectionChanged(const QString &mapName,
                                      const QString &direction,
                                      const QString &targetMap) {
    if (!instance) return;
    QJSValueList args {mapName, direction, targetMap};
    instance->invokeCallback(OnConnectionChanged, args);
}

void Scripting::cb_WildEncountersSaved(const QString &mapName) {
    if (!instance) return;
    QJSValueList args {mapName};
    instance->invokeCallback(OnWildEncountersSaved, args);
}

void Scripting::cb_HealLocationChanged(const QString &mapName, int x, int y) {
    if (!instance) return;
    QJSValueList args {mapName, x, y};
    instance->invokeCallback(OnHealLocationChanged, args);
}

void Scripting::cb_MapHeaderChanged(const QString &mapName,
                                     const QString &property,
                                     const QString &value) {
    if (!instance) return;
    QJSValueList args {mapName, property, value};
    instance->invokeCallback(OnMapHeaderChanged, args);
}

void Scripting::cb_TilesetChanged(const QString &primaryTileset,
                                   const QString &secondaryTileset) {
    if (!instance) return;
    QJSValueList args {primaryTileset, secondaryTileset};
    instance->invokeCallback(OnTilesetChanged, args);
}

""",
        label="callback implementations",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 3: Add writeBridgeFile to ScriptUtility
# ═════════════════════════════════════════════════════════════════════════════

def patch_scriptutility_h(src_dir: str):
    path = os.path.join(src_dir, "include", "scriptutility.h")
    content = _read(path)
    if _is_patched(content):
        return

    # Add writeBridgeFile and query functions after the last Q_INVOKABLE
    content = _insert_after(
        content,
        "Q_INVOKABLE bool isSecondaryTileset(QString tilesetName);",
        f"""
    {SENTINEL}
    Q_INVOKABLE void writeBridgeFile(QString jsonData);
    Q_INVOKABLE QString readCommandFile();
    Q_INVOKABLE QJSValue getMapHeader();
    Q_INVOKABLE QJSValue getCurrentTilesets();
    Q_INVOKABLE QJSValue getMapConnections();
    Q_INVOKABLE QJSValue getMapEvents();""",
        label="Q_INVOKABLE declarations",
    )

    _write(path, content)
    print(f"  Patched {path}")


def patch_apiutility_cpp(src_dir: str):
    path = os.path.join(src_dir, "src", "scriptapi", "apiutility.cpp")
    content = _read(path)
    if _is_patched(content):
        return

    # Need QFile for writeBridgeFile (mainwindow.h already pulls in editor.h -> project.h)
    if "#include <QFile>" not in content:
        content = _insert_after(
            content,
            '#include "config.h"',
            "\n#include <QFile>",
            label="QFile include",
        )

    # Add implementations before the final #endif
    content = _insert_before(
        content,
        "\n#endif // QT_QML_LIB",
        f"""
{SENTINEL}
// ═══════════════════════════════════════════════════════════════════════════
// PorySuite-Z bridge API
// ═══════════════════════════════════════════════════════════════════════════

QString ScriptUtility::readCommandFile() {{
    if (!window || !window->editor || !window->editor->project)
        return QString();
    QString projectDir = window->editor->project->root;
    if (projectDir.isEmpty()) return QString();

    QString cmdPath = projectDir + "/porysuite_command.json";
    QFile file(cmdPath);
    if (!file.exists()) return QString();
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return QString();
    QString contents = QString::fromUtf8(file.readAll());
    file.close();
    file.remove();
    return contents;
}}

void ScriptUtility::writeBridgeFile(QString jsonData) {{
    // Write JSON data to porysuite_bridge.json in the project root
    if (!window) return;
    QString projectDir;
    if (window->editor && window->editor->project) {{
        projectDir = window->editor->project->root;
    }}
    if (projectDir.isEmpty()) return;

    QString bridgePath = projectDir + "/porysuite_bridge.json";
    QFile file(bridgePath);
    if (file.open(QIODevice::WriteOnly | QIODevice::Text)) {{
        file.write(jsonData.toUtf8());
        file.close();
    }}
}}

QJSValue ScriptUtility::getMapHeader() {{
    if (!window || !window->editor || !window->editor->map)
        return QJSValue();
    auto engine = Scripting::getEngine();
    if (!engine) return QJSValue();

    Map *map = window->editor->map.data();
    auto *hdr = map->header();
    QJSValue obj = engine->newObject();
    obj.setProperty("name", map->name());
    if (hdr) {{
        obj.setProperty("song", hdr->song());
        obj.setProperty("location", hdr->location());
        obj.setProperty("requiresFlash", hdr->requiresFlash());
        obj.setProperty("weather", hdr->weather());
        obj.setProperty("type", hdr->type());
        obj.setProperty("battleScene", hdr->battleScene());
        obj.setProperty("showsLocationName", hdr->showsLocationName());
        obj.setProperty("allowsRunning", hdr->allowsRunning());
        obj.setProperty("allowsBiking", hdr->allowsBiking());
        obj.setProperty("allowsEscaping", hdr->allowsEscaping());
        obj.setProperty("floorNumber", hdr->floorNumber());
    }}
    return obj;
}}

QJSValue ScriptUtility::getCurrentTilesets() {{
    if (!window || !window->editor || !window->editor->map)
        return QJSValue();
    auto engine = Scripting::getEngine();
    if (!engine) return QJSValue();

    QJSValue obj = engine->newObject();
    auto *layout = window->editor->map.data()->layout();
    if (layout) {{
        obj.setProperty("primary", layout->tileset_primary_label);
        obj.setProperty("secondary", layout->tileset_secondary_label);
    }}
    return obj;
}}

QJSValue ScriptUtility::getMapConnections() {{
    if (!window || !window->editor || !window->editor->map)
        return QJSValue();
    auto engine = Scripting::getEngine();
    if (!engine) return QJSValue();

    Map *map = window->editor->map.data();
    QJSValue arr = engine->newArray();
    int idx = 0;
    for (auto *conn : map->getConnections()) {{
        QJSValue connObj = engine->newObject();
        connObj.setProperty("direction", conn->direction());
        connObj.setProperty("map", conn->targetMapName());
        connObj.setProperty("offset", conn->offset());
        arr.setProperty(idx++, connObj);
    }}
    return arr;
}}

QJSValue ScriptUtility::getMapEvents() {{
    if (!window || !window->editor || !window->editor->map)
        return QJSValue();
    auto engine = Scripting::getEngine();
    if (!engine) return QJSValue();

    Map *map = window->editor->map.data();
    QJSValue arr = engine->newArray();
    int idx = 0;
    for (auto *event : map->getEvents()) {{
        QJSValue eObj = engine->newObject();
        eObj.setProperty("type", Event::typeToString(event->getEventType()));
        eObj.setProperty("group", Event::groupToString(event->getEventGroup()));
        eObj.setProperty("index", event->getEventIndex());
        eObj.setProperty("x", event->getX());
        eObj.setProperty("y", event->getY());
        // Use getScripts() — returns the script labels for this event type
        QStringList scripts = event->getScripts();
        if (!scripts.isEmpty() && !scripts.first().isEmpty()) {{
            eObj.setProperty("script", scripts.first());
        }}
        arr.setProperty(idx++, eObj);
    }}
    return arr;
}}
""",
        label="bridge API implementations",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 4: Wire callbacks into MainWindow save and event selection
# ═════════════════════════════════════════════════════════════════════════════

def patch_mainwindow_cpp(src_dir: str):
    path = os.path.join(src_dir, "src", "mainwindow.cpp")
    content = _read(path)
    if _is_patched(content):
        return

    # ── Add map save callback after successful save ─────────────────────────
    # Anchor: the one-time reload message check — unique to save()
    content = _insert_before(
        content,
        "if (success && !porymapConfig.shownInGameReloadMessage)",
        f"""    {SENTINEL}
    // Notify scripts that the map was saved
    if (success && this->editor && this->editor->map) {{
        Scripting::cb_MapSaved(this->editor->map.data()->name());
    }}

    """,
        label="save callback",
    )

    # ── Add event selection callback ────────────────────────────────────────
    # The updateSelectedEvents method runs whenever event selection changes.
    # We hook after the single-event selection to notify scripts.
    # Find the line after the single-event setup (after isProgrammaticEventTabChange = true)
    # Insert at the end of the single-event case block
    content = _insert_after(
        content,
        'this->isProgrammaticEventTabChange = true;',
        """
    // PorySuite-Z: notify scripts of event selection
    if (events.length() == 1) {
        Event *ev = events.constFirst();
        if (ev) {
            QString typeStr = Event::typeToString(ev->getEventType());
            QString scriptLabel;
            QStringList scripts = ev->getScripts();
            if (!scripts.isEmpty()) scriptLabel = scripts.first();
            Scripting::cb_EventSelected(typeStr, ev->getEventIndex(),
                                         scriptLabel, ev->getX(), ev->getY());
        }
    }""",
        label="event selection callback",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 5: Add Q_INVOKABLE openMap to MainWindow for script-based navigation
# ═════════════════════════════════════════════════════════════════════════════

def patch_mainwindow_h(src_dir: str):
    """Add Q_INVOKABLE openMap() to MainWindow so scripts can navigate maps."""
    path = os.path.join(src_dir, "include", "mainwindow.h")
    content = _read(path)
    if "Q_INVOKABLE bool openMap" in content:
        return  # Already has it

    # Add after the last Q_INVOKABLE in the public section
    content = _insert_after(
        content,
        "Q_INVOKABLE void setFloorNumber(int floorNumber);",
        """    // PorySuite-Z: allow scripts to navigate to a different map
    Q_INVOKABLE bool openMap(const QString &mapName);""",
        label="openMap declaration",
    )
    _write(path, content)
    print(f"  Patched {path}")


def patch_mainwindow_openmap(src_dir: str):
    """Add openMap() implementation to mainwindow.cpp."""
    path = os.path.join(src_dir, "src", "mainwindow.cpp")
    content = _read(path)
    if "MainWindow::openMap(const QString" in content:
        return  # Already has it

    # Add the implementation before setLayoutOnlyMode
    content = _insert_before(
        content,
        "// These parts of the UI only make sense when editing maps.",
        """// PorySuite-Z: public Q_INVOKABLE for scripts to navigate to a map
bool MainWindow::openMap(const QString &mapName) {
    return setMap(mapName);
}

""",
        label="openMap implementation",
    )
    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 6: Add CLI project path argument support to main.cpp
# ═════════════════════════════════════════════════════════════════════════════

def patch_main_cpp(src_dir: str):
    """Patch main.cpp to accept a project directory as a CLI argument.

    Usage: porymap.exe [project_dir]
    When a project directory is passed, Porymap writes it as the most recent
    project in porymap.cfg so it auto-opens on this launch.
    """
    path = os.path.join(src_dir, "src", "main.cpp")
    content = _read(path)
    if _is_patched(content):
        return

    # Add includes at the top
    content = _insert_after(
        content,
        '#include <QApplication>',
        f"""
{SENTINEL}
#include <QDir>
#include "config.h"
""",
        label="main.cpp includes",
    )

    # Add CLI arg handling before MainWindow construction
    # Must set org/app name first so config paths resolve correctly
    content = _insert_before(
        content,
        'porysplash = new PorymapLoadingScreen;',
        """    // PorySuite-Z: accept CLI arguments: porymap.exe [project_dir] [map_name]
    // Set names first so QStandardPaths resolves to pret/porymap/
    QCoreApplication::setOrganizationName("pret");
    QCoreApplication::setApplicationName("porymap");
    QStringList args = a.arguments();
    if (args.size() > 1) {
        QString projectDir = QDir::cleanPath(args.at(1));
        if (QDir(projectDir).exists()) {
            // Force this project to open on launch
            porymapConfig.load();
            porymapConfig.addRecentProject(projectDir);
            porymapConfig.projectManuallyClosed = false;
            porymapConfig.reopenOnLaunch = true;
            porymapConfig.save();

            // If a map name was also passed, write it to the user config
            if (args.size() > 2) {
                QString mapName = args.at(2);
                userConfig.load(projectDir);
                userConfig.recentMapOrLayout = mapName;
                userConfig.save();
            }
        }
    }

""",
        label="CLI project arg",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def apply_all(src_dir: str):
    """Apply all PorySuite-Z patches to a Porymap source directory."""
    if not os.path.isdir(os.path.join(src_dir, "include")):
        print(f"ERROR: {src_dir} does not look like a Porymap source directory")
        sys.exit(1)

    print(f"Applying PorySuite-Z patches to {src_dir}...")

    patch_scripting_h(src_dir)
    patch_scripting_cpp(src_dir)
    patch_scriptutility_h(src_dir)
    patch_apiutility_cpp(src_dir)
    patch_mainwindow_cpp(src_dir)
    patch_mainwindow_h(src_dir)
    patch_mainwindow_openmap(src_dir)
    patch_main_cpp(src_dir)

    print("All patches applied successfully!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <porymap_source_dir>")
        sys.exit(1)
    apply_all(sys.argv[1])
