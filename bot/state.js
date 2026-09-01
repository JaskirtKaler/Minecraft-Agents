/**
 * State Serializer for Mineflayer Bot
 * Converts raw bot state into structured JSON for LLM Prompt Context.
 */

function getBotState(bot, options = {}) {
    if (!bot || !bot.entity) {
        return { ready: false, message: "Bot is not fully spawned yet." };
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
    for (const entityName in bot.entities) {
        const entity = bot.entities[entityName];
        if (!entity || entity === bot.entity) continue;

        const dist = entity.position.distanceTo(pos);
        if (dist <= mobRadius) {
            nearbyEntities.push({
                name: entity.name || entity.username || entity.type,
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
    const keyBlockTypes = [
        'oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log',
        'crafting_table', 'furnace', 'chest', 'bed',
        'coal_ore', 'deepslate_coal_ore', 'iron_ore', 'deepslate_iron_ore', 'gold_ore', 'diamond_ore',
        'water', 'lava', 'wheat', 'carrots', 'potatoes'
    ];

    const nearbyBlocksSummary = {};

    if (bot.findBlocks) {
        // Search for key blocks in radius
        const foundPositions = bot.findBlocks({
            matching: (block) => block && keyBlockTypes.includes(block.name),
            maxDistance: blockRadius,
            count: 35
        });

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
    const blockBelow = bot.blockAt(pos.offset(0, -1, 0));

    return {
        ready: true,
        stats,
        equipment,
        inventory: inventoryItems,
        standingOn: blockBelow ? blockBelow.name : 'unknown',
        nearbyEntities: topEntities,
        nearbyKeyBlocks: nearbyBlocksSummary
    };
}

module.exports = { getBotState };
