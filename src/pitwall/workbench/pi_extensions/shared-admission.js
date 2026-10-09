import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstat, mkdir, open } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { constants } from 'node:fs';
/** Host-local, kernel-owned request lease. Never held while a tool or child is awaited. */
export class SharedRequestAdmission {
    resourceGroup;
    directory;
    constructor(resourceGroup, directory = process.env.PITWALL_WORKBENCH_RESOURCE_DIR ?? join(process.env.XDG_STATE_HOME?.trim() || join(homedir(), '.local/state'), 'pitwall/pi-workbench/admission')) {
        this.resourceGroup = resourceGroup;
        this.directory = directory;
        if (!resourceGroup.trim() || resourceGroup.length > 200)
            throw new Error('invalid admission resource group');
    }
    async acquire(signal) {
        if (process.platform !== 'linux')
            throw new Error('shared request admission requires Linux flock; no process-local fallback');
        if (signal?.aborted)
            throw signal.reason ?? new Error('Request cancelled');
        const directory = resolve(this.directory);
        await mkdir(directory, { recursive: true, mode: 0o700 });
        const stat = await lstat(directory);
        if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o077) || stat.uid !== process.getuid?.())
            throw new Error('admission directory must be private and owned by this user');
        const path = join(directory, createHash('sha256').update(this.resourceGroup).digest('hex') + '.lock');
        const file = await open(path, constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW, 0o600);
        try {
            const metadata = await file.stat();
            if (!metadata.isFile() || metadata.uid !== process.getuid?.() || (metadata.mode & 0o077))
                throw new Error('admission lock must be a private regular file');
        }
        finally {
            await file.close();
        }
        if (signal?.aborted)
            throw signal.reason ?? new Error('Request cancelled');
        return new Promise((resolvePermit, reject) => {
            // --no-fork makes Node the lease owner. Parent crash closes stdin; EOF releases
            // the kernel lock. No stale-lock deletion, PID reuse heuristic, or lease stealing.
            const child = spawn('flock', ['--exclusive', '--no-fork', path, process.execPath, '-e', "process.stdin.resume();process.stdin.on('end',()=>process.exit(0));process.stdout.write('acquired\\n');"], { stdio: ['pipe', 'pipe', 'pipe'], env: { PATH: process.env.PATH } });
            let granted = false, released = false, output = '';
            let resolveReleased;
            const releasedPromise = new Promise((resolve) => { resolveReleased = resolve; });
            const release = () => {
                if (!released) {
                    released = true;
                    signal?.removeEventListener('abort', abort);
                    child.stdin.end();
                }
                return releasedPromise;
            };
            const abort = () => {
                // Once granted, the transport owns the permit until its result settles.
                // Aborting the request signal must not release the host lock early.
                if (granted)
                    return;
                reject(signal?.reason ?? new Error('Request cancelled'));
                release();
                child.kill('SIGTERM');
            };
            signal?.addEventListener('abort', abort, { once: true });
            child.stdin.on('error', () => { });
            child.stderr.resume();
            child.on('error', () => { release(); reject(new Error('shared admission could not start flock')); });
            child.on('exit', () => {
                signal?.removeEventListener('abort', abort);
                resolveReleased?.();
                if (!granted && !released)
                    reject(new Error('shared admission ended before acquisition'));
            });
            child.stdout.on('data', (bytes) => {
                output += bytes.toString();
                if (!granted && output.includes('acquired\n')) {
                    if (signal?.aborted || released) {
                        abort();
                        return;
                    }
                    granted = true;
                    resolvePermit(release);
                }
            });
            if (signal?.aborted)
                abort();
        });
    }
}
