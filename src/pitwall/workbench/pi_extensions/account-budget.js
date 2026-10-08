import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { constants } from "node:fs";
import { mkdir, lstat, open } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
const STATE_SCRIPT = String.raw `
const fs = require("node:fs");
const statePath = process.argv[1];
const operation = JSON.parse(process.argv[2]);
const now = Date.now();
const fail = (code, reason) => { process.stdout.write(JSON.stringify({ ok: false, code, reason })); process.exit(0); };
let state = { schemaVersion: 1, accountGroup: operation.accountGroup, policy: { maxConcurrent: operation.maxConcurrent, inFlightTokenBudget: operation.inFlightTokenBudget, unknownUsage: operation.unknownUsage }, reservations: {} };
try { state = JSON.parse(fs.readFileSync(statePath, "utf8")); } catch (error) { if (error.code !== "ENOENT") fail("invalid-state", "account budget state could not be read"); }
if (state.schemaVersion !== 1 || state.accountGroup !== operation.accountGroup || !state.reservations || typeof state.reservations !== "object" || Array.isArray(state.reservations)) fail("invalid-state", "account budget state is invalid");
const requestedPolicy = { maxConcurrent: operation.maxConcurrent, inFlightTokenBudget: operation.inFlightTokenBudget, unknownUsage: operation.unknownUsage };
if (state.policy === undefined) state.policy = requestedPolicy;
if (!state.policy || typeof state.policy !== "object" || Array.isArray(state.policy)) fail("invalid-state", "account budget policy is invalid");
const policy = state.policy ?? requestedPolicy;
if (!Number.isSafeInteger(policy.maxConcurrent) || policy.maxConcurrent < 1 || !Number.isSafeInteger(policy.inFlightTokenBudget) || policy.inFlightTokenBudget < 1 || (policy.unknownUsage !== "hold" && policy.unknownUsage !== "release")) fail("invalid-state", "account budget policy is invalid");
if (state.policy.maxConcurrent !== requestedPolicy.maxConcurrent || state.policy.inFlightTokenBudget !== requestedPolicy.inFlightTokenBudget || state.policy.unknownUsage !== requestedPolicy.unknownUsage) fail("policy-conflict", "account budget policy conflicts with existing account-group policy");
for (const [id, reservation] of Object.entries(state.reservations)) {
  if (!id || !reservation || typeof reservation !== "object" || Array.isArray(reservation) || !Number.isSafeInteger(reservation.reservedTokens) || reservation.reservedTokens <= 0 || !Number.isSafeInteger(reservation.ownerPid) || reservation.ownerPid < 0 || (reservation.status !== "active" && reservation.status !== "unknown") || (reservation.status === "active" && reservation.ownerPid === 0)) fail("invalid-state", "account budget reservation is invalid");
  let alive = true;
  try { process.kill(Number(reservation.ownerPid), 0); } catch (error) { if (error.code === "ESRCH") alive = false; }
  if (!alive && reservation.status === "active") { reservation.status = "unknown"; reservation.ownerPid = 0; reservation.updatedAt = now; }
}
const reservations = Object.values(state.reservations);
if (operation.kind === "reserve") {
  let reserved = 0;
  for (const item of reservations) {
    reserved += item.reservedTokens;
    if (!Number.isSafeInteger(reserved)) fail("invalid-state", "account budget reservation total is invalid");
  }
  if (!Number.isSafeInteger(operation.tokens) || operation.tokens <= 0 || operation.tokens > operation.inFlightTokenBudget) fail("invalid-operation", "account budget reservation is invalid");
  if (reservations.length >= operation.maxConcurrent) process.stdout.write(JSON.stringify({ ok: false, code: "concurrency-limit", reason: "account concurrency limit reached", reservedTokens: reserved }));
  else if (!Number.isSafeInteger(reserved + operation.tokens) || reserved + operation.tokens > operation.inFlightTokenBudget) process.stdout.write(JSON.stringify({ ok: false, code: "budget-exhausted", reason: "account in-flight budget exhausted", reservedTokens: reserved }));
  else {
    const id = operation.reservationId;
    state.reservations[id] = { ownerPid: operation.ownerPid, reservedTokens: operation.tokens, status: "active", createdAt: now };
    fs.writeFileSync(statePath, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
    process.stdout.write(JSON.stringify({ ok: true, reservationId: id, reservationTokens: operation.tokens, totalReservedTokens: reserved + operation.tokens }));
  }
} else if (operation.kind === "reconcile") {
  const item = state.reservations[operation.reservationId];
  if (!item) process.stdout.write(JSON.stringify({ ok: false, code: "reservation-not-found", reason: "reservation not found" }));
  else if (operation.usageTokens === null && operation.unknownUsage === "hold") {
    item.status = "unknown";
    item.updatedAt = now;
    fs.writeFileSync(statePath, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
    process.stdout.write(JSON.stringify({ ok: true, held: true, reservedTokens: item.reservedTokens }));
  } else {
    const reservedTokens = Number(item.reservedTokens);
    delete state.reservations[operation.reservationId];
    fs.writeFileSync(statePath, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
    const reportedTokens = operation.usageTokens === null ? null : Number(operation.usageTokens);
    process.stdout.write(JSON.stringify({ ok: true, held: false, reservedTokens, reportedTokens, overrunTokens: reportedTokens === null ? null : Math.max(0, reportedTokens - reservedTokens) }));
  }
} else if (operation.kind === "release") {
  delete state.reservations[operation.reservationId];
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
  process.stdout.write(JSON.stringify({ ok: true }));
} else fail("invalid-operation", "unknown account budget operation");
`;
function validatePolicy(policy) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(policy.accountGroup))
        throw new Error("account budget requires a valid accountGroup");
    if (!Number.isSafeInteger(policy.maxConcurrent) || policy.maxConcurrent < 1)
        throw new Error("account budget maxConcurrent must be positive");
    if (!Number.isSafeInteger(policy.inFlightTokenBudget) || policy.inFlightTokenBudget < 1)
        throw new Error("account budget inFlightTokenBudget must be positive");
    if (policy.unknownUsage !== "hold" && policy.unknownUsage !== "release")
        throw new Error("account budget unknownUsage must be hold or release");
}
function usageTokens(usage) {
    if (!usage || typeof usage !== "object")
        return null;
    if (typeof usage.totalTokens === "number" && Number.isSafeInteger(usage.totalTokens) && usage.totalTokens >= 0)
        return usage.totalTokens;
    const input = typeof usage.input === "number" && Number.isSafeInteger(usage.input) && usage.input >= 0 ? usage.input : undefined;
    const output = typeof usage.output === "number" && Number.isSafeInteger(usage.output) && usage.output >= 0 ? usage.output : undefined;
    if (input === undefined || output === undefined)
        return null;
    const total = input + output;
    return Number.isSafeInteger(total) ? total : null;
}
export class AccountBudgetAdmission {
    policy;
    directory;
    statePath;
    lockPath;
    constructor(policy, directory = process.env.PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR ?? join(homedir(), ".local/state/pitwall/pi-workbench/account-budgets")) {
        validatePolicy(policy);
        this.policy = { ...policy };
        this.directory = resolve(directory);
        const identity = createHash("sha256").update(policy.accountGroup).digest("hex");
        this.statePath = join(this.directory, `${identity}.json`);
        this.lockPath = join(this.directory, `${identity}.lock`);
    }
    async operation(operation, signal) {
        if (process.platform !== "linux")
            throw new Error("account budget admission requires Linux flock; no process-local fallback");
        await mkdir(this.directory, { recursive: true, mode: 0o700 });
        const stat = await lstat(this.directory);
        if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o077) || stat.uid !== process.getuid?.())
            throw new Error("account budget directory must be private and owned by this user");
        const lockFile = await open(this.lockPath, constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW, 0o600);
        try {
            const lockStat = await lockFile.stat();
            if (!lockStat.isFile() || (lockStat.mode & 0o077) || lockStat.uid !== process.getuid?.())
                throw new Error("account budget lock must be a private user-owned regular file");
        }
        finally {
            await lockFile.close();
        }
        try {
            const stateStat = await lstat(this.statePath);
            if (!stateStat.isFile() || stateStat.isSymbolicLink() || (stateStat.mode & 0o077) || stateStat.uid !== process.getuid?.())
                throw new Error("account budget state must be a private user-owned regular file");
        }
        catch (error) {
            if (error.code !== "ENOENT")
                throw error;
        }
        const args = ["--exclusive", "--no-fork", this.lockPath, process.execPath, "-e", STATE_SCRIPT, this.statePath, JSON.stringify({ ...operation, accountGroup: this.policy.accountGroup, maxConcurrent: this.policy.maxConcurrent, inFlightTokenBudget: this.policy.inFlightTokenBudget, unknownUsage: this.policy.unknownUsage })];
        const value = await new Promise((resolveResult, reject) => {
            const child = spawn("flock", args, { stdio: ["ignore", "pipe", "pipe"], env: { PATH: process.env.PATH } });
            let output = "";
            let settled = false;
            const abort = () => { if (settled)
                return; child.kill("SIGTERM"); settled = true; reject(signal?.reason ?? new Error("Request cancelled")); };
            if (signal?.aborted) {
                abort();
                return;
            }
            signal?.addEventListener("abort", abort, { once: true });
            child.stdout.on("data", (part) => { output += part.toString(); if (output.length > 1024 * 1024)
                abort(); });
            child.stderr.resume();
            child.once("error", (error) => { if (settled)
                return; settled = true; signal?.removeEventListener("abort", abort); reject(error); });
            child.once("close", (code) => { if (settled)
                return; settled = true; signal?.removeEventListener("abort", abort); if (code !== 0)
                reject(new Error(`account budget operation exited ${code}`));
            else {
                try {
                    resolveResult(JSON.parse(output));
                }
                catch (error) {
                    reject(error);
                }
            } });
        });
        if (value.ok !== true) {
            const code = typeof value.code === "string" ? value.code : "rejected";
            const reason = typeof value.reason === "string" ? value.reason : "account budget operation rejected";
            throw new Error(`${code}: ${reason}`);
        }
        return value;
    }
    async reserve(tokens, signal) {
        if (!Number.isSafeInteger(tokens) || tokens <= 0 || tokens > this.policy.inFlightTokenBudget)
            throw new Error("account reservation exceeds explicit budget policy");
        if (signal?.aborted)
            throw signal.reason ?? new Error("Request cancelled");
        const reservationId = `reservation-${randomUUID()}`;
        try {
            const value = await this.operation({ kind: "reserve", reservationId, ownerPid: process.pid, tokens }, signal);
            return { reservationId, reservedTokens: Number(value.reservationTokens ?? tokens) };
        }
        catch (error) {
            if (signal?.aborted)
                void this.operation({ kind: "release", reservationId }).catch(() => undefined);
            throw error;
        }
    }
    async reconcile(reservation, usage) {
        const value = await this.operation({ kind: "reconcile", reservationId: reservation.reservationId, usageTokens: usageTokens(usage), unknownUsage: this.policy.unknownUsage });
        return { held: value.held === true, reservedTokens: Number(value.reservedTokens ?? reservation.reservedTokens), reportedTokens: value.reportedTokens === null || value.reportedTokens === undefined ? null : Number(value.reportedTokens), overrunTokens: value.overrunTokens === null || value.overrunTokens === undefined ? null : Number(value.overrunTokens) };
    }
    async release(reservation) {
        await this.operation({ kind: "release", reservationId: reservation.reservationId });
    }
}
export function accountBudgetPolicyFromProfile(profile) {
    const values = [profile.accountGroup, profile.accountMaxConcurrent, profile.accountInFlightTokenBudget, profile.accountUnknownUsage];
    if (values.every((value) => value === undefined))
        return undefined;
    if (typeof profile.accountGroup !== "string" || typeof profile.accountMaxConcurrent !== "number" || typeof profile.accountInFlightTokenBudget !== "number" || !profile.accountUnknownUsage)
        throw new Error("hosted account budget policy must specify accountGroup, maxConcurrent, inFlightTokenBudget, and unknownUsage together");
    return { accountGroup: profile.accountGroup, maxConcurrent: profile.accountMaxConcurrent, inFlightTokenBudget: profile.accountInFlightTokenBudget, unknownUsage: profile.accountUnknownUsage };
}
