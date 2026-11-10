/**
 * Reading and writing a DATE FIELD's value without changing how it is written.
 *
 * The rule this whole file exists to keep: a date is stored exactly as its document wrote it.
 * Nothing in this product normalises one. Extraction keeps the document's own wording (see the
 * backend's extraction._DATE_FORMATS, which exists precisely because they vary), a tenant can
 * demand a particular one through tenant_format_prompt, the ERP robot re-renders the value
 * itself when it types it in (browser._set_date), and the Excel export copies the string
 * straight through. So the calendar button must change the DATE and nothing else: pick a new
 * day on "15/03/2026" and you get "20/03/2026", pick one on "15-Mar-2026" and you get
 * "20-Mar-2026".
 *
 * Writing everything back as YYYY-MM-DD would have been far less code, and would have silently
 * rewritten every tenant's chosen format on the first click.
 */

/** A format we can both read and write, as a display token and the pieces to do it with. */
interface DateShape {
  token: string;
  re: RegExp;
  /** Which capture group holds what. */
  order: "dmy" | "ymd" | "mdy";
  /** Month written as a name rather than a number. */
  monthName?: "short" | "long";
  sep: string;
  twoDigitYear?: boolean;
}

const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December"];

/**
 * Tried in order, first whole-string match wins - the same order, and the same day-first
 * preference, as the backend's own _DATE_FORMATS. This app's paperwork is Indian/international
 * customs documents, where day-first is the house convention, so 08/09/2026 is 8 September,
 * never 9 August. "MM/DD/YYYY" sits last and only ever catches what day-first cannot read at
 * all (04/17/2026 - there is no 17th month).
 */
const SHAPES: DateShape[] = [
  { token: "DD-MMM-YYYY", re: /^(\d{1,2})-([A-Za-z]{3,})-(\d{4})$/, order: "dmy", monthName: "short", sep: "-" },
  { token: "DD-MMM-YY", re: /^(\d{1,2})-([A-Za-z]{3,})-(\d{2})$/, order: "dmy", monthName: "short", sep: "-", twoDigitYear: true },
  { token: "YYYY-MM-DD", re: /^(\d{4})-(\d{1,2})-(\d{1,2})$/, order: "ymd", sep: "-" },
  { token: "DD/MM/YYYY", re: /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/, order: "dmy", sep: "/" },
  { token: "DD/MM/YY", re: /^(\d{1,2})\/(\d{1,2})\/(\d{2})$/, order: "dmy", sep: "/", twoDigitYear: true },
  { token: "DD-MM-YYYY", re: /^(\d{1,2})-(\d{1,2})-(\d{4})$/, order: "dmy", sep: "-" },
  { token: "DD.MM.YYYY", re: /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/, order: "dmy", sep: "." },
  { token: "DD.MM.YY", re: /^(\d{1,2})\.(\d{1,2})\.(\d{2})$/, order: "dmy", sep: ".", twoDigitYear: true },
  { token: "MM/DD/YYYY", re: /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/, order: "mdy", sep: "/" },
];

/**
 * What an admin can choose as a field's fallback format, for a box that is EMPTY and so has no
 * existing value whose format a pick could copy.
 *
 * Deliberately NOT every shape above. Two shapes are read but never offered as something to
 * WRITE:
 *
 *   "DD-MM-YYYY" - the ERP robot reads it (browser._DATE_PATTERNS) but the cross-document
 *   comparer does not (extraction._DATE_FORMATS has no %d-%m-%Y), so choosing it would quietly
 *   turn every "does the invoice date match the packing list date?" check on that field into a
 *   plain string compare.
 *
 *   "MM/DD/YYYY" - month-first is UNREADABLE BACK. Everything in this product reads an
 *   ambiguous slash date day-first, so a month-first "01/02/2024" written here comes back as
 *   1 February everywhere else: on the comparer, on the ERP robot, on the next operator's
 *   screen. Picking 2 January would file a declaration dated 1 February. The exhaustive
 *   round-trip check in the scratchpad caught this on 528 of 8766 date/format pairs.
 *
 * Both are still READ by SHAPES above, because a document that already writes dates that way
 * must open in the calendar rather than looking blank - and pickedDateToFieldValue's own
 * round-trip guard stops either of them being written back ambiguously.
 */
export const DATE_FORMAT_CHOICES = [
  { value: "", label: "DD/MM/YYYY  (15/03/2026) - default" },
  { value: "DD-MMM-YYYY", label: "DD-MMM-YYYY  (15-Mar-2026)" },
  { value: "YYYY-MM-DD", label: "YYYY-MM-DD  (2026-03-15)" },
  { value: "DD.MM.YYYY", label: "DD.MM.YYYY  (15.03.2026)" },
] as const;

export const DEFAULT_DATE_FORMAT = "DD/MM/YYYY";

function monthFromName(name: string): number | null {
  const key = name.slice(0, 3).toLowerCase();
  const i = MONTHS_SHORT.findIndex((m) => m.toLowerCase() === key);
  return i < 0 ? null : i + 1;
}

/** Two-digit years the same way Python's %y does it, so a value reads identically either side. */
function expandYear(n: number): number {
  return n <= 68 ? 2000 + n : 1900 + n;
}

export interface ParsedFieldDate {
  y: number;
  m: number;
  d: number;
  /** Which of SHAPES it was written in. */
  token: string;
}

/** Read a stored value, whichever way it is written. Returns null for anything unparseable. */
export function parseFieldDate(text: string | null | undefined): ParsedFieldDate | null {
  const raw = (text ?? "").trim();
  if (!raw) return null;
  for (const shape of SHAPES) {
    const m = shape.re.exec(raw);
    if (!m) continue;
    let y: number;
    let mo: number | null;
    let d: number;
    if (shape.order === "ymd") {
      y = Number(m[1]); mo = Number(m[2]); d = Number(m[3]);
    } else if (shape.order === "mdy") {
      mo = Number(m[1]); d = Number(m[2]); y = Number(m[3]);
    } else {
      d = Number(m[1]);
      mo = shape.monthName ? monthFromName(m[2]) : Number(m[2]);
      y = Number(m[3]);
    }
    if (mo === null) continue;          // "15-Xyz-2026" - looked like a month name, is not one
    if (shape.twoDigitYear) y = expandYear(y);
    // Reject a shape that matched the digits but not the calendar - 31/02/2026, 00/05/2026.
    // Without this, new Date() would roll 31 February over to 3 March and the calendar would
    // open on a day the document never said.
    if (mo < 1 || mo > 12 || d < 1 || d > 31) continue;
    const probe = new Date(y, mo - 1, d);
    if (probe.getFullYear() !== y || probe.getMonth() !== mo - 1 || probe.getDate() !== d) continue;
    return { y, m: mo, d, token: shape.token };
  }
  return null;
}

/** "15/03/2026" -> "2026-03-15", what <input type="date"> needs. "" when it cannot be read. */
export function toInputDate(text: string | null | undefined): string {
  const p = parseFieldDate(text);
  if (!p) return "";
  return `${String(p.y).padStart(4, "0")}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
}

/** Which of the shapes above a stored value is written in, or null if none of them. */
export function detectDateFormat(text: string | null | undefined): string | null {
  return parseFieldDate(text)?.token ?? null;
}

/** Write y/m/d out in a named format. An unknown token falls back to the house default. */
export function formatFieldDate(y: number, m: number, d: number, token: string): string {
  const shape = SHAPES.find((s) => s.token === token)
    ?? SHAPES.find((s) => s.token === DEFAULT_DATE_FORMAT)!;
  const dd = String(d).padStart(2, "0");
  const mm = shape.monthName === "long"
    ? MONTHS_LONG[m - 1]
    : shape.monthName === "short"
      ? MONTHS_SHORT[m - 1]
      : String(m).padStart(2, "0");
  const yy = shape.twoDigitYear ? String(y % 100).padStart(2, "0") : String(y).padStart(4, "0");
  if (shape.order === "ymd") return [yy, mm, dd].join(shape.sep);
  if (shape.order === "mdy") return [mm, dd, yy].join(shape.sep);
  return [dd, mm, yy].join(shape.sep);
}

/**
 * The whole round trip: what the calendar hands back ("2026-03-20") becomes a value written the
 * way THIS field already writes dates.
 *
 * `current` wins over `configured` on purpose. A box that already holds a date is the most
 * reliable statement there is of how this tenant's documents write one - better than any
 * setting, because it came off the document itself. The configured format is consulted only
 * when the box is empty, and the house day-first default when there is no setting either.
 *
 * Whatever format that lands on, the result is then READ BACK before it is returned, and
 * thrown away for the day-first default if it does not come back as the day that was picked.
 * That guard is not theoretical: a field whose existing value is month-first ("04/17/2026" -
 * detected as MM/DD/YYYY because no day-first reading of it exists) would otherwise have the
 * next pick of 2 January written as "01/02/2024", which every other reader in this product -
 * the cross-document comparer, the ERP robot, the next operator to open the job - reads as
 * 1 February. A correct date written in a slightly different style beats a wrong date written
 * in the expected one.
 */
export function pickedDateToFieldValue(
  isoFromCalendar: string,
  current: string | null | undefined,
  configured: string | null | undefined,
): string {
  const iso = (isoFromCalendar ?? "").trim();
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return "";                                    // the calendar was cleared
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const token = detectDateFormat(current) ?? (configured || DEFAULT_DATE_FORMAT);
  const written = formatFieldDate(y, mo, d, token);
  if (toInputDate(written) === iso) return written;
  return formatFieldDate(y, mo, d, DEFAULT_DATE_FORMAT);
}
