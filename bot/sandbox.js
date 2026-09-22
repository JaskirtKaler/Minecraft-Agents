/**
 * Execution Sandbox for Mineflayer JavaScript Code Snippets
 * Executes generated code dynamically with captured logs and error stack traces.
 */

const { Vec3 } = require('vec3');
const pathfinder = require('mineflayer-pathfinder');
const minecraftData = require('minecraft-data');

/**
 * Extracts pure JavaScript code from LLM responses (stripping markdown & reasoning).
 */
function extractExecutableCode(rawText) {
    if (!rawText) return '';
    let text = rawText.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();

    // 1. Match code inside ```javascript ... ``` or ```js ... ``` or ``` ... ```
    const match = text.match(/```(?:javascript|js)?\s*([\s\S]*?)```/i);
    let code = match ? match[1].trim() : text;

    // 2. If no closing backticks but starts with backtick block
    if (code.startsWith('```javascript')) code = code.substring(13);
    else if (code.startsWith('```js')) code = code.substring(5);
    else if (code.startsWith('```')) code = code.substring(3);
    if (code.endsWith('```')) code = code.substring(0, code.length - 3);

    // 3. Prevent bot recreation or quit
    code = code.replace(/const\s+mineflayer\s*=\s*require\s*\(\s*['"]mineflayer['"]\s*\);?/g, '// mineflayer injected');
    code = code.replace(/(?:const|let|var)\s+bot\s*=\s*mineflayer\.createBot\s*\([\s\S]*?\);?/g, '// bot injected');
    code = code.replace(/bot\.quit\s*\(\s*\);?/g, '// bot.quit prevented');

    // 4. If bot.once('spawn', fn) is used, run it immediately because the bot is already in world!
    code = code.replace(/bot\.(?:once|on)\s*\(\s*['"]spawn['"]\s*,\s*([a-zA-Z0-9_]+)\s*\);?/g, 'await $1();');

    // 5. If code defines a function like async function mine() but doesn't call it, auto-invoke
    const funcMatch = code.match(/async\s+function\s+([a-zA-Z0-9_]+)\s*\(/);
    if (funcMatch) {
        const funcName = funcMatch[1];
        // Check if function is called anywhere below its definition
        const restOfCode = code.substring(code.indexOf(funcMatch[0]) + funcMatch[0].length);
        if (!restOfCode.includes(`${funcName}(`)) {
            code += `\nawait ${funcName}();`;
        }
    }

    return code.trim();
}

/**
 * Executes a JS code string within the bot context.
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
    const cleanCode = extractExecutableCode(code);

    let timeoutId;
    const timeoutPromise = new Promise((_, reject) => {
        timeoutId = setTimeout(() => {
            reject(new Error(`Code execution timed out after ${timeoutMs / 1000} seconds.`));
        }, timeoutMs);
    });

    const executionPromise = (async () => {
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
            try { bot.pathfinder.setGoal(null); } catch (e) {}
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

module.exports = { executeCodeSnippet, extractExecutableCode };
