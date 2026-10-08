import { readFileSync } from 'node:fs';
/**
 * Whether this setpriv accepts --seccomp-filter (util-linux 2.41 and later). The option
 * string is read from the binary, so the check never runs it.
 */
export function setprivSupportsSeccompFilter(path = '/usr/bin/setpriv', read = readFileSync) {
    try {
        return read(path).includes('seccomp-filter');
    }
    catch {
        return false;
    }
}
