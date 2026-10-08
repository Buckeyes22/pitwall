import { realpathSync, existsSync, writeFileSync, mkdtempSync, openSync, closeSync, unlinkSync, rmdirSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { tmpdir } from 'node:os';
import { setprivSupportsSeccompFilter } from './setpriv.js';
import { resolve, dirname, join } from 'node:path';
export { setprivSupportsSeccompFilter } from './setpriv.js';
import { createBashToolDefinition } from '@earendil-works/pi-coding-agent';
/** Filesystem containment for the complete trusted Pi process tree, not a network sandbox. */
export function restrictedCommand(command, args, options) {
    assertRestrictedPrerequisites();
    const cwd = realpathSync(options.cwd), agentDir = realpathSync(options.agentDir), runtime = realpathSync(options.runtimeRoot), runtimeDependencies = (options.runtimeDependencies ?? []).map(path => realpathSync(path)), admission = realpathSync(options.admissionDir), accountBudget = options.accountBudgetDir ? realpathSync(options.accountBudgetDir) : undefined;
    if ([cwd, agentDir, runtime, ...runtimeDependencies, admission, accountBudget].filter((path) => Boolean(path)).some(path => path === '/' || path === '/home' || path === '/root'))
        throw new Error('restricted mounts must name specific owned directories');
    const mounts = ['--die-with-parent', '--new-session', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--ro-bind', '/', '/', '--tmpfs', '/home', '--tmpfs', '/root', '--tmpfs', '/run', '--tmpfs', '/tmp', '--proc', '/proc', '--dev', '/dev'];
    // The package runtime and any npm-hoisted dependency roots are read-only; owned state is writable.
    const writable = new Set([cwd, agentDir, admission, ...(accountBudget ? [accountBudget] : [])]);
    const readOnly = new Set([runtime, ...runtimeDependencies]);
    for (const path of [...new Set([...readOnly, ...writable])].sort((a, b) => a.length - b.length)) {
        mounts.push('--dir', dirname(path), readOnly.has(path) && !writable.has(path) ? '--ro-bind' : '--bind', path, path);
    }
    mounts.push('--chdir', cwd, '--', resolve(command), ...args);
    return { command: '/usr/bin/bwrap', args: mounts };
}
/**
 * The trusted Pi process needs the selected provider credential for inference,
 * but a shell tool child does not.  Keep the child environment useful for
 * normal repository commands while removing the selected credential and the
 * conventional credential channels that could be supplied through an
 * explicit passthrough environment.
 */
export function restrictedToolEnvironment(source, credentialEnv) {
    const environment = { ...source };
    const credentialName = credentialEnv?.trim();
    const conventionalCredential = /(?:^|_)(?:API[_-]?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH(?:ORIZATION)?)(?:_|$)/i;
    for (const name of Object.keys(environment)) {
        if (name === credentialName || conventionalCredential.test(name))
            delete environment[name];
    }
    return environment;
}
const restrictedArchitectures = new Set(['x64', 'arm64']);
/** Fail before starting Pi when the restricted tool boundary cannot be enforced. */
export function assertRestrictedPrerequisites(host = {}) {
    const platform = host.platform ?? process.platform;
    const arch = host.arch ?? process.arch;
    const pathExists = host.pathExists ?? existsSync;
    if (platform !== 'linux')
        throw new Error('restricted mode requires Linux; refusing an unrestricted fallback');
    if (!pathExists('/usr/bin/bwrap'))
        throw new Error('restricted mode requires Linux bubblewrap; refusing an unrestricted fallback');
    if (!pathExists('/usr/bin/setpriv'))
        throw new Error('restricted mode requires /usr/bin/setpriv for the tool network boundary');
    if (!restrictedArchitectures.has(arch))
        throw new Error(`restricted tool network boundary is unsupported on ${arch}`);
    if (!(host.setprivSupportsSeccompFilter ?? setprivSupportsSeccompFilter)()) {
        throw new Error('restricted mode requires util-linux 2.41 or later: /usr/bin/setpriv lacks --seccomp-filter');
    }
}
function seccompFilter() {
    const architectures = {
        // io_uring can issue socket operations without the classic socket syscalls,
        // so setup/enter/register are denied alongside the socket family.
        x64: { audit: 0xc000003e, syscalls: [41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 288, 299, 307, 425, 426, 427] },
        arm64: { audit: 0xc00000b7, syscalls: [198, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 242, 243, 269, 417, 425, 426, 427] },
    };
    const architecture = architectures[process.arch];
    if (!architecture)
        throw new Error(`restricted tool network boundary is unsupported on ${process.arch}`);
    const instructions = [];
    const add = (code, jt, jf, k) => instructions.push({ code, jt, jf, k });
    add(0x20, 0, 0, 4); // BPF_LD | BPF_W | BPF_ABS, seccomp_data.arch
    add(0x15, 1, 0, architecture.audit); // kill an unexpected syscall ABI
    add(0x06, 0, 0, 0x80000000); // SECCOMP_RET_KILL_PROCESS
    add(0x20, 0, 0, 0); // BPF_LD | BPF_W | BPF_ABS, seccomp_data.nr
    if (process.arch === 'x64') {
        // x32 uses the x86_64 audit ABI with a high syscall-number tag. Reject
        // that ABI before the exact x86_64 socket numbers are considered.
        add(0x54, 0, 0, 0x40000000); // BPF_ALU | BPF_AND | BPF_K
        add(0x15, 1, 0, 0); // an untagged syscall skips the kill instruction
        add(0x06, 0, 0, 0x80000000); // SECCOMP_RET_KILL_PROCESS
        add(0x20, 0, 0, 0); // reload seccomp_data.nr for the deny list
    }
    for (const syscall of architecture.syscalls) {
        add(0x15, 0, 1, syscall); // matching syscall falls through to EPERM
        add(0x06, 0, 0, 0x00050001); // SECCOMP_RET_ERRNO | EPERM
    }
    add(0x06, 0, 0, 0x7fff0000); // SECCOMP_RET_ALLOW
    const filter = Buffer.alloc(instructions.length * 8);
    instructions.forEach((instruction, index) => {
        const offset = index * 8;
        filter.writeUInt16LE(instruction.code, offset);
        filter[offset + 2] = instruction.jt;
        filter[offset + 3] = instruction.jf;
        filter.writeUInt32LE(instruction.k >>> 0, offset + 4);
    });
    return filter;
}
let networkFilterBytes;
function ensureNetworkFilter() {
    if (networkFilterBytes)
        return networkFilterBytes;
    assertRestrictedPrerequisites();
    networkFilterBytes = seccompFilter();
    return networkFilterBytes;
}
function openNetworkFilter() {
    const directory = mkdtempSync(join(tmpdir(), 'pi-workbench-seccomp-'));
    const path = join(directory, 'deny-network.bpf');
    try {
        writeFileSync(path, ensureNetworkFilter(), { mode: 0o600, flag: 'wx' });
        const fd = openSync(path, 'r');
        unlinkSync(path);
        rmdirSync(directory);
        return fd;
    }
    catch (error) {
        try {
            unlinkSync(path);
        }
        catch { }
        try {
            rmdirSync(directory);
        }
        catch { }
        throw error;
    }
}
function killProcessTree(child) {
    if (!child.pid)
        return;
    try {
        process.kill(-child.pid, 'SIGKILL');
    }
    catch {
        child.kill('SIGKILL');
    }
}
/** Run shell tools with a syscall boundary while the Pi parent keeps provider connectivity. */
export function restrictedBashOperations(options) {
    return {
        exec: async (command, cwd, { onData, signal, timeout, env }) => {
            const filterFd = openNetworkFilter();
            let child;
            try {
                child = spawn('/usr/bin/setpriv', ['--no-new-privs', '--seccomp-filter', '/proc/self/fd/3', '--', '/bin/bash', '-c', command], { cwd, detached: true, env: restrictedToolEnvironment(env ?? process.env, options.credentialEnv), stdio: ['ignore', 'pipe', 'pipe', filterFd] });
            }
            catch (error) {
                closeSync(filterFd);
                throw error;
            }
            closeSync(filterFd);
            let timedOut = false;
            let timeoutHandle;
            const onAbort = () => killProcessTree(child);
            child.stdout?.on('data', onData);
            child.stderr?.on('data', onData);
            if (timeout !== undefined && timeout > 0)
                timeoutHandle = setTimeout(() => { timedOut = true; killProcessTree(child); }, timeout * 1000);
            if (signal)
                signal.aborted ? onAbort() : signal.addEventListener('abort', onAbort, { once: true });
            try {
                const exitCode = await new Promise((resolveExit, reject) => { child.once('error', reject); child.once('close', code => resolveExit(code)); });
                if (signal?.aborted)
                    throw new Error('aborted');
                if (timedOut)
                    throw new Error(`timeout:${timeout}`);
                return { exitCode };
            }
            finally {
                if (timeoutHandle)
                    clearTimeout(timeoutHandle);
                signal?.removeEventListener('abort', onAbort);
            }
        },
    };
}
export function restrictedPathsFromEnvironment(cwd) {
    const runtimeRoot = process.env.PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT;
    const agentDir = process.env.PI_CODING_AGENT_DIR;
    const admissionDir = process.env.PITWALL_WORKBENCH_RESOURCE_DIR;
    const accountBudgetDir = process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR;
    const credentialEnv = process.env.PITWALL_WORKBENCH_RESTRICTED_CREDENTIAL_ENV;
    if (!runtimeRoot || !agentDir || !admissionDir)
        throw new Error('restricted tool boundary requires runtime, agent, and admission directories');
    return { cwd, agentDir, runtimeRoot, admissionDir, ...(accountBudgetDir ? { accountBudgetDir } : {}), ...(credentialEnv ? { credentialEnv } : {}) };
}
export function registerRestrictedBashTool(pi) {
    if (process.env.PITWALL_WORKBENCH_RESTRICTED !== '1')
        return;
    const paths = restrictedPathsFromEnvironment(process.cwd());
    pi.registerTool(createBashToolDefinition(paths.cwd, { operations: restrictedBashOperations(paths) }));
}
