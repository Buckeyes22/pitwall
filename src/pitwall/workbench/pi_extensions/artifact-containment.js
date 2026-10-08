import { lstatSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";
function isWithin(root, candidate) {
    const distance = relative(root, candidate);
    return distance === "" || (distance !== ".." && !distance.startsWith(`..${sep}`) && !isAbsolute(distance));
}
/**
 * Validate an artifact path before exposing or persisting it in a task record.
 * The artifact must be an existing regular file owned by this task's record
 * directory.  realpath checks cover both final and intermediate symlink escapes.
 */
export function assertTaskArtifactPath(path, options) {
    if (typeof path !== "string" || !path || !isAbsolute(path))
        throw new Error("artifact path must be an absolute path");
    if (!/^task-[A-Za-z0-9_-]+$/.test(options.taskId))
        throw new Error("artifact task identity is invalid");
    const lexicalRoot = resolve(options.root);
    let root;
    try {
        root = realpathSync(lexicalRoot);
    }
    catch {
        throw new Error("artifact root is unavailable");
    }
    const candidate = resolve(path);
    if (!isWithin(lexicalRoot, candidate))
        throw new Error("artifact path escapes task record directory");
    const expectedPrefix = `${options.taskId}.${options.kind}-`;
    const expectedName = new RegExp(`^${options.taskId}\\.${options.kind}-[0-9a-f]{16}\\.txt$`);
    if (!basename(candidate).startsWith(expectedPrefix) || !expectedName.test(basename(candidate)))
        throw new Error("artifact path is not owned by task");
    let resolved;
    try {
        resolved = realpathSync(candidate);
    }
    catch {
        throw new Error("artifact path is unavailable");
    }
    if (!isWithin(root, resolved))
        throw new Error("artifact path symlink escapes task record directory");
    if (dirname(resolved) !== root || !expectedName.test(basename(resolved)))
        throw new Error("artifact path symlink is not owned by task");
    let stat;
    try {
        stat = lstatSync(candidate);
    }
    catch {
        throw new Error("artifact path is unavailable");
    }
    if (!stat.isFile() || stat.isSymbolicLink())
        throw new Error("artifact path must be a regular file");
}
