/**
 * Host machine labels derived from the backend's platform report.
 *
 * The workbench status payload carries a `platform` block
 * (backend/platform_support.py via GET /api/workbench/status). When it is
 * missing — remote pairing gate before login, offline tests, SSR snapshots —
 * the fallback is strictly platform-neutral; never guess "Mac".
 */

const NEUTRAL_MACHINE = { label: '运行 ModelDesk 的这台电脑', short: '这台电脑', system: '', isMacos: false, known: false };
export { NEUTRAL_MACHINE };
const MAC_MACHINE = { label: '这台 Mac', short: 'Mac', system: 'Darwin', isMacos: true, known: true };

/** Resolve host machine labels from a workbench status payload. */
export function machineInfo(status) {
  const platform = status?.platform;
  if (!platform || typeof platform !== 'object' || Array.isArray(platform)) return NEUTRAL_MACHINE;
  const system = typeof platform.system === 'string' ? platform.system : '';
  const isMacos = platform.is_macos === true;
  const hasLabel = typeof platform.machine_label === 'string' && platform.machine_label.trim();
  const hasShort = typeof platform.machine_short === 'string' && platform.machine_short.trim();
  if (!system && !isMacos && !hasLabel && !hasShort) return NEUTRAL_MACHINE;
  if (isMacos) {
    const label = typeof platform.machine_label === 'string' && platform.machine_label.trim() ? platform.machine_label.trim() : MAC_MACHINE.label;
    const short = typeof platform.machine_short === 'string' && platform.machine_short.trim() ? platform.machine_short.trim() : MAC_MACHINE.short;
    return { label, short, system, isMacos: true, known: true };
  }
  const neutralShort = typeof platform.machine_short === 'string' && platform.machine_short.trim() ? platform.machine_short.trim() : NEUTRAL_MACHINE.short;
  const neutralLabel = typeof platform.machine_label === 'string' && platform.machine_label.trim() ? platform.machine_label.trim() : NEUTRAL_MACHINE.label;
  return { label: neutralLabel, short: neutralShort, system, isMacos: false, known: true };
}

/** Long label: names the machine the user has to walk over to. */
export function machineLabel(status) { return machineInfo(status).label; }

/** Short label for inline sentences where the long form reads awkwardly. */
export function machineShort(status) { return machineInfo(status).short; }

/** True only when the backend explicitly reported a macOS host. */
export function hostIsMacos(status) { return machineInfo(status).isMacos === true; }

/** True when a real platform block arrived; false for the neutral fallback. */
export function platformKnown(status) { return machineInfo(status).known; }
