/**
 * Canonical IST (Asia/Kolkata) Time Formatting Utilities for Frontend.
 * Enforces Indian Standard Time (UTC+05:30) display across all browsers and machines.
 */

const IST_TZ = 'Asia/Kolkata';

/**
 * Safely parse any date/time representation into a valid Date object.
 */
export function parseDate(ts) {
  if (!ts) return null;
  if (ts instanceof Date) return isNaN(ts.getTime()) ? null : ts;

  if (typeof ts === 'string') {
    // If backend already passed "YYYY-MM-DD HH:mm:ss IST", convert to ISO with +05:30
    let clean = ts.trim();
    if (clean.endsWith('IST')) {
      clean = clean.replace('IST', '').trim().replace(' ', 'T') + '+05:30';
    } else if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(clean)) {
      clean = clean.replace(' ', 'T') + '+05:30';
    }
    const d = new Date(clean);
    return isNaN(d.getTime()) ? null : d;
  }

  if (typeof ts === 'number') {
    const d = new Date(ts > 1e11 ? ts : ts * 1000);
    return isNaN(d.getTime()) ? null : d;
  }

  return null;
}

/**
 * Format timestamp as "YYYY-MM-DD HH:mm:ss IST" in strict Indian Standard Time.
 */
export function formatIST(ts) {
  const d = parseDate(ts);
  if (!d) return '—';
  try {
    const formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: IST_TZ,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
    const parts = formatter.formatToParts(d).reduce((acc, p) => ({ ...acc, [p.type]: p.value }), {});
    const hour = parts.hour === '24' ? '00' : parts.hour;
    return `${parts.year}-${parts.month}-${parts.day} ${hour}:${parts.minute}:${parts.second} IST`;
  } catch {
    return String(ts);
  }
}

/**
 * Short date format in IST: "DD MMM HH:mm:ss IST"
 */
export function formatShortIST(ts) {
  const d = parseDate(ts);
  if (!d) return '—';
  try {
    const formatter = new Intl.DateTimeFormat('en-IN', {
      timeZone: IST_TZ,
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
    return `${formatter.format(d)} IST`;
  } catch {
    return String(ts);
  }
}

/**
 * Format timestamp as time only in IST: "HH:mm:ss IST"
 */
export function formatTimeOnlyIST(ts) {
  const d = parseDate(ts);
  if (!d) return '—';
  try {
    const formatter = new Intl.DateTimeFormat('en-IN', {
      timeZone: IST_TZ,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
    return `${formatter.format(d)} IST`;
  } catch {
    return String(ts);
  }
}

/**
 * Format countdown seconds into "mm:ss"
 */
export function formatCountdown(seconds) {
  if (seconds == null || isNaN(seconds) || seconds < 0) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
