/** One date/time format for the whole app, the same on every machine.
 *
 * Calling toLocaleString(undefined, ...) uses the BROWSER's own language, so the same job
 * read "05:26 PM" in Chrome on Windows (English US) and "17:26" in Chrome on a Mac set to
 * English UK/India - which has nothing to do with the OS clock setting. These pin both: day
 * before month ("13 Nov"), and a 12-hour clock with AM/PM ("05:26 PM"). */
const DAY_MONTH = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" });
const DAY_MONTH_YEAR = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" });
const TIME = new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", hour12: true });

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "05:26 PM" - "" when missing or unreadable. */
export function formatTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? TIME.format(d) : "";
}

/** "13 Nov" (or "13 Nov 2026" with year) - "" when missing or unreadable. */
export function formatDate(value: string | Date | null | undefined, opts: { year?: boolean } = {}): string {
  const d = toDate(value);
  return d ? (opts.year ? DAY_MONTH_YEAR : DAY_MONTH).format(d) : "";
}

/** "13 Nov, 05:26 PM" (or "13 Nov 2026, 05:26 PM" with year) - "" when missing or unreadable. */
export function formatDateTime(value: string | Date | null | undefined, opts: { year?: boolean } = {}): string {
  const d = toDate(value);
  return d ? `${formatDate(d, opts)}, ${formatTime(d)}` : "";
}
