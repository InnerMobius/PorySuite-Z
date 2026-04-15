"""
PorySuite-Z Porymap Patcher — applies source modifications to Porymap.

Instead of fragile .patch files that break on upstream line changes, this
script uses search-and-replace to inject our additions into the C++ source.
Each patch function finds a known anchor string and inserts code relative
to it, making the patches resilient to unrelated changes in Porymap.

Patches applied:
    1. Callback types (onEventSelected, onMapSaved) in scripting.h/.cpp
    2. writeBridgeFile() + readCommandFile() bridge I/O in
       scriptutility.h / apiutility.cpp
    3. Callback invocation hooks in mainwindow.cpp (save + event selection)
    4. openMap() Q_INVOKABLE on MainWindow for script-based navigation
    5. CLI project path argument support in main.cpp

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


def _check_anchor(content: str, anchor: str, label: str):
    """Verify anchor appears exactly once. Fail loudly if 0 or >1 matches.

    This guards against silent mis-patching if upstream Porymap ever adds a
    second match (e.g. a comment or a similarly-named symbol). Better to
    break the patcher at apply time than to emit a broken Porymap build.
    """
    count = content.count(anchor)
    if count == 0:
        raise RuntimeError(f"Anchor not found ({label}): {anchor[:80]!r}")
    if count > 1:
        raise RuntimeError(
            f"Anchor ambiguous ({label}): matches {count} locations — "
            f"upstream Porymap may have changed. Anchor: {anchor[:80]!r}")


def _insert_after(content: str, anchor: str, insertion: str, *, label: str = "") -> str:
    """Insert text after the single occurrence of anchor."""
    _check_anchor(content, anchor, label)
    idx = content.find(anchor)
    pos = idx + len(anchor)
    return content[:pos] + insertion + content[pos:]


def _insert_before(content: str, anchor: str, insertion: str, *, label: str = "") -> str:
    """Insert text before the single occurrence of anchor."""
    _check_anchor(content, anchor, label)
    idx = content.find(anchor)
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
    OnMapSaved,""",
        label="CallbackType enum",
    )

    # ── Add callback declarations (QT_QML_LIB section) ─────────────────────
    content = _insert_after(
        content,
        "static void cb_BorderVisibilityToggled(bool visible);",
        """
    // PorySuite-Z callbacks
    static void cb_EventSelected(const QString &eventType, int eventIndex, const QString &scriptLabel, int x, int y);
    static void cb_MapSaved(const QString &mapName);""",
        label="callback declarations",
    )

    # ── Add no-op stubs (non-QML section) ───────────────────────────────────
    content = _insert_after(
        content,
        "static void cb_BorderVisibilityToggled(bool) {};",
        """
    static void cb_EventSelected(const QString &, int, const QString &, int, int) {};
    static void cb_MapSaved(const QString &) {};""",
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
    {{OnMapSaved, "onMapSaved"}},""",
        label="callbackFunctions map",
    )

    # ── Add callback implementations (before fromBlock) ─────────────────────
    content = _insert_before(
        content,
        "QJSValue Scripting::fromBlock(Block block) {",
        """
// PorySuite-Z: event selection and map save callbacks

void Scripting::cb_EventSelected(const QString &eventType, int eventIndex,
                                  const QString &scriptLabel, int x, int y) {
    if (!instance) return;
    QJSValueList args {eventType, eventIndex, scriptLabel, x, y};
    instance->invokeCallback(OnEventSelected, args);
}

void Scripting::cb_MapSaved(const QString &mapName) {
    if (!instance) return;
    QJSValueList args {mapName};
    instance->invokeCallback(OnMapSaved, args);
}

""",
        label="callback implementations",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 3: Add bridge communication functions to ScriptUtility
# ═════════════════════════════════════════════════════════════════════════════

def patch_scriptutility_h(src_dir: str):
    path = os.path.join(src_dir, "include", "scriptutility.h")
    content = _read(path)
    if _is_patched(content):
        return

    content = _insert_after(
        content,
        "Q_INVOKABLE bool isSecondaryTileset(QString tilesetName);",
        f"""
    {SENTINEL}
    Q_INVOKABLE void writeBridgeFile(QString jsonData);
    Q_INVOKABLE QString readCommandFile();""",
        label="Q_INVOKABLE declarations",
    )

    _write(path, content)
    print(f"  Patched {path}")


def patch_apiutility_cpp(src_dir: str):
    path = os.path.join(src_dir, "src", "scriptapi", "apiutility.cpp")
    content = _read(path)
    if _is_patched(content):
        return

    if "#include <QFile>" not in content:
        content = _insert_after(
            content,
            '#include "config.h"',
            "\n#include <QFile>",
            label="QFile include",
        )

    content = _insert_before(
        content,
        "\n#endif // QT_QML_LIB",
        f"""
{SENTINEL}
// PorySuite-Z bridge I/O: JS writes messages out, reads commands in.

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
""",
        label="bridge API implementations",
    )

    _write(path, content)
    print(f"  Patched {path}")


# ═════════════════════════════════════════════════════════════════════════════
# Patch 4: Wire callbacks into MainWindow (save + event selection)
# ═════════════════════════════════════════════════════════════════════════════

def patch_mainwindow_cpp(src_dir: str):
    path = os.path.join(src_dir, "src", "mainwindow.cpp")
    content = _read(path)
    if _is_patched(content):
        return

    # ── Add map save callback after successful save ─────────────────────────
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
        return

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
        return

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
