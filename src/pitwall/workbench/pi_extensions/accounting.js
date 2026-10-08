import { createHash } from 'node:crypto';
import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
const efforts = new Set(['none', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
const bytes = (value) => Buffer.byteLength(JSON.stringify(value) ?? '');
const object = (value) => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const hash = (value) => createHash('sha256').update(JSON.stringify(value) ?? '').digest('hex').slice(0, 16);
const imageTypes = new Set(['image_url', 'image', 'input_image']);
function countImages(value) {
    if (Array.isArray(value))
        return value.reduce((total, item) => total + countImages(item), 0);
    if (!value || typeof value !== 'object')
        return 0;
    const item = value;
    return (typeof item.type === 'string' && imageTypes.has(item.type) ? 1 : 0) + countImages(item.content);
}
export function shapePayload(payload) {
    const value = object(payload);
    // The hook sees each API's serialized request, rather than Pi's
    // provider-neutral Context. Normalize the supported request shapes while
    // retaining only sizes, roles, and hashes.
    const messages = [];
    if (Array.isArray(value.messages))
        messages.push(...value.messages.map(object));
    if (Array.isArray(value.input))
        messages.push(...value.input.map(object));
    const roles = {};
    let imageCount = 0, hasReasoningFields = false, systemBytes = 0, conversationBytes = 0;
    const contentHashes = messages.map(message => {
        const role = ['system', 'developer', 'assistant', 'user', 'tool'].includes(message.role) ? message.role : 'unknown';
        roles[role] = (roles[role] ?? 0) + 1;
        if (role === 'system' || role === 'developer')
            systemBytes += bytes(message);
        else
            conversationBytes += bytes(message);
        imageCount += countImages(message.content);
        if ('reasoning' in message || 'reasoning_content' in message)
            hasReasoningFields = true;
        return hash(message.content ?? '');
    });
    // Anthropic keeps system content outside `messages`; Responses may use
    // `instructions` instead of a system/developer input item.
    if (value.system !== undefined) {
        systemBytes += bytes(value.system);
        imageCount += countImages(value.system);
    }
    if (value.instructions !== undefined)
        systemBytes += bytes(value.instructions);
    // Some compatible Responses endpoints accept scalar input. It has no role,
    // but should still contribute to conversation size without retaining text.
    if (value.input !== undefined && !Array.isArray(value.input)) {
        conversationBytes += bytes(value.input);
        contentHashes.push(hash(value.input));
    }
    const kwargs = object(value.chat_template_kwargs), safe = {};
    for (const name of ['enable_thinking', 'preserve_thinking'])
        if (typeof kwargs[name] === 'boolean')
            safe[name] = kwargs[name];
    if (typeof kwargs.reasoning_effort === 'string' && efforts.has(kwargs.reasoning_effort))
        safe.reasoning_effort = kwargs.reasoning_effort;
    return { serializedBytes: bytes(payload), systemBytes, toolsBytes: bytes(value.tools ?? []), conversationBytes, messageCount: messages.length, roles, toolCount: Array.isArray(value.tools) ? value.tools.length : 0, imageCount, hasReasoningFields, reasoningEffort: typeof value.reasoning_effort === 'string' && efforts.has(value.reasoning_effort) ? value.reasoning_effort : undefined, chatTemplateKwargs: safe, contentHashes };
}
export function safeUsage(value) {
    const input = object(value), out = {};
    for (const name of ['input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens', 'reasoning']) {
        if (typeof input[name] === 'number' && Number.isFinite(input[name]) && input[name] >= 0)
            out[name] = input[name];
    }
    // Pi initializes every usage field to zero before a provider sends usage.
    // Treat an all-zero object as unavailable so transport errors cannot be
    // reported as a measured zero-token request.
    return Object.keys(out).length && Object.values(out).some(value => value > 0) ? out : null;
}
export async function appendAccounting(path, record) {
    const file = await open(path, constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
    try {
        await file.chmod(0o600);
        await file.writeFile(JSON.stringify(record) + '\n');
    }
    finally {
        await file.close();
    }
}
