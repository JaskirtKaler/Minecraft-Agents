/**
 * Main Mineflayer Bot Entry Point & Bridge Interface
 */

const mineflayer = require('mineflayer');
const { pathfinder, Movements } = require('mineflayer-pathfinder');
const minecraftData = require('minecraft-data');
const WebSocket = require('ws');
const { mineflayer: mineflayerViewer } = require('prismarine-viewer');
const { getBotState } = require('./state');
const { executeCodeSnippet } = require('./sandbox');

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

// Setup Pathfinder movements on spawn
bot.once('spawn', () => {
    console.log(`[Mineflayer] Bot successfully spawned in world as '${bot.username}'!`);
    mcData = minecraftData(bot.version);
    const defaultMovements = new Movements(bot, mcData);
    bot.pathfinder.setMovements(defaultMovements);

    if (ENABLE_VIEWER) {
        try {
            mineflayerViewer(bot, { port: VIEWER_PORT, firstPerson: true });
            console.log(`[Prismarine Viewer] Live 3D web view active on http://localhost:${VIEWER_PORT}`);
        } catch (err) {
            console.error('[Prismarine Viewer] Failed to start web viewer:', err.message);
        }
    }

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
    console.warn(`[Mineflayer] Bot died in game.`);
    sendToOrchestrator({
        type: 'event',
        event: 'death',
        data: { position: bot.entity ? bot.entity.position : null }
    });
});

/**
 * WebSocket Connection Manager
 */
function connectToOrchestrator() {
    if (wsClient && (wsClient.readyState === WebSocket.OPEN || wsClient.readyState === WebSocket.CONNECTING)) {
        return;
    }

    console.log(`[WebSocket Bridge] Connecting to Python Orchestrator at ${WS_URL}...`);
    wsClient = new WebSocket(WS_URL);

    wsClient.on('open', () => {
        console.log(`[WebSocket Bridge] Connected to Python Orchestrator successfully.`);
        if (reconnectTimer) {
            clearInterval(reconnectTimer);
            reconnectTimer = null;
        }

        // Send initial bot state
        sendToOrchestrator({
            type: 'state_update',
            data: getBotState(bot)
        });
    });

    wsClient.on('message', async (rawMessage) => {
        try {
            const payload = JSON.parse(rawMessage.toString());
            await handleOrchestratorMessage(payload);
        } catch (err) {
            console.error('[WebSocket Bridge] Error parsing message from Orchestrator:', err);
        }
    });

    wsClient.on('close', () => {
        console.warn(`[WebSocket Bridge] Connection lost to Orchestrator. Will retry in 5s...`);
        scheduleReconnect();
    });

    wsClient.on('error', (err) => {
        console.error(`[WebSocket Bridge Error] ${err.message}`);
    });
}

function scheduleReconnect() {
    if (!reconnectTimer) {
        reconnectTimer = setInterval(() => {
            connectToOrchestrator();
        }, 5000);
    }
}

function sendToOrchestrator(payload) {
    if (wsClient && wsClient.readyState === WebSocket.OPEN) {
        wsClient.send(JSON.stringify(payload));
    }
}

/**
 * Handle incoming commands from Python Orchestrator
 */
async function handleOrchestratorMessage(message) {
    const { type, id, code, text } = message;

    switch (type) {
        case 'get_state':
            sendToOrchestrator({
                type: 'state_response',
                id,
                data: getBotState(bot)
            });
            break;

        case 'execute_code':
            console.log(`[Execution Sandbox] Executing request ID: ${id || 'unnamed'}`);
            console.log(`--- CODE --- \n${code}\n------------`);
            
            const result = await executeCodeSnippet(code, bot);
            
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
