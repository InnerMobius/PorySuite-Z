// PorySuite-Z Bridge Script for Porymap
//
// This script runs inside Porymap's JavaScript engine. It listens to a
// minimal set of callbacks and writes JSON messages to a bridge file that
// PorySuite-Z watches. It also polls for commands written by PorySuite-Z
// and executes them (currently: open a specific map).
//
// Stock Porymap callbacks used (work without patches):
//   onProjectOpened     — register actions + start command poll
//   onMapOpened         — tell PorySuite the current map
//   onBlockHoverChanged — track cursor position for Ctrl+E edit requests
//
// Patched callbacks used (require our C++ additions):
//   onEventSelected     — user clicked an event in Porymap
//   onMapSaved          — user saved the map
//
// Everything else Porymap emits is intentionally NOT handled — adding
// callbacks with no PorySuite-Z consumer is dead weight.

let currentMap = "";
let lastHoverX = 0;
let lastHoverY = 0;
let commandPollActive = false;

// ═══════════════════════════════════════════════════════════════════════════
// Callbacks
// ═══════════════════════════════════════════════════════════════════════════

export function onProjectOpened(projectPath) {
    utility.log("[PorySuite-Z Bridge] Project opened: " + projectPath);
    // Register PorySuite-Z actions in Porymap's Tools > Custom Actions menu
    utility.registerAction("editInPorySuite", "Edit in PorySuite-Z", "Ctrl+E");
    utility.registerAction("syncToPorySuite", "Sync Map to PorySuite-Z", "Ctrl+Shift+E");
    // Start polling for commands from PorySuite-Z
    utility.log("[PorySuite-Z Bridge] Starting command poll...");
    startCommandPoll();
}

export function onMapOpened(mapName) {
    currentMap = mapName;
    writeBridge({type: "map_opened", map: mapName});
}

export function onBlockHoverChanged(x, y) {
    // Track cursor position so editInPorySuite knows where the user was pointing.
    // Intentionally does not write to the bridge — way too noisy.
    lastHoverX = x;
    lastHoverY = y;
}

export function onEventSelected(eventType, eventIndex, scriptLabel, x, y) {
    writeBridge({
        type: "event_selected",
        map: currentMap,
        eventType: eventType,
        eventIndex: eventIndex,
        script: scriptLabel,
        x: x,
        y: y
    });
}

export function onMapSaved(mapName) {
    writeBridge({type: "map_saved", map: mapName});
}

// ═══════════════════════════════════════════════════════════════════════════
// User-triggered actions (registered in Porymap's Tools menu)
// ═══════════════════════════════════════════════════════════════════════════

export function editInPorySuite() {
    // Ctrl+E — send current hover position so PorySuite-Z can look up the event
    writeBridge({type: "edit_request", map: currentMap, x: lastHoverX, y: lastHoverY});
}

export function syncToPorySuite() {
    // Ctrl+Shift+E — ask PorySuite-Z to focus the current map
    writeBridge({type: "sync_request", map: currentMap});
}

// ═══════════════════════════════════════════════════════════════════════════
// Bridge writer
// ═══════════════════════════════════════════════════════════════════════════

function writeBridge(data) {
    data.timestamp = Date.now();
    try {
        // Patched Q_INVOKABLE: writes JSON to porysuite_bridge.json in the project root
        utility.writeBridgeFile(JSON.stringify(data));
    } catch(e) {
        // Fallback for an unpatched Porymap: log so it's visible at least
        utility.log("PSBRIDGE:" + JSON.stringify(data));
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Command channel — PorySuite-Z writes commands, we execute them
// ═══════════════════════════════════════════════════════════════════════════

function startCommandPoll() {
    if (commandPollActive) return;
    commandPollActive = true;
    pollForCommand();
}

let pollErrorCount = 0;
function pollForCommand() {
    // If readCommandFile isn't patched into this Porymap build, bail out so
    // we don't log-spam forever.
    if (typeof utility.readCommandFile !== "function") {
        utility.log("[PorySuite-Z Bridge] readCommandFile not available — "
                    + "this Porymap is unpatched. Command poll disabled.");
        commandPollActive = false;
        return;
    }
    try {
        // Read command file (written by PorySuite-Z, deleted by the C++ reader)
        let raw = utility.readCommandFile();
        if (raw && raw.length > 0) {
            utility.log("[PorySuite-Z Bridge] Got command: " + raw);
            handleCommand(JSON.parse(raw));
        }
        pollErrorCount = 0;
    } catch(e) {
        pollErrorCount += 1;
        if (pollErrorCount <= 3) {
            utility.log("[PorySuite-Z Bridge] Poll error: " + e);
        }
        if (pollErrorCount >= 10) {
            utility.log("[PorySuite-Z Bridge] Too many poll errors — "
                        + "disabling command poll.");
            commandPollActive = false;
            return;
        }
    }
    // Poll every 500ms. `utility.setTimeout` is Porymap's scripting API
    // (see Porymap docs — utility object exposes setTimeout/setInterval).
    utility.setTimeout(pollForCommand, 500);
}

function handleCommand(cmd) {
    if (!cmd || !cmd.action) return;
    utility.log("[PorySuite-Z Bridge] Handling command: " + cmd.action
                + " map=" + (cmd.map || ""));
    if (cmd.action === "openMap" && cmd.map) {
        // map.openMap() is a Q_INVOKABLE we added to MainWindow
        let ok = map.openMap(cmd.map);
        utility.log("[PorySuite-Z Bridge] openMap result: " + ok);
        if (!ok) {
            utility.warn("PorySuite-Z: Could not open map '" + cmd.map + "'");
        }
    }
}
