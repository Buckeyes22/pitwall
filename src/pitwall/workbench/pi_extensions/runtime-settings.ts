import { lstat, readFile, writeFile, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import type { WorkbenchProfile } from './profile.js';

export type DerivedCompactionSettings = { enabled: true; reserveTokens: number; keepRecentTokens: number; safetyReserveTokens: number };

export function safetyReserveTokens(profile: Pick<WorkbenchProfile, 'servedContextTokens' | 'maxCompletionTokens' | 'contextReserveTokens'>): number {
  return profile.contextReserveTokens ?? Math.min(profile.maxCompletionTokens, Math.max(1, Math.floor(profile.servedContextTokens * 0.1)));
}

export function deriveCompactionSettings(profile: Pick<WorkbenchProfile, 'servedContextTokens' | 'maxCompletionTokens' | 'contextReserveTokens'>): DerivedCompactionSettings {
  const safetyReserve = safetyReserveTokens(profile);
  const reserveTokens = profile.maxCompletionTokens + safetyReserve;
  if (reserveTokens >= profile.servedContextTokens) throw new Error('profile maxCompletionTokens plus context safety reserve must be below served context');
  // Pi uses up to 80% of reserveTokens for the generated summary. Retain at
  // most half of the remaining context for conversation history: the other
  // half must absorb the system prompt, tool schemas, and provider overhead.
  // This also leaves enough discarded history for prepareCompaction() to find
  // a real cut point when the usage threshold is crossed, instead of entering
  // a no-op automatic-compaction loop on a small context profile.
  const summaryBudget = Math.min(Math.floor(reserveTokens * 0.8), profile.maxCompletionTokens);
  const remainingContext = profile.servedContextTokens - reserveTokens - summaryBudget - safetyReserve;
  const keepRecentTokens = Math.min(20_000, Math.floor(remainingContext / 2));
  if (keepRecentTokens <= 0) throw new Error('profile leaves no room for recent context after compaction reserve');
  return { enabled: true, reserveTokens, keepRecentTokens, safetyReserveTokens: safetyReserve };
}

function checkProjectCompaction(project: Record<string, any>, compaction: DerivedCompactionSettings): void {
  const value = project.compaction;
  if (value === undefined) return;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('project compaction settings conflict with Workbench profile policy');
  if (Object.prototype.hasOwnProperty.call(value, 'enabled') && value.enabled !== true) throw new Error('project compaction settings conflict with Workbench profile policy');
  if (Object.prototype.hasOwnProperty.call(value, 'reserveTokens') && value.reserveTokens !== compaction.reserveTokens) throw new Error('project compaction settings conflict with Workbench profile policy');
  if (Object.prototype.hasOwnProperty.call(value, 'keepRecentTokens') && value.keepRecentTokens !== compaction.keepRecentTokens) throw new Error('project compaction settings conflict with Workbench profile policy');
}

export async function enforceRuntimeSettings(agentDir: string, cwd?: string, profile?: Pick<WorkbenchProfile, 'servedContextTokens' | 'maxCompletionTokens' | 'contextReserveTokens' | 'requestTimeoutMs' | 'requestInactivityTimeoutMs'>): Promise<void> {
  const path = join(agentDir, 'settings.json');
  let settings: Record<string, any> = {};
  try { if (!(await lstat(path)).isFile() || (await lstat(path)).isSymbolicLink()) throw new Error('runtime settings must be a regular file'); settings = JSON.parse(await readFile(path, 'utf8')); }
  catch(error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error('invalid runtime settings');
  if (cwd) {
    try {
      const project = JSON.parse(await readFile(join(cwd, '.pi/settings.json'), 'utf8')) as Record<string, any>;
      const retry = project && typeof project === 'object' && !Array.isArray(project) ? project.retry : undefined;
      if (retry !== undefined) {
        if (!retry || typeof retry !== 'object' || Array.isArray(retry) || (Object.prototype.hasOwnProperty.call(retry, 'enabled') && retry.enabled !== false) || (Object.prototype.hasOwnProperty.call(retry, 'maxRetries') && retry.maxRetries !== 0)) throw new Error('project retry settings conflict with Workbench no-retry policy');
        const provider = retry.provider;
        if (provider !== undefined && (!provider || typeof provider !== 'object' || Array.isArray(provider) || (Object.prototype.hasOwnProperty.call(provider, 'maxRetries') && provider.maxRetries !== 0))) throw new Error('project retry settings conflict with Workbench no-retry policy');
      }
      if (profile) checkProjectCompaction(project, deriveCompactionSettings(profile));
      const idleTimeout = profile?.requestInactivityTimeoutMs ?? profile?.requestTimeoutMs;
      if (idleTimeout !== undefined && Object.prototype.hasOwnProperty.call(project, 'httpIdleTimeoutMs') && project.httpIdleTimeoutMs !== idleTimeout) throw new Error('project HTTP inactivity timeout conflicts with Workbench profile policy');
    }
    catch(error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  }
  const derived = profile ? deriveCompactionSettings(profile) : undefined;
  const idleTimeout = profile?.requestInactivityTimeoutMs ?? profile?.requestTimeoutMs;
  const updated={...settings,
    ...(idleTimeout !== undefined ? { httpIdleTimeoutMs: idleTimeout } : {}),
    ...(derived ? { compaction: { ...settings.compaction, enabled: true, reserveTokens: derived.reserveTokens, keepRecentTokens: derived.keepRecentTokens } } : {}),
    retry:{...settings.retry,enabled:false,maxRetries:0,provider:{...settings.retry?.provider,maxRetries:0}}};
  const content=JSON.stringify(updated,null,2)+'\n';
  if (JSON.stringify(settings) === JSON.stringify(updated)) return;
  const temporary=join(agentDir,`.settings-${randomUUID()}.tmp`);
  await writeFile(temporary,content,{flag:'wx',mode:0o600});await rename(temporary,path);
}
