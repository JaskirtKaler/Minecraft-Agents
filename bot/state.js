/**
 * State Serializer for Mineflayer Bot
 * Converts raw bot state into structured JSON for LLM Prompt Context.
 */

const { randomUUID } = require('crypto');
const { farmingTargets, plantingSites } = require('./grounding');
const { plantingRule } = require('./knowledge');

const KEY_BLOCK_TYPES = new Set([
    'oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log',
    'crimson_stem', 'warped_stem',
    'crafting_table', 'furnace', 'blast_furnace', 'smoker', 'chest', 'trapped_chest', 'ender_chest',
    'nether_portal', 'end_portal',
    'coal_ore', 'deepslate_coal_ore', 'iron_ore', 'deepslate_iron_ore', 'gold_ore', 'diamond_ore',
    'water', 'lava', 'farmland', 'wheat', 'carrots', 'potatoes', 'beetroots', 'stone', 'cobblestone'
]);

// Kept outside the Mineflayer object so state serialization does not expose
// implementation details to the planner. A new id is assigned on every spawn.
const worldSessions = new WeakMap();

function canStoreSession(bot) {
    return bot && (typeof bot === 'object' || typeof bot === 'function');
}

function startWorldSession(bot) {
    const sessionId = randomUUID();
    if (canStoreSession(bot)) {
        worldSessions.set(bot, sessionId);
    }
    return sessionId;
}

function getWorldSessionId(bot) {
    if (!canStoreSession(bot)) {
        return randomUUID();
    }

    let sessionId = worldSessions.get(bot);
    if (!sessionId) {
        sessionId = startWorldSession(bot);
    }
    return sessionId;
}

function getWorldIdentity(bot) {
    const host = process.env.MC_HOST || 'localhost';
    const port = process.env.MC_PORT || '25565';
    return {
        id: process.env.MC_WORLD_ID || `${host}:${port}`,
        dimension: bot && bot.game && bot.game.dimension != null ? String(bot.game.dimension) : 'unknown',
        sessionId: getWorldSessionId(bot)
    };
}

function isKeyBlockName(name) {
    return typeof name === 'string' && (KEY_BLOCK_TYPES.has(name) || name.endsWith('_bed'));
}

function blockName(block) {
    return block && typeof block.name === 'string' ? block.name : null;
}

function blockPosition(block) {
    if (!block || !block.position) return null;
    const { x, y, z } = block.position;
    if (![x, y, z].every(Number.isFinite)) return null;
    return { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) };
}

/**
 * Builds a memory-safe block-change event from a loaded Mineflayer update.
 * A null new block is deliberately ignored: chunk unloads must never imply a
 * depleted resource. An old null block is still useful when a loaded key block
 * first becomes observable.
 */
function createBlockChangeObservation(bot, oldBlock, newBlock, observedAt = new Date().toISOString()) {
    if (!newBlock) return null;

    const oldName = blockName(oldBlock);
    const newName = blockName(newBlock);
    if (!newName || oldName === newName) return null;
    if (!isKeyBlockName(oldName) && !isKeyBlockName(newName)) return null;

    const position = blockPosition(newBlock) || blockPosition(oldBlock);
    if (!position) return null;

    return {
        observedAt,
        world: getWorldIdentity(bot),
        position,
        oldName,
        newName
    };
}

function getBotState(bot, options = {}) {
    const observation = {
        observedAt: new Date().toISOString(),
        username: bot && bot.username ? bot.username : null,
        world: getWorldIdentity(bot)
    };

    if (!bot || !bot.entity) {
        return {
            ready: false,
            message: "Bot is not fully spawned yet.",
            ...observation,
            nearbyKeyBlocks: {},
            // The radius-limited scan below is never an absence proof.
            nearbyKeyBlocksComplete: false
        };
    }

    const {
        blockRadius = 16,
        mobRadius = 24
    } = options;

    const pos = bot.entity.position;

    // 1. Basic Stats
    const stats = {
        position: {
            x: Math.round(pos.x * 10) / 10,
            y: Math.round(pos.y * 10) / 10,
            z: Math.round(pos.z * 10) / 10
        },
        health: bot.health,
        food: bot.food,
        oxygen: bot.oxygenLevel,
        isRaining: bot.isRaining || false,
        timeOfDay: bot.time ? bot.time.timeOfDay : null,
        isNight: bot.time ? (bot.time.timeOfDay > 13000 && bot.time.timeOfDay < 23000) : false,
        gameMode: bot.game ? bot.game.gameMode : 'unknown'
    };

    // 2. Inventory & Equipment
    const inventoryItems = bot.inventory.items().map(item => ({
        name: item.name,
        count: item.count,
        slot: item.slot
    }));

    const heldItem = bot.heldItem ? { name: bot.heldItem.name, count: bot.heldItem.count } : null;

    const equipment = {
        held: heldItem,
        head: bot.inventory.slots[5] ? bot.inventory.slots[5].name : null,
        torso: bot.inventory.slots[6] ? bot.inventory.slots[6].name : null,
        legs: bot.inventory.slots[7] ? bot.inventory.slots[7].name : null,
        feet: bot.inventory.slots[8] ? bot.inventory.slots[8].name : null,
        offhand: bot.inventory.slots[45] ? bot.inventory.slots[45].name : null
    };

    // 3. Nearby Entities (Mobs, Animals, Players)
    const nearbyEntities = [];
    for (const entityName in bot.entities || {}) {
        const entity = bot.entities[entityName];
        if (!entity || entity === bot.entity) continue;

        const dist = entity.position.distanceTo(pos);
        if (dist <= mobRadius) {
            nearbyEntities.push({
                name: entity.username || entity.name || entity.type,
                type: entity.type, // 'mob', 'animal', 'player', 'object'
                kind: entity.kind,
                distance: Math.round(dist * 10) / 10,
                position: {
                    x: Math.round(entity.position.x * 10) / 10,
                    y: Math.round(entity.position.y * 10) / 10,
                    z: Math.round(entity.position.z * 10) / 10
                }
            });
        }
    }
    // Sort entities by distance and take top 10
    nearbyEntities.sort((a, b) => a.distance - b.distance);
    const topEntities = nearbyEntities.slice(0, 10);

    // 4. Surrounding Key Blocks Search
    const nearbyBlocksSummary = {};

    if (bot.findBlocks) {
        // Search for key blocks in radius
        const foundPositions = bot.findBlocks({
            matching: (block) => block && isKeyBlockName(block.name) && !['stone', 'cobblestone'].includes(block.name),
            maxDistance: blockRadius,
            count: 35
        }) || [];
        // Common stone must not crowd chests, crafting stations, and hazards
        // out of the bounded landmark scan.
        foundPositions.push(...(bot.findBlocks({
            matching: block => block && ['stone', 'cobblestone'].includes(block.name),
            maxDistance: blockRadius,
            count: 12
        }) || []));

        for (const p of foundPositions) {
            const block = bot.blockAt(p);
            if (!block) continue;

            const name = block.name;
            const dist = Math.round(p.distanceTo(pos) * 10) / 10;

            if (!nearbyBlocksSummary[name]) {
                nearbyBlocksSummary[name] = [];
            }
            if (nearbyBlocksSummary[name].length < 3) {
                nearbyBlocksSummary[name].push({
                    x: p.x,
                    y: p.y,
                    z: p.z,
                    distance: dist
                });
            }
        }
    }

    // 5. Standing Block & Block Below
    const blockBelow = typeof bot.blockAt === 'function'
        ? bot.blockAt(pos.offset(0, -1, 0))
        : null;

    return {
        ready: true,
        ...observation,
        stats,
        equipment,
        inventory: inventoryItems,
        farmingTargets: farmingTargets(bot, blockRadius),
        plantingTargets: bot.version ? inventoryItems.filter(item => plantingRule(bot, item.name)?.category === 'sapling')
            .slice(0, 2).map(item => ({ item: item.name, ...plantingSites(bot, item.name, Math.min(32, Math.max(1, blockRadius))) })) : [],
        standingOn: blockBelow ? blockBelow.name : 'unknown',
        gameKnowledge: {
            source: 'minecraft-data for ' + bot.version,
            cobblestone: 'Mine exposed stone/cobblestone with a harvest-capable pickaxe without Silk Touch; verify pickup.',
            navigation: 'Walking cannot dig. Bounded controlled uphill staircase escape may clear terrain; no towers or mining underfoot.',
            wheat: 'Plant wheat_seeds to create a wheat block in the empty cell directly above farmland. Soil and crop have different positions. Inspect age before harvest; do not remove existing crops unless requested.'
        },
        nearbyEntities: topEntities,
        nearbyKeyBlocks: nearbyBlocksSummary,
        // A bounded scan only reports what is currently loaded and nearby.
        // Missing keys must not be interpreted as world-wide absence.
        nearbyKeyBlocksComplete: false
    };
}

module.exports = {
    KEY_BLOCK_TYPES,
    createBlockChangeObservation,
    getBotState,
    getWorldIdentity,
    getWorldSessionId,
    isKeyBlockName,
    startWorldSession
};
