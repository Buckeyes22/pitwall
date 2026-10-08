// Ported from packages/pi-workbench/tests/provider-extension.test.ts (vitest -> node:test).
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import { realpathSync } from 'node:fs';
import { afterEach, mock } from 'node:test';
import { any, assertMatch, errorLike, extensionUrl, loadExtension, piModulesDirectory, piTest as test, skipReason } from './support.mjs';

const { registerAdmissionProvider, registerConfiguredProvider } = skipReason ? {} : await loadExtension('provider-extension.js');
const { AccountBudgetAdmission } = skipReason ? {} : await loadExtension('account-budget.js');
const servers = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(server => new Promise(resolve => server.close(() => resolve())))); });
test('hosted account profiles require an explicit conservative budget policy', () => {
    assert.throws(() => registerAdmissionProvider({ registerProvider: () => undefined }, { profile: { provider: 'hosted-fixture', accountRef: 'shared-account', api: 'openai-completions', resourceGroup: 'hosted-fixture', servedContextTokens: 4096, maxCompletionTokens: 32 }, providerConfig: { baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions', models: [] } }, '/tmp/hosted-budget-policy.json'), errorLike('explicit account budget policy'));
});
test('admission rejects unknown Workbench profile keys while preserving provider metadata', () => {
    const providerConfig = { baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions', models: [], samplingParams: { providerSpecificKnob: true } };
    assert.throws(() => registerAdmissionProvider({ registerProvider: () => undefined }, {
        profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'fixture', servedContextTokens: 4096, maxCompletionTokens: 32, typoModel: 'wrong' },
        providerConfig,
    }, '/tmp/provider-unknown-profile.json'), errorLike('unknown profile field: typoModel'));
    let registered;
    registerAdmissionProvider({ registerProvider: (_name, config) => { registered = config; } }, {
        profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'fixture-metadata', servedContextTokens: 4096, maxCompletionTokens: 32 },
        providerConfig,
    }, '/tmp/provider-metadata.json');
    assert.deepStrictEqual(registered.samplingParams, { providerSpecificKnob: true });
});
test('hosted registration requires an exact model and endpoint', () => {
    const profile = { provider: 'selected-account', accountRef: 'account-a', accountGroup: 'account-a', accountMaxConcurrent: 1, accountInFlightTokenBudget: 256, accountUnknownUsage: 'hold', api: 'openai-completions', resourceGroup: 'account-a', servedContextTokens: 4096, maxCompletionTokens: 32 };
    assert.throws(() => registerAdmissionProvider({ registerProvider: () => undefined }, { profile, providerConfig: { baseUrl: 'http://127.0.0.1:1/v1', models: [] } }, '/tmp/affinity-required.json'), errorLike('exact model, endpoint, and credential affinity'));
});
test('hosted registration refuses an edited provider configuration', () => {
    const endpoint = 'http://127.0.0.1:1/v1';
    const profile = { provider: 'selected-account', modelId: 'entitled-model', endpoint, apiKeyEnv: 'PITWALL_WORKBENCH_TEST_HOSTED_KEY', accountRef: 'account-a', accountGroup: 'account-a', accountMaxConcurrent: 1, accountInFlightTokenBudget: 256, accountUnknownUsage: 'hold', api: 'openai-completions', resourceGroup: 'account-a', servedContextTokens: 4096, maxCompletionTokens: 32 }; // pragma: allowlist secret
    const providerConfig = { baseUrl: endpoint, api: 'openai-completions', apiKey: '$PITWALL_WORKBENCH_TEST_HOSTED_KEY', models: [{ id: 'entitled-model' }] }; // pragma: allowlist secret
    for (const altered of [{ ...providerConfig, baseUrl: 'http://127.0.0.1:2/v1' }, { ...providerConfig, api: 'anthropic-messages' }, { ...providerConfig, models: [{ id: 'other-model' }] }, { ...providerConfig, models: [providerConfig.models[0], { id: 'other-model' }] }]) {
        assert.throws(() => registerAdmissionProvider({ registerProvider: () => undefined }, { profile, providerConfig: altered }, '/tmp/edited-profile.json'), errorLike('configuration differs'));
    }
});
test('direct admission registration enforces the compiled no-fallback policy', () => {
    const profile = { provider: 'fixture', modelId: 'fixture-model', endpoint: 'http://127.0.0.1:1/v1', api: 'openai-completions', keyless: 'dummy', servedContextTokens: 32768, maxCompletionTokens: 4096, resourceGroup: 'fixture', allowProviderFallback: false };
    const providerConfig = { baseUrl: profile.endpoint, api: profile.api, apiKey: 'dummy', models: [{ id: profile.modelId }] }; // pragma: allowlist secret
    assert.throws(() => registerAdmissionProvider({ registerProvider: () => undefined }, { profile: { ...profile, allowProviderFallback: true }, providerConfig }, '/tmp/direct-edited-profile.json'), errorLike('provider fallback must be false'));
    let registered = false;
    registerAdmissionProvider({ registerProvider: () => { registered = true; } }, { profile, providerConfig }, '/tmp/direct-valid-profile.json');
    assert.strictEqual(registered, true);
});
test('configured provider rejects a public or malformed profile file before registration', async () => {
    const root = await mkdtemp(join(tmpdir(), 'pi-provider-file-'));
    const path = join(root, 'provider-profile.json');
    const old = process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
    process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE = path;
    let calls = 0;
    const pi = { registerProvider: () => { calls++; } };
    try {
        await writeFile(path, '{}', { mode: 0o644 });
        await await assert.rejects(registerConfiguredProvider(pi), errorLike('private user-owned regular file'));
        await chmod(path, 0o600);
        await await assert.rejects(registerConfiguredProvider(pi), errorLike('provider profile is invalid'));
        assert.strictEqual(calls, 0);
        const validProfile = { provider: 'fixture', modelId: 'fixture-model', endpoint: 'http://127.0.0.1:1/v1', api: 'openai-completions', keyless: 'dummy', servedContextTokens: 32768, maxCompletionTokens: 4096, resourceGroup: 'fixture', allowProviderFallback: false };
        const providerConfig = { baseUrl: validProfile.endpoint, api: validProfile.api, apiKey: 'dummy', models: [{ id: validProfile.modelId }] }; // pragma: allowlist secret
        await writeFile(path, JSON.stringify({ profile: validProfile, providerConfig }), { mode: 0o600 });
        await assert.strictEqual((await registerConfiguredProvider(pi)), undefined);
        assert.strictEqual(calls, 1);
        await writeFile(path, JSON.stringify({ profile: { ...validProfile, allowProviderFallback: true }, providerConfig }), { mode: 0o600 });
        await await assert.rejects(registerConfiguredProvider(pi), errorLike('provider fallback must be false'));
        assert.strictEqual(calls, 1);
    }
    finally {
        if (old === undefined)
            delete process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
        else
            process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE = old;
        await rm(root, { recursive: true, force: true });
    }
});
test('profile affinity refuses wrong model, provider, and endpoint before HTTP', async () => {
    let requests = 0;
    const server = createServer((_req, res) => { requests++; res.writeHead(401); res.end(); });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    let registration;
    registerAdmissionProvider({ registerProvider: (_name, config) => { registration = config; } }, { profile: { provider: 'selected-account', modelId: 'entitled-model', endpoint, api: 'openai-completions', resourceGroup: 'affinity-test', servedContextTokens: 4096, maxCompletionTokens: 32 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } }, '/tmp/affinity-profile.json');
    const model = { id: 'entitled-model', provider: 'selected-account', baseUrl: endpoint, api: 'openai-completions', contextWindow: 4096, maxTokens: 32 };
    const context = { messages: [{ role: 'user', content: 'hello' }] };
    for (const mismatch of [{ id: 'unentitled-model' }, { provider: 'other-account' }, { api: 'anthropic-messages' }, { baseUrl: 'http://127.0.0.1:1/v1' }]) {
        const result = await registration.streamSimple({ ...model, ...mismatch }, context, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        assert.strictEqual(result.stopReason, 'error');
        assert.match(result.errorMessage, /differs from profile/);
    }
    assert.strictEqual(requests, 0);
});
test('hosted stream rejects a response model different from the selected profile and records an error settlement', async () => {
    let requests = 0;
    let requestedModel = '';
    const server = createServer((req, res) => {
        requests++;
        let body = '';
        req.on('data', chunk => { body += chunk; });
        req.on('end', () => {
            requestedModel = JSON.parse(body).model;
            res.writeHead(200, { 'content-type': 'text/event-stream' });
            res.end([
                `data: ${JSON.stringify({ id: 'wrong-model', object: 'chat.completion.chunk', model: 'MiniMax-M3', choices: [{ index: 0, delta: { content: 'must not be forwarded' }, finish_reason: null }] })}`,
                '',
                `data: ${JSON.stringify({ id: 'wrong-model', object: 'chat.completion.chunk', model: 'MiniMax-M3', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } })}`,
                '',
                'data: [DONE]',
                '',
            ].join('\n'));
        });
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const root = await mkdtemp(join(tmpdir(), 'pi-response-model-'));
    const previousKey = process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
    const previousResourceDirectory = process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
    const previousBudgetDirectory = process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
    process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = 'fixture-selected-key'; // pragma: allowlist secret
    process.env.PITWALL_WORKBENCH_RESOURCE_DIR = join(root, 'admission');
    process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR = join(root, 'budget');
    try {
        const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
        let registration;
        registerAdmissionProvider({ registerProvider: (_name, value) => { registration = value; } }, { profile: { provider: 'minimax-coding-plan', modelId: 'pi-unentitled-probe-20260921', endpoint, apiKeyEnv: 'PITWALL_WORKBENCH_TEST_HOSTED_KEY', accountRef: 'minimax-account', accountGroup: 'minimax-account', accountMaxConcurrent: 1, accountInFlightTokenBudget: 128, accountUnknownUsage: 'hold', api: 'openai-completions', resourceGroup: 'minimax-account', servedContextTokens: 4096, maxCompletionTokens: 32 }, providerConfig: { baseUrl: endpoint, apiKey: '$PITWALL_WORKBENCH_TEST_HOSTED_KEY', api: 'openai-completions', models: [{ id: 'pi-unentitled-probe-20260921' }] } }, join(root, 'provider.json')); // pragma: allowlist secret
        const model = { id: 'pi-unentitled-probe-20260921', name: 'pi-unentitled-probe-20260921', api: 'openai-completions', provider: 'minimax-coding-plan', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
        const stream = registration.streamSimple(model, { messages: [{ role: 'user', content: 'probe' }] }, { apiKey: 'fixture-selected-key' }); // pragma: allowlist secret
        const eventsPromise = (async () => { const events = []; for await (const event of stream)
            events.push(event); return events; })();
        const result = await stream.result();
        const events = await eventsPromise;
        assert.strictEqual(requests, 1);
        assert.strictEqual(requestedModel, 'pi-unentitled-probe-20260921');
        assert.strictEqual(result.stopReason, 'error');
        assert.match(result.errorMessage, /response model.*MiniMax-M3.*selected model.*pi-unentitled-probe-20260921/i);
        assert.strictEqual(result.responseModel, 'MiniMax-M3');
        assert.ok((events.map(event => event.type)).includes('start'));
        assert.ok((events.map(event => event.type)).includes('error'));
        assert.ok(!(events.map(event => event.type)).includes('done'));
        assert.strictEqual(events.some(event => event.type === 'text_delta'), false);
        let records = [];
        for (let attempt = 0; attempt < 100; attempt++) {
            try {
                records = (await readFile(join(root, 'native-accounting.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
            }
            catch {
                records = [];
            }
            if (records.some(record => record.type === 'native_released'))
                break;
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        const settled = records.find(record => record.type === 'native_settled');
        assertMatch(settled, { outcome: 'error', usage: null });
        assert.strictEqual(records.some(record => record.type === 'native_released'), true);
    }
    finally {
        if (previousKey === undefined)
            delete process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
        else
            process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = previousKey;
        if (previousResourceDirectory === undefined)
            delete process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
        else
            process.env.PITWALL_WORKBENCH_RESOURCE_DIR = previousResourceDirectory;
        if (previousBudgetDirectory === undefined)
            delete process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
        else
            process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR = previousBudgetDirectory;
    }
});
test('hosted profile refuses a credential from another account before HTTP', async () => {
    let requests = 0;
    const server = createServer((_req, res) => { requests++; res.writeHead(401); res.end(); });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    const previousKey = process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
    process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = 'selected-account-fixture'; // pragma: allowlist secret
    try {
        let registration;
        registerAdmissionProvider({ registerProvider: (_name, config) => { registration = config; } }, { profile: { provider: 'selected-account', modelId: 'entitled-model', endpoint, apiKeyEnv: 'PITWALL_WORKBENCH_TEST_HOSTED_KEY', accountRef: 'selected-account', accountGroup: 'selected-account', accountMaxConcurrent: 1, accountInFlightTokenBudget: 256, accountUnknownUsage: 'hold', api: 'openai-completions', resourceGroup: 'selected-account', servedContextTokens: 4096, maxCompletionTokens: 32 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', apiKey: '$PITWALL_WORKBENCH_TEST_HOSTED_KEY', models: [{ id: 'entitled-model' }] } }, '/tmp/account-affinity-profile.json'); // pragma: allowlist secret
        const model = { id: 'entitled-model', provider: 'selected-account', baseUrl: endpoint, api: 'openai-completions', contextWindow: 4096, maxTokens: 32 };
        const result = await registration.streamSimple(model, { messages: [{ role: 'user', content: 'hello' }] }, { apiKey: 'other-account-fixture' }).result(); // pragma: allowlist secret
        assert.strictEqual(result.stopReason, 'error');
        assert.strictEqual(result.errorMessage, 'selected account credential differs from profile');
        assert.strictEqual(requests, 0);
    }
    finally {
        if (previousKey === undefined)
            delete process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
        else
            process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = previousKey;
    }
});
for (const api of ["openai-completions", "openai-responses", "anthropic-messages"])
    for (const status of [401, 403, 404, 429]) {
        test(`stock ${api} transport makes exactly one request for ${status}`, async () => {
            let requests = 0;
            const server = createServer((_req, res) => { requests++; res.writeHead(status, { 'content-type': 'application/json' }); res.end('{}'); });
            servers.push(server);
            await new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve()));
            const port = server.address().port;
            let registration;
            const pi = { registerProvider: (_name, config) => { registration = config; } };
            registerAdmissionProvider(pi, { profile: { provider: 'fixture', api, resourceGroup: `fixture-${api}-${status}`, servedContextTokens: 4096, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: `http://127.0.0.1:${port}/v1`, api, models: [] } }, '/tmp/provider-profile.json');
            const model = { id: 'fixture', name: 'fixture', api, provider: 'fixture', baseUrl: `http://127.0.0.1:${port}/v1`, reasoning: false, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 4096, maxTokens: 32 };
            const stream = registration.streamSimple(model, { messages: [{ role: 'user', content: 'ping' }] }, { maxRetries: 4, apiKey: 'fixture' }); // pragma: allowlist secret
            const result = await stream.result();
            assert.strictEqual(requests, 1);
            if (status === 403 || status === 404)
                assert.ok((result.errorMessage ?? '').includes(String(status)));
        });
    }
test('two separate Node hosts serialize actual HTTP requests through shared admission', { timeout: 10000 }, async () => {
    let active = 0, maxActive = 0, requests = 0;
    const server = createServer((req, res) => { req.resume(); active++; maxActive = Math.max(maxActive, active); requests++; setTimeout(() => { active--; res.writeHead(401, { 'content-type': 'application/json' }); res.end('{}'); }, 150); });
    servers.push(server);
    await new Promise(r => server.listen(0, '127.0.0.1', r));
    const root = await mkdtemp(join(tmpdir(), 'pi-two-http-hosts-')), endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    const profile = { profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'shared-http', servedContextTokens: 4096, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } };
    const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
    const script = join(root, 'host.mjs');
    await writeFile(script, `import{registerAdmissionProvider}from ${JSON.stringify(await extensionUrl('provider-extension.js'))};let registration;registerAdmissionProvider({registerProvider:(_n,v)=>registration=v},${JSON.stringify(profile)},${JSON.stringify(join(root, 'provider.json'))});process.stdout.write('ready\\n');await new Promise(r=>process.stdin.once('data',r));await registration.streamSimple(${JSON.stringify(model)},{messages:[{role:'user',content:'hello',timestamp:Date.now()}]},{apiKey:'fixture'}).result();process.stdin.destroy();`); // pragma: allowlist secret
    const children = [0, 1].map(() => spawn(process.execPath, [script], { env: { ...process.env, PITWALL_WORKBENCH_RESOURCE_DIR: root }, stdio: ['pipe', 'pipe', 'pipe'] }));
    try {
        await Promise.all(children.map(child => new Promise((resolve, reject) => { let out = ''; child.stdout.on('data', x => { out += x; if (out.includes('ready'))
            resolve(); }); child.on('error', reject); })));
        const finished = children.map(child => new Promise(resolve => child.on('exit', resolve)));
        children.forEach(child => child.stdin.write('go'));
        assert.deepStrictEqual(await Promise.all(finished), [0, 0]);
        assert.strictEqual(requests, 2);
        assert.strictEqual(maxActive, 1);
        assert.notStrictEqual(children[0].pid, children[1].pid);
    }
    finally {
        children.forEach(child => child.kill('SIGKILL'));
    }
});
test('records Workbench admission queue, transport, settlement, and release intervals', { timeout: 15000 }, async () => {
    let requests = 0;
    let firstResponse;
    let active = 0;
    let maxActive = 0;
    const server = createServer((_req, res) => {
        requests++;
        active++;
        maxActive = Math.max(maxActive, active);
        res.on('close', () => { active--; });
        if (requests === 1) {
            firstResponse = res;
            res.writeHead(200, { 'content-type': 'text/event-stream' });
            return;
        }
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.end(`data: ${JSON.stringify({ id: 'second', object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content: 'second' }, finish_reason: 'stop' }], usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 } })}\n\ndata: [DONE]\n\n`);
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const root = await mkdtemp(join(tmpdir(), 'pi-admission-measurement-'));
    const resourceDirectory = join(root, 'admission');
    const previous = process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
    process.env.PITWALL_WORKBENCH_RESOURCE_DIR = resourceDirectory;
    try {
        const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
        const profile = { profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'measured-queue', servedContextTokens: 4096, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } };
        await Promise.all([mkdir(join(root, 'first'), { recursive: true }), mkdir(join(root, 'second'), { recursive: true })]);
        let first;
        let second;
        registerAdmissionProvider({ registerProvider: (_name, value) => { first = value; } }, profile, join(root, 'first', 'provider.json'));
        registerAdmissionProvider({ registerProvider: (_name, value) => { second = value; } }, profile, join(root, 'second', 'provider.json'));
        const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
        const firstResult = first.streamSimple(model, { messages: [{ role: 'user', content: 'hold' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        const deadline = Date.now() + 3_000;
        while (requests !== 1) {
            if (Date.now() > deadline)
                throw new Error('first request did not reach transport');
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        const secondResult = second.streamSimple(model, { messages: [{ role: 'user', content: 'queued' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        await new Promise(resolve => setTimeout(resolve, 80));
        firstResponse.end(`data: ${JSON.stringify({ id: 'first', object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content: 'first' }, finish_reason: 'stop' }], usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 } })}\n\ndata: [DONE]\n\n`);
        await Promise.all([firstResult, secondResult]);
        const accounting = async (name) => (await readFile(join(root, name, 'native-accounting.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
        let records = [];
        for (let attempt = 0; attempt < 100; attempt++) {
            records = [...await accounting('first'), ...await accounting('second')];
            if (records.filter(record => record.type === 'native_released').length >= records.filter(record => record.type === 'native_request').length)
                break;
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        const request = records.find(record => record.type === 'native_request' && record.requestId);
        const queuedRequest = records.find(record => record.type === 'native_request' && record.queueWaitMs > 0);
        const settled = records.find(record => record.type === 'native_settled' && record.requestId === queuedRequest?.requestId);
        const released = records.find(record => record.type === 'native_released' && record.requestId === queuedRequest?.requestId);
        assert.strictEqual(maxActive, 1);
        assert.notStrictEqual(queuedRequest, undefined);
        assertMatch(request, { queuedAt: any(Number), acquiredAt: any(Number), transportStartedAt: any(Number) });
        assert.ok(queuedRequest.acquiredAt >= queuedRequest.queuedAt);
        assert.ok(queuedRequest.transportStartedAt >= queuedRequest.acquiredAt);
        assertMatch(settled, { settledAt: any(Number) });
        assertMatch(released, { releasedAt: any(Number) });
        assert.ok(released.releasedAt >= settled.settledAt);
        assert.ok(queuedRequest.queueWaitMs > 0);
        const evidencePath = process.env.PITWALL_WORKBENCH_ADMISSION_EVIDENCE_PATH;
        if (evidencePath) {
            const measured = records.filter(record => record.type === 'native_request').map(record => {
                const end = records.find(candidate => candidate.type === 'native_settled' && candidate.requestId === record.requestId);
                const release = records.find(candidate => candidate.type === 'native_released' && candidate.requestId === record.requestId);
                return { queueWaitMs: record.queueWaitMs, admissionToTransportMs: record.transportStartedAt - record.acquiredAt, transportToSettledMs: typeof end?.settledAt === 'number' ? end.settledAt - record.transportStartedAt : null, settledToReleasedMs: typeof end?.settledAt === 'number' && typeof release?.releasedAt === 'number' ? release.releasedAt - end.settledAt : null };
            });
            await writeFile(evidencePath, JSON.stringify({ schemaVersion: 1, kind: 'workbench-admission-measurement', measuredAt: new Date().toISOString(), transport: 'loopback pinned provider wrapper', requests, maxActive, samples: measured, toolExecutionMs: null, testExecutionMs: null, limitation: 'This provider admission fixture measures Workbench queue and transport lifecycle only; tool and host-test execution are separate Pi-runner phases and are not inferred from transport time.', noSecretsPrinted: true }, null, 2) + '\n', { mode: 0o600 });
        }
    }
    finally {
        firstResponse?.end('data: [DONE]\n\n');
        if (previous === undefined)
            delete process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
        else
            process.env.PITWALL_WORKBENCH_RESOURCE_DIR = previous;
    }
});
test('queued provider request cancellation prevents a late transport request', { timeout: 10000 }, async () => {
    let requests = 0;
    let firstResponse;
    const server = createServer((_req, res) => {
        requests++;
        if (requests === 1) {
            firstResponse = res;
            res.writeHead(200, { 'content-type': 'text/event-stream' });
            return;
        }
        res.writeHead(401, { 'content-type': 'application/json' });
        res.end('{}');
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve()));
    const root = await mkdtemp(join(tmpdir(), 'pi-queued-cancel-'));
    const resourceDirectory = join(root, 'admission');
    const previous = process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
    process.env.PITWALL_WORKBENCH_RESOURCE_DIR = resourceDirectory;
    try {
        const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
        const profile = { profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'queued-cancel', servedContextTokens: 4096, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } };
        let first;
        let second;
        registerAdmissionProvider({ registerProvider: (_name, value) => { first = value; } }, profile, join(root, 'first.json'));
        registerAdmissionProvider({ registerProvider: (_name, value) => { second = value; } }, profile, join(root, 'second.json'));
        const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
        const firstResult = first.streamSimple(model, { messages: [{ role: 'user', content: 'hold' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        const deadline = Date.now() + 3_000;
        while (requests !== 1) {
            if (Date.now() > deadline)
                throw new Error('first request did not reach transport');
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        const controller = new AbortController();
        const secondResult = second.streamSimple(model, { messages: [{ role: 'user', content: 'queued' }] }, { apiKey: 'fixture', signal: controller.signal }).result(); // pragma: allowlist secret
        await new Promise(resolve => setTimeout(resolve, 60));
        controller.abort(new Error('queued request cancelled'));
        const cancelled = await secondResult;
        assert.ok((['error', 'aborted']).includes(cancelled.stopReason));
        assert.strictEqual(requests, 1);
        firstResponse.end(`data: ${JSON.stringify({ id: 'fixture', object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content: 'done' }, finish_reason: 'stop' }], usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } })}\n\ndata: [DONE]\n\n`);
        await firstResult;
        assert.strictEqual(requests, 1);
    }
    finally {
        if (previous === undefined)
            delete process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
        else
            process.env.PITWALL_WORKBENCH_RESOURCE_DIR = previous;
    }
});
test('hosted account reservation reconciles a reported successful call before the next call', async () => {
    let requests = 0;
    const server = createServer((_req, res) => {
        requests++;
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.flushHeaders();
        res.end(`data: ${JSON.stringify({ id: 'fixture', object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content: 'ok' }, finish_reason: 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 } })}\n\ndata: [DONE]\n\n`);
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve()));
    const root = await mkdtemp(join(tmpdir(), 'pi-hosted-budget-provider-'));
    const budgetDirectory = join(root, 'budget');
    const previous = process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
    process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR = budgetDirectory;
    const previousKey = process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
    process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = 'fixture'; // pragma: allowlist secret
    try {
        const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
        let registration;
        registerAdmissionProvider({ registerProvider: (_name, value) => { registration = value; } }, { profile: { provider: 'hosted-fixture', modelId: 'fixture', endpoint, apiKeyEnv: 'PITWALL_WORKBENCH_TEST_HOSTED_KEY', accountRef: 'shared-account', accountGroup: 'shared-account', accountMaxConcurrent: 1, accountInFlightTokenBudget: 1000, accountUnknownUsage: 'hold', api: 'openai-completions', resourceGroup: 'hosted-fixture', servedContextTokens: 4096, maxCompletionTokens: 32 }, providerConfig: { baseUrl: endpoint, apiKey: '$PITWALL_WORKBENCH_TEST_HOSTED_KEY', api: 'openai-completions', models: [{ id: 'fixture' }] } }, join(root, 'provider.json')); // pragma: allowlist secret
        const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'hosted-fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
        await registration.streamSimple(model, { messages: [{ role: 'user', content: 'hello' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        await registration.streamSimple(model, { messages: [{ role: 'user', content: 'hello' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        assert.strictEqual(requests, 2);
    }
    finally {
        if (previous === undefined)
            delete process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
        else
            process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR = previous;
        if (previousKey === undefined)
            delete process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
        else
            process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = previousKey;
    }
});
test('settlement timestamp excludes delayed account reconciliation', { timeout: 15000 }, async () => {
    const server = createServer((_req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.end(`data: ${JSON.stringify({ id: 'fixture', object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content: 'ok' }, finish_reason: 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 } })}\n\ndata: [DONE]\n\n`);
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const root = await mkdtemp(join(tmpdir(), 'pi-delayed-reconciliation-'));
    const budgetDirectory = join(root, 'budget');
    const previous = process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
    process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR = budgetDirectory;
    const previousKey = process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
    process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = 'fixture'; // pragma: allowlist secret
    const reconcileOriginal = AccountBudgetAdmission.prototype.reconcile;
    const reconcile = mock.method(AccountBudgetAdmission.prototype, 'reconcile', async function (reservation, usage) {
        await new Promise(resolve => setTimeout(resolve, 120));
        return reconcileOriginal.call(this, reservation, usage);
    });
    try {
        const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
        let registration;
        const profilePath = join(root, 'provider.json');
        registerAdmissionProvider({ registerProvider: (_name, value) => { registration = value; } }, { profile: { provider: 'hosted-fixture', modelId: 'fixture', endpoint, apiKeyEnv: 'PITWALL_WORKBENCH_TEST_HOSTED_KEY', accountRef: 'shared-account', accountGroup: 'delayed-account', accountMaxConcurrent: 1, accountInFlightTokenBudget: 1000, accountUnknownUsage: 'hold', api: 'openai-completions', resourceGroup: 'delayed-account', servedContextTokens: 4096, maxCompletionTokens: 32 }, providerConfig: { baseUrl: endpoint, apiKey: '$PITWALL_WORKBENCH_TEST_HOSTED_KEY', api: 'openai-completions', models: [{ id: 'fixture' }] } }, profilePath); // pragma: allowlist secret
        const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'hosted-fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
        await registration.streamSimple(model, { messages: [{ role: 'user', content: 'hello' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        let records = [];
        for (let attempt = 0; attempt < 100; attempt++) {
            try {
                records = (await readFile(join(root, 'native-accounting.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
            }
            catch {
                records = [];
            }
            if (records.some(record => record.type === 'native_released'))
                break;
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        const settled = records.find(record => record.type === 'native_settled');
        const released = records.find(record => record.type === 'native_released');
        assertMatch(settled?.settledAt, any(Number));
        assertMatch(released?.releasedAt, any(Number));
        assert.ok(released.releasedAt - settled.settledAt >= 100);
        const evidencePath = process.env.PITWALL_WORKBENCH_DELAYED_ADMISSION_EVIDENCE_PATH;
        if (evidencePath)
            await writeFile(evidencePath, JSON.stringify({ schemaVersion: 1, kind: 'workbench-admission-delayed-reconciliation', measuredAt: new Date().toISOString(), injectedReconciliationDelayMs: 120, settledAt: settled.settledAt, releasedAt: released.releasedAt, settledToReleasedMs: released.releasedAt - settled.settledAt, assertion: 'settledAt is captured before delayed account reconciliation; releasedAt follows admission release confirmation', noSecretsPrinted: true }, null, 2) + '\n', { mode: 0o600 });
    }
    finally {
        reconcile.mock.restore();
        if (previous === undefined)
            delete process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
        else
            process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR = previous;
        if (previousKey === undefined)
            delete process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY;
        else
            process.env.PITWALL_WORKBENCH_TEST_HOSTED_KEY = previousKey;
    }
});
test('request deadline closes stalled HTTP and releases resource for next request', async () => {
    let requests = 0, closed = 0;
    const server = createServer((req, res) => { requests++; req.resume(); res.on('close', () => closed++); if (requests > 1) {
        res.writeHead(401, { 'content-type': 'application/json' });
        res.end('{}');
    } });
    servers.push(server);
    await new Promise(r => server.listen(0, '127.0.0.1', r));
    const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    let registration;
    registerAdmissionProvider({ registerProvider: (_name, value) => registration = value }, { profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'deadline-fixture', requestTimeoutMs: 2000, servedContextTokens: 4096, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } }, '/tmp/deadline-provider-profile.json');
    const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
    const context = { messages: [{ role: 'user', content: 'hello', timestamp: Date.now() }] };
    const start = Date.now();
    await registration.streamSimple(model, context, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
    assert.ok(Date.now() - start < 5000);
    await registration.streamSimple(model, context, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
    assert.strictEqual(requests, 2);
    assert.ok(closed >= 1);
});
test('distinct inactivity and generation deadlines allow active long streams but stop stalls and overlong generation', async () => {
    const dispatcherPath = realpathSync(join(piModulesDirectory, '@earendil-works/pi-coding-agent/dist/core/http-dispatcher.js'));
    const { configureHttpDispatcher } = await import(pathToFileURL(dispatcherPath).href);
    const body = (n, complete) => `data: ${JSON.stringify({ id: `fixture-${n}`, object: 'chat.completion.chunk', choices: [{ index: 0, delta: { content: 'x' }, finish_reason: complete && n >= 4 ? 'stop' : null }] })}\n\n`;
    let mode = 'stall';
    const server = createServer((_req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.flushHeaders();
        if (mode === 'stall')
            return;
        let n = 0;
        const timer = setInterval(() => {
            res.write(body(n++, mode === 'active'));
            if (mode === 'active' && n >= 5) {
                clearInterval(timer);
                res.end('data: [DONE]\n\n');
            }
        }, 20);
        res.on('close', () => clearInterval(timer));
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'timeout-fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 4096, maxTokens: 32 };
    let registration;
    registerAdmissionProvider({ registerProvider: (_name, value) => { registration = value; } }, { profile: { provider: 'timeout-fixture', api: 'openai-completions', resourceGroup: 'timeout-fixture', requestInactivityTimeoutMs: 250, generationTimeoutMs: 1500, servedContextTokens: 4096, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } }, join(tmpdir(), 'timeout-provider.json'));
    try {
        configureHttpDispatcher(250);
        const stalledStart = Date.now();
        const stalled = await registration.streamSimple(model, { messages: [{ role: 'user', content: 'stall' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        assert.ok((['error', 'aborted']).includes(stalled.stopReason));
        assert.match(stalled.errorMessage ?? '', /body|headers|inactiv|timeout/i);
        // Inactivity (250ms) must end the stall before the 1500ms generation limit could.
        assert.ok(Date.now() - stalledStart < 1_400);
        mode = 'active';
        const activeStart = Date.now();
        const active = await registration.streamSimple(model, { messages: [{ role: 'user', content: 'active' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        assert.strictEqual(active.stopReason, 'stop');
        assert.ok(Date.now() - activeStart >= 40);
        mode = 'overlong';
        const overlongStart = Date.now();
        const overlong = await registration.streamSimple(model, { messages: [{ role: 'user', content: 'overlong' }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
        assert.ok((['error', 'aborted']).includes(overlong.stopReason));
        assert.match(overlong.errorMessage ?? '', /generation|timeout|abort/i);
        assert.doesNotMatch(overlong.errorMessage ?? '', /body|headers|inactiv/i);
        // The configured total-generation limit is 1500ms and the fixture never ends the stream, so
        // the limit is what settled it. Reject an unrelated immediate abort (lower bound) and a limit
        // that was ignored for the 120s default (upper bound); a loaded event loop can delay the
        // abort well past 1500ms, so the upper bound is not a tolerance on the limit itself.
        const overlongElapsed = Date.now() - overlongStart;
        assert.ok(overlongElapsed >= 1_200, `settled after ${overlongElapsed}ms`);
        assert.ok(overlongElapsed < 5_000, `settled after ${overlongElapsed}ms`);
    }
    finally {
        configureHttpDispatcher(300_000);
    }
});
test('context guard rejects oversized input before any transport request', async () => {
    let requests = 0;
    const server = createServer((_req, res) => { requests++; res.end('{}'); });
    servers.push(server);
    await new Promise(r => server.listen(0, '127.0.0.1', r));
    const endpoint = `http://127.0.0.1:${server.address().port}/v1`;
    let registration;
    registerAdmissionProvider({ registerProvider: (_n, v) => registration = v }, { profile: { provider: 'fixture', api: 'openai-completions', resourceGroup: 'context-fixture', servedContextTokens: 100, maxCompletionTokens: 32, contextReserveTokens: 1 }, providerConfig: { baseUrl: endpoint, api: 'openai-completions', models: [] } }, '/tmp/context-provider-profile.json');
    const model = { id: 'fixture', name: 'fixture', api: 'openai-completions', provider: 'fixture', baseUrl: endpoint, reasoning: false, input: ['text'], contextWindow: 100, maxTokens: 32 };
    const result = await registration.streamSimple(model, { messages: [{ role: 'user', content: 'x'.repeat(5000), timestamp: Date.now() }] }, { apiKey: 'fixture' }).result(); // pragma: allowlist secret
    assert.strictEqual(result.stopReason, 'error');
    assert.ok(result.errorMessage.includes('estimated context'));
    assert.strictEqual(requests, 0);
});
