// PorySuite-Z Bridge Script for Porymap
//
// This script runs inside Porymap's JavaScript engine. It listens to all
// available callbacks and writes structured JSON messages to a bridge file
// that PorySuite-Z watches.
//
// Stock Porymap callbacks (work without patches):
//   onProjectOpened, onProjectClosed, onMapOpened, onMainTabChanged,
//   onMapViewTabChanged, onBlockHoverChanged, onBlockHoverCleared,
//   onTilesetUpdated, onMapResized, onBorderResized, onMapShifted,
//   onBorderVisibilityToggled
//
// Patched callbacks (require our C++ additions):
//   onEventSelected, onEventCreated, onEventDeleted, onEventMoved,
//   onMapSaved, onLayoutSaved, onConnectionChanged,
//   onWildEncountersSaved, onHealLocationChanged, onMapHeaderChanged,
//   onTilesetChanged

let currentMap = "";
let currentProject = "";
let lastHoverX = 0;
let lastHoverY = 0;
let commandPollActive = false;

// ═══════════════════════════════════════════════════════════════════════════
// Stock Porymap callbacks (always available)
// ═══════════════════════════════════════════════════════════════════════════

export function onProjectOpened(projectPath) {
    currentProject = projectPath;
    utility.log("[PorySuite-Z Bridge] Project opened: " + projectPath);
    // Register PorySuite-Z actions in Porymap's Tools > Custom Actions menu
    utility.registerAction("editInPorySuite", "Edit in PorySuite-Z", "Ctrl+E");
    utility.registerAction("syncToPorySuite", "Sync Map to PorySuite-Z", "Ctrl+Shift+E");
    writeBridge({type: "project_opened", project: projectPath});
    // Start polling for commands from PorySuite-Z
    utility.log("[PorySuite-Z Bridge] Starting command poll...");
    startCommandPoll();
}

export function onProjectClosed(projectPath) {
    writeBridge({type: "project_closed"});
}

export function onMapOpened(mapName) {
    currentMap = mapName;
    // Try to send full context (patched functions); fall back gracefully
    let msg = {type: "map_opened", map: mapName};
    try { msg.header = utility.getMapHeader(); } catch(e) { msg.header = {}; }
    try { msg.tilesets = utility.getCurrentTilesets(); } catch(e) { msg.tilesets = {}; }
    try { msg.connections = utility.getMapConnections(); } catch(e) { msg.connections = []; }
    writeBridge(msg);
}

export function onMainTabChanged(oldTab, newTab) {
    // Tabs: 0=Map, 1=Events, 2=Header, 3=Connections, 4=WildPokemon
    writeBridge({type: "tab_changed", tab: newTab});
}

export function onMapViewTabChanged(oldTab, newTab) {
    // Map view tabs: 0=Metatiles, 1=Collision, 2=Prefabs
    writeBridge({type: "map_view_tab_changed", tab: newTab});
}

export function onBlockHoverChanged(x, y) {
    lastHoverX = x;
    lastHoverY = y;
    // Don't write to bridge on every hover — too noisy. Just track position.
}

export function onBlockHoverCleared() {
    // No action needed
}

export function onTilesetUpdated(tilesetName) {
    writeBridge({type: "tileset_updated", tileset: tilesetName});
}

export function onMapResized(oldWidth, oldHeight, delta) {
    writeBridge({
        type: "map_resized", map: currentMap,
        oldWidth: oldWidth, oldHeight: oldHeight, delta: delta
    });
}

export function onBorderResized(oldWidth, oldHeight, newWidth, newHeight) {
    writeBridge({
        type: "border_resized", map: currentMap,
        oldWidth: oldWidth, oldHeight: oldHeight,
        newWidth: newWidth, newHeight: newHeight
    });
}

export function onMapShifted(xDelta, yDelta) {
    writeBridge({type: "map_shifted", map: currentMap, xDelta: xDelta, yDelta: yDelta});
}

export function onBorderVisibilityToggled(visible) {
    // Informational only, no action needed on PorySuite-Z side
}

// ═══════════════════════════════════════════════════════════════════════════
// Patched callbacks (only fire if Porymap has our C++ patches applied)
// If these functions exist but the callbacks don't fire, they're harmless.
// ═══════════════════════════════════════════════════════════════════════════

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

export function onEventCreated(eventType, eventIndex) {
    writeBridge({
        type: "event_created", map: currentMap,
        eventType: eventType, eventIndex: eventIndex
    });
}

export function onEventDeleted(eventType, eventIndex) {
    writeBridge({
        type: "event_deleted", map: currentMap,
        eventType: eventType, eventIndex: eventIndex
    });
}

export function onEventMoved(eventType, eventIndex, oldX, oldY, newX, newY) {
    writeBridge({
        type: "event_moved", map: currentMap,
        eventType: eventType, eventIndex: eventIndex,
        oldX: oldX, oldY: oldY, newX: newX, newY: newY
    });
}

export function onMapSaved(mapName) {
    writeBridge({type: "map_saved", map: mapName});
}

export function onLayoutSaved(layoutId) {
    writeBridge({type: "layout_saved", layout: layoutId});
}

export function onConnectionChanged(mapName, direction, targetMap) {
    writeBridge({
        type: "connection_changed", map: mapName,
        direction: direction, target: targetMap
    });
}

export function onWildEncountersSaved(mapName) {
    writeBridge({type: "wild_encounters_saved", map: mapName});
}

export function onHealLocationChanged(mapName, x, y) {
    writeBridge({type: "heal_location_changed", map: mapName, x: x, y: y});
}

export function onMapHeaderChanged(mapName, property, value) {
    writeBridge({
        type: "header_changed", map: mapName,
        property: property, value: value
    });
}

export function onTilesetChanged(primaryTileset, secondaryTileset) {
    writeBridge({
        type: "tileset_changed", map: currentMap,
        primary: primaryTileset, secondary: secondaryTileset
    });
}

// ═══════════════════════════════════════════════════════════════════════════
// User-triggered actions (registered in Porymap's Tools menu)
// ═══════════════════════════════════════════════════════════════════════════

export function editInPorySuite() {
    // Ctrl+E — send current hover position so PorySuite-Z can look up the event
    writeBridge({type: "edit_request", map: currentMap, x: lastHoverX, y: lastHoverY});
}

export function syncToPorySuite() {
    // Ctrl+Shift+E — full map sync without specific event
    let msg = {type: "sync_request", map: currentMap};
    try { msg.header = utility.getMapHeader(); } catch(e) { msg.header = {}; }
    try { msg.tilesets = utility.getCurrentTilesets(); } catch(e) { msg.tilesets = {}; }
    try { msg.connections = utility.getMapConnections(); } catch(e) { msg.connections = []; }
    writeBridge(msg);
}

// ═══════════════════════════════════════════════════════════════════════════
// Bridge writer
// ═══════════════════════════════════════════════════════════════════════════

function writeBridge(data) {
    data.timestamp = Date.now();
    try {
        // Use our patched writeBridgeFile if available
        utility.writeBridgeFile(JSON.stringify(data));
    } catch(e) {
        // Fallback for stock Porymap: write to log (PorySuite-Z can tail it)
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

function pollForCommand() {
    try {
        // Read command file (written by PorySuite-Z launcher, deleted after reading)
        let raw = utility.readCommandFile();
        if (raw && raw.length > 0) {
            utility.log("[PorySuite-Z Bridge] Got command: " + raw);
            let cmd = JSON.parse(raw);
            handleCommand(cmd);
        }
    } catch(e) {
        utility.log("[PorySuite-Z Bridge] Poll error: " + e);
    }
    // Poll every 500ms
    utility.setTimeout(pollForCommand, 500);
}

function handleCommand(cmd) {
    if (!cmd || !cmd.action) return;
    utility.log("[PorySuite-Z Bridge] Handling command: " + cmd.action + " map=" + (cmd.map || ""));
    if (cmd.action === "openMap" && cmd.map) {
        // map.openMap() is a Q_INVOKABLE we added to MainWindow
        utility.log("[PorySuite-Z Bridge] Calling map.openMap('" + cmd.map + "')...");
        let ok = map.openMap(cmd.map);
        utility.log("[PorySuite-Z Bridge] openMap result: " + ok);
        if (ok) {
            writeBridge({type: "command_ack", action: "openMap", map: cmd.map, success: true});
        } else {
            utility.warn("PorySuite-Z: Could not open map '" + cmd.map + "'");
            writeBridge({type: "command_ack", action: "openMap", map: cmd.map, success: false});
        }
    }
}
