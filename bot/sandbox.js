/**
 * Execution Sandbox for Mineflayer JavaScript Code Snippets
 * Executes generated code dynamically with captured logs and error stack traces.
 */

const { Vec3 } = require('vec3');
const pathfinder = require('mineflayer-pathfinder');
const minecraftData = require('minecraft-data');

/**
 * Executes a JS code string within the bot context.
 * 
 * @param {string} code - Mineflayer JavaScript snippet to execute
 * @param {object} bot - Active Mineflayer bot instance
 * @param {number} timeoutMs - Max execution time in ms (default 30s)
 * @returns {Promise<{success: boolean, stdout: string, stderr: string, errorStack: string, durationMs: number}>}
 */
async function executeCodeSnippet(code, bot, timeoutMs = 30000) {
    const startTime = Date.now();
    const stdoutLines = [];
    const stderrLines = [];

    // Capture console output
    const customConsole = {
        log: (...args) => stdoutLines.push(args.map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ')),
        info: (...args) => stdoutLines.push('[INFO] ' + args.map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ')),
        warn: (...args) => stderrLines.push('[WARN] ' + args.map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ')),
        error: (...args) => stderrLines.push('[ERROR] ' + args.map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' '))
    };

    const mcData = minecraftData(bot.version);
    const goals = pathfinder.goals;

    // Clean up code snippet markdown and reasoning tags (<think>...</think>) if present
    let cleanCode = code.trim();
    cleanCode = cleanCode.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();

    if (cleanCode.startsWith('```javascript')) {
        cleanCode = cleanCode.substring(13);
    } else if (cleanCode.startsWith('```js')) {
        cleanCode = cleanCode.substring(5);
    }
    if (cleanCode.endsWith('```')) {
        cleanCode = cleanCode.substring(0, cleanCode.length - 3);
    }
    cleanCode = cleanCode.trim();

    // Create execution context
    let timeoutId;
    const timeoutPromise = new Promise((_, reject) => {
        timeoutId = setTimeout(() => {
            reject(new Error(`Code execution timed out after ${timeoutMs / 1000} seconds.`));
        }, timeoutMs);
    });

    const executionPromise = (async () => {
        // Construct function with injected parameters
        const AsyncFunction = Object.getPrototypeOf(async function () { }).constructor;
        const runner = new AsyncFunction(
            'bot',
            'Vec3',
            'pathfinder',
            'goals',
            'mcData',
            'console',
            `"use strict";\n${cleanCode}`
        );

        return await runner(bot, Vec3, pathfinder, goals, mcData, customConsole);
    })();

    try {
        await Promise.race([executionPromise, timeoutPromise]);
        clearTimeout(timeoutId);

        return {
            success: true,
            stdout: stdoutLines.join('\n'),
            stderr: stderrLines.join('\n'),
            errorStack: null,
            durationMs: Date.now() - startTime
        };
    } catch (err) {
        clearTimeout(timeoutId);
        
        // Stop any pathfinder movement if error occurred
        if (bot.pathfinder) {
            bot.pathfinder.setGoal(null);
        }

        const stackTrace = err.stack || err.toString();
        stderrLines.push(stackTrace);

        return {
            success: false,
            stdout: stdoutLines.join('\n'),
            stderr: stderrLines.join('\n'),
            errorStack: stackTrace,
            durationMs: Date.now() - startTime
        };
    }
}

module.exports = { executeCodeSnippet };
