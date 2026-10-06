/**
 * Main Mineflayer Bot Entry Point & Bridge Interface
 */

const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '../.env') });
const mineflayer = require('mineflayer');
const { pathfinder, Movements } = require('mineflayer-pathfinder');
const minecraftData = require('minecraft-data');
const WebSocket = require('ws');
let mineflayerViewer = null;
try {
    mineflayerViewer = require('prismarine-viewer').mineflayer;
} catch (e) {
    console.warn('[Prismarine Viewer] Optional viewer module unavailable:', e.message);
}
const {
    createBlockChangeObservation,
    getBotState,
    getWorldIdentity,
    startWorldSession
} = require('./state');
const { executeCodeSnippet } = require('./sandbox');
const { executeTask } = require('./skills');
const { cancelOperation } = require('./operations');
const { describeSubject } = require('./knowledge');

// Configuration from environment or defaults
const HOST = process.env.MC_HOST || 'localhost';
const PORT = parseInt(process.env.MC_PORT || '25565');
const USERNAME = process.env.MC_USERNAME || 'AI_Agent';
const VERSION = process.env.MC_VERSION || false; // false = auto-detect
const WS_URL = process.env.WS_URL || 'ws://localhost:8765';
const ENABLE_VIEWER = process.env.ENABLE_VIEWER === 'true';
const VIEWER_PORT = parseInt(process.env.VIEWER_PORT || '3000');

console.log(`[Bot Launcher] Initializing Mineflayer bot '${USERNAME}' for ${HOST}:${PORT}...`);

const botOptions = {
    host: HOST,
    port: PORT,
    username: USERNAME,
    auth: 'offline'
};
if (VERSION) {
    botOptions.version = VERSION;
}

const bot = mineflayer.createBot(botOptions);
bot.loadPlugin(pathfinder);

let mcData = null;
let wsClient = null;
let reconnectTimer = null;
let stateUpdateTimer = null;
let botSpawned = false;
let botEnded = false;
let viewerStarted = false;

const STATE_UPDATE_INTERVAL_MS = 30_000;
const BLOCK_CHANGE_WINDOW_MS = 30_000;
const MAX_BLOCK_CHANGE_EVENTS_PER_WINDOW = 48;
const BLOCK_CHANGE_DEDUPE_MS = 750;
const MAX_BLOCK_CHANGE_DEDUPE_ENTRIES = 256;

let blockChangeWindowStartedAt = 0;
let blockChangeEventsInWindow = 0;
const recentBlockChanges = new Map();

function isWebSocketOpen() {
    return wsClient && wsClient.readyState === WebSocket.OPEN;
}

function isBotSpawned() {
    return botSpawned && Boolean(bot.entity);
}

function clearStateUpdateTimer() {
    if (stateUpdateTimer) {
        clearInterval(stateUpdateTimer);
        stateUpdateTimer = null;
    }
}

function clearReconnectTimer() {
    if (reconnectTimer) {
        clearInterval(reconnectTimer);
        reconnectTimer = null;
    }
}

function emitStateUpdate() {
    if (!isWebSocketOpen() || !isBotSpawned()) return false;
    sendToOrchestrator({
        type: 'state_update',
        data: getBotState(bot)
    });
    return true;
}

function startStateUpdates() {
    clearStateUpdateTimer();
    if (!isWebSocketOpen() || !isBotSpawned()) return;

    stateUpdateTimer = setInterval(() => {
        // Do not emit stale observations while dead, disconnected, or before a
        // new spawn has supplied a fresh world session id.
        emitStateUpdate();
    }, STATE_UPDATE_INTERVAL_MS);
}

function emitWorldJoin() {
    if (!isWebSocketOpen() || !isBotSpawned()) return;
    sendToOrchestrator({
        type: 'event',
        event: 'world_join',
        data: { currentState: getBotState(bot) }
    });
}

function resetBlockChangeThrottle() {
    blockChangeWindowStartedAt = 0;
    blockChangeEventsInWindow = 0;
    recentBlockChanges.clear();
}

function shouldEmitBlockChange(change) {
    const now = Date.now();
    if (now - blockChangeWindowStartedAt >= BLOCK_CHANGE_WINDOW_MS) {
        blockChangeWindowStartedAt = now;
        blockChangeEventsInWindow = 0;
        recentBlockChanges.clear();
    }

    // Cap discovery chatter from newly loaded chunks, but never discard an
    // explicit replacement/removal (e.g. a 64-log mining task).
    const explicitChange = Boolean(change.oldName);
    if (!explicitChange && blockChangeEventsInWindow >= MAX_BLOCK_CHANGE_EVENTS_PER_WINDOW) {
        return false;
    }

    const { world, position, oldName, newName } = change;
    const key = `${world.sessionId}:${position.x},${position.y},${position.z}:${oldName || ''}:${newName || ''}`;
    const previousAt = recentBlockChanges.get(key);
    if (previousAt && now - previousAt < BLOCK_CHANGE_DEDUPE_MS) {
        return false;
    }

    recentBlockChanges.set(key, now);
    while (recentBlockChanges.size > MAX_BLOCK_CHANGE_DEDUPE_ENTRIES) {
        recentBlockChanges.delete(recentBlockChanges.keys().next().value);
    }
    if (!explicitChange) blockChangeEventsInWindow += 1;
    return true;
}

function handleBlockUpdate(oldBlock, newBlock) {
    if (!isWebSocketOpen() || !isBotSpawned()) return;

    const change = createBlockChangeObservation(bot, oldBlock, newBlock);
    if (!change || !shouldEmitBlockChange(change)) return;

    sendToOrchestrator({
        type: 'event',
        event: 'block_change',
        data: change
    });
}

// Setup Pathfinder movements on spawn
bot.on('spawn', () => {
    console.log(`[Mineflayer] Bot successfully spawned in world as '${bot.username}'!`);
    // A respawn is a new observation session even if it occurs in the same
    // world/dimension. This happens before any state or world_join event.
    startWorldSession(bot);
    resetBlockChangeThrottle();
    botEnded = false;
    botSpawned = true;

    mcData = minecraftData(bot.version);
    const defaultMovements = new Movements(bot, mcData);
    defaultMovements.canDig = false;
    defaultMovements.allow1by1towers = false;
    defaultMovements.scafoldingBlocks = []; // Walking must not silently place bridges.
    defaultMovements.allowParkour = false;
    defaultMovements.maxDropDown = 1;
    for (const name of ['water', 'lava', 'magma_block', 'cactus', 'campfire']) {
        if (mcData.blocksByName[name]) defaultMovements.blocksToAvoid.add(mcData.blocksByName[name].id);
    }
    bot.pathfinder.setMovements(defaultMovements);

    if (ENABLE_VIEWER && !viewerStarted) {
        try {
            mineflayerViewer(bot, { port: VIEWER_PORT, firstPerson: true });
            viewerStarted = true;
            console.log(`[Prismarine Viewer] Live 3D web view active on http://localhost:${VIEWER_PORT}`);
        } catch (err) {
            console.error('[Prismarine Viewer] Failed to start web viewer:', err.message);
        }
    }

    // If the bridge is already connected (for example after a respawn), tell
    // it about the fresh session immediately. Initial connection sends the
    // equivalent state_update below once the socket opens.
    emitWorldJoin();
    emitStateUpdate();
    startStateUpdates();

    // Connect to Python Orchestrator WebSocket Server
    connectToOrchestrator();
});

// Event Listeners for State Broadcast
bot.on('chat', (username, message) => {
    if (username === bot.username) return;
    console.log(`[Minecraft Chat] <${username}> ${message}`);
    sendToOrchestrator({
        type: 'event',
        event: 'chat',
        data: { username, message }
    });
});

bot.on('health', () => {
    sendToOrchestrator({
        type: 'event',
        event: 'health',
        data: { health: bot.health, food: bot.food }
    });
});

// Mineflayer forwards world block updates through the bot, which keeps this
// listener active even if the underlying world object changes dimension.
bot.on('blockUpdate', handleBlockUpdate);

bot.on('kicked', (reason) => {
    console.warn(`[Mineflayer] Bot kicked: ${reason}`);
    sendToOrchestrator({
        type: 'event',
        event: 'kicked',
        data: { reason }
    });
});

bot.on('error', (err) => {
    console.error(`[Mineflayer Error]`, err);
    sendToOrchestrator({
        type: 'event',
        event: 'error',
        data: { message: err.message }
    });
});

bot.on('death', () => {
    cancelOperation(bot);
    console.warn(`[Mineflayer] Bot died in game. Automatically respawning...`);
    botSpawned = false;
    clearStateUpdateTimer();
    sendToOrchestrator({
        type: 'event',
        event: 'death',
        data: {
            observedAt: new Date().toISOString(),
            username: bot.username,
            world: getWorldIdentity(bot),
            position: bot.entity ? bot.entity.position : null
        }
    });
    setTimeout(() => {
        try {
            bot.respawn();
        } catch (e) {
            console.error('[Respawn Error]', e.message);
        }
    }, 1000);
});

bot.on('end', (reason) => {
    cancelOperation(bot);
    console.warn(`[Mineflayer] Bot connection ended: ${reason || 'unknown reason'}`);
    botSpawned = false;
    botEnded = true;
    clearStateUpdateTimer();
    clearReconnectTimer();
});

/**
 * WebSocket Connection Manager
 */
function connectToOrchestrator() {
    if (botEnded) return;
    if (wsClient && (wsClient.readyState === WebSocket.OPEN || wsClient.readyState === WebSocket.CONNECTING)) {
        return;
    }

    console.log(`[WebSocket Bridge] Connecting to Python Orchestrator at ${WS_URL}...`);
    const client = new WebSocket(WS_URL);
    wsClient = client;

    client.on('open', () => {
        if (wsClient !== client) return;
        console.log(`[WebSocket Bridge] Connected to Python Orchestrator successfully.`);
        clearReconnectTimer();

        // Send initial state only after spawn. The state includes the current
        // world/session identity, so reconnecting does not create a duplicate
        // world session.
        emitStateUpdate();
        startStateUpdates();
    });

    client.on('message', async (rawMessage) => {
        try {
            const payload = JSON.parse(rawMessage.toString());
            await handleOrchestratorMessage(payload);
        } catch (err) {
            console.error('[WebSocket Bridge] Error parsing message from Orchestrator:', err);
        }
    });

    client.on('close', () => {
        if (wsClient !== client) return;
        cancelOperation(bot);
        console.warn(`[WebSocket Bridge] Connection lost to Orchestrator. Will retry in 5s...`);
        wsClient = null;
        clearStateUpdateTimer();
        scheduleReconnect();
    });

    client.on('error', (err) => {
        console.error(`[WebSocket Bridge Error] ${err.message}`);
    });
}

function scheduleReconnect() {
    if (!botEnded && !reconnectTimer) {
        reconnectTimer = setInterval(() => {
            connectToOrchestrator();
        }, 5000);
    }
}

function sendToOrchestrator(payload) {
    if (isWebSocketOpen()) {
        wsClient.send(JSON.stringify(payload));
    }
}

/**
 * Handle incoming commands from Python Orchestrator
 */
async function handleOrchestratorMessage(message) {
    const { type, id, code, text, task, timeoutMs } = message;

    switch (type) {
        case 'cancel_task':
            cancelOperation(bot);
            break;
        case 'get_knowledge':
            sendToOrchestrator({ type: 'knowledge_response', id, data: describeSubject(bot, message.subject) });
            break;
        case 'get_state':
            sendToOrchestrator({
                type: 'state_response',
                id,
                data: getBotState(bot)
            });
            break;

        case 'execute_code':
            if (process.env.ALLOW_EXPERIMENTAL_CODE !== 'true') {
                sendToOrchestrator({ type: 'execution_result', id, result: {
                    success: false, verified: false, status: 'failed',
                    errorStack: 'Generated JavaScript is disabled. Use verified resource skills.'
                } });
                break;
            }
            console.log(`[Execution Sandbox] Executing request ID: ${id || 'unnamed'}`);
            console.log(`--- CODE --- \n${code}\n------------`);
            
            const sandboxTimeoutMs = Number.isInteger(timeoutMs)
                ? Math.min(Math.max(timeoutMs, 1000), 120000)
                : 30000;
            const result = await executeCodeSnippet(code, bot, sandboxTimeoutMs);
            
            console.log(`[Execution Result] Success: ${result.success} | Duration: ${result.durationMs}ms`);
            if (!result.success) {
                console.error(`[Execution Error Stack]\n${result.errorStack}`);
            }

            sendToOrchestrator({
                type: 'execution_result',
                id,
                result,
                currentState: getBotState(bot)
            });
            break;

        case 'execute_task': {
            if (!isBotSpawned()) {
                sendToOrchestrator({ type: 'execution_result', id, result: {
                    success: false, verified: false, message: 'The bot is not spawned; wait for it to rejoin.'
                } });
                break;
            }
            console.log(`[Verified Task] Executing request ID: ${id || 'unnamed'} (${task?.name || 'unknown'})`);
            const startedAt = Date.now();
            const taskResult = await executeTask(bot, task, { onProgress: phase => {
                console.log('[Task Progress]', phase);
                sendToOrchestrator({ type: 'event', event: 'task_progress', data: { id, phase } });
            } });
            const result = { ...taskResult, durationMs: Date.now() - startedAt };

            console.log(`[Verified Task] Success: ${result.success} | Verified: ${result.verified} | Duration: ${result.durationMs}ms`);
            sendToOrchestrator({
                type: 'execution_result',
                id,
                result,
                currentState: getBotState(bot)
            });
            break;
        }

        case 'chat':
            if (text) {
                bot.chat(text);
            }
            break;

        case 'ping':
            sendToOrchestrator({ type: 'pong', id });
            break;

        default:
            console.warn(`[WebSocket Bridge] Unhandled message type: '${type}'`);
    }
}
