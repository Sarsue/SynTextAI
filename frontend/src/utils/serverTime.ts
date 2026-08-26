/**
 * Reading the timestamps the API sends.
 *
 * The server stores naive UTC (`Column(DateTime, default=datetime.utcnow)`) and
 * serialises it with `.isoformat()`, which produces "2026-08-26T16:10:01.791016"
 * with no timezone designator at all.
 *
 * JavaScript parses a date-time string WITHOUT a designator as LOCAL time. So a
 * message written at 16:10 UTC was read as 16:10 in the viewer's own zone, and
 * every timestamp in the app was wrong by the viewer's offset from UTC. In
 * Toronto that is four hours, which is how a question came to be stamped 4:10 PM
 * and the answer it produced 12:10 PM: the reply appeared to arrive before the
 * question that caused it.
 *
 * It hid for as long as it did because the raw ISO string was printed straight
 * to the screen. Nobody reads milliseconds, so nobody noticed the hours.
 */
export function parseServerTime(value?: string | null): Date | null {
    if (!value) return null;
    // A designator is a trailing Z, or +hh:mm / -hh:mm after the time part.
    const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
    const d = new Date(hasZone ? value : `${value}Z`);
    return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * A time a person can read, in their own zone.
 *
 * Today shows the clock time, this year shows the date, older carries the year.
 */
export function formatServerTime(value?: string | null): string {
    const d = parseServerTime(value);
    if (!d) return value || '';

    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    }
    return d.toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'short',
        ...(d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
    });
}
