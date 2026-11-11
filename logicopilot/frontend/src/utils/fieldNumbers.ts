/**
 * The one definition of what a NUMBER field accepts, shared by the Template Creation Wizard
 * (which writes the pattern) and the Job Run screen (which has to recognise it again).
 *
 * Shared on purpose rather than written out in both places: the job screen only knows a field
 * is numeric by matching the pattern the wizard saved. Two copies of that string are free to
 * drift, and the symptom of drift is silent - the field keeps validating on save, but stops
 * filtering what can be typed into it, which is the half of this that the operator actually
 * sees.
 */

/** Digits, at most one decimal point, nothing else.
 *
 * Distinct from the "digits" mode's [0-9]+, which is whole numbers only and therefore wrong
 * for every weight and amount in this product (519.000 KG, 11042.40) - that gap is why these
 * fields were left unrestricted, and why "2232.000dsdsdsd", "24sdwdwd" and
 * "3447.004wdwdwdwdwdwd" are sitting in Gross Wt, total_quantity and Product Value today.
 *
 * No leading "-": a negative gross weight or invoice value is not a thing. Negatives only
 * "where explicitly required" is a per-field Custom pattern, not the default for every number.
 *
 * No thousands separators, because they are ambiguous in exactly this data: Gross Wt holds
 * both "4,882.000" (comma groups thousands) and "2.891,870" (comma IS the decimal point).
 * Accepting both would mean guessing which one a given customs weight meant.
 */
export const NUMBER_PATTERN = String.raw`\d+(?:\.\d+)?`;

export function isNumberPattern(pattern: string | null | undefined): boolean {
  return !!pattern && pattern === NUMBER_PATTERN;
}

/**
 * What a numeric box is allowed to contain WHILE IT IS BEING TYPED.
 *
 * Looser than NUMBER_PATTERN on purpose: "12." is not a valid number, but it is a perfectly
 * normal thing to have on screen one keystroke before "12.5". Rejecting it here would make
 * the decimal point impossible to type. The strict pattern is what gates the save; this only
 * decides which characters may appear at all.
 *
 * React fires onChange for a PASTE as well as a keystroke, so running every change through
 * this is what stops "5780.00 KG" or a copied cell of junk landing in the box - no separate
 * onPaste handler needed, and nothing can slip in by drag-and-drop or autofill either.
 *
 * A COMMA is deliberately kept rather than stripped, which is the one place this is not
 * simply "remove what is not a digit". Gross Wt holds both "4,882.000" and "2.891,870" today:
 * in the first the comma groups thousands, in the second it IS the decimal point. Silently
 * dropping it turns "2.891,870" into "2.891870" - a clean, plausible, valid-looking number
 * that is wrong by a factor of a thousand, on a customs weight, with nothing on screen to say
 * so. Left in place the value simply fails the strict pattern, the operator is told, and a
 * human decides which number was meant. A visible complaint beats a silent 1000x error.
 */
export function sanitizeNumericInput(next: string): string {
  const cleaned = next.replace(/[^\d.,]/g, "");
  const firstDot = cleaned.indexOf(".");
  if (firstDot === -1) return cleaned;
  // Keep the first decimal point, drop every later one - so "1.2.3" becomes "1.23" rather
  // than being refused outright, which is what someone fixing a typo mid-number expects.
  return cleaned.slice(0, firstDot + 1) + cleaned.slice(firstDot + 1).replace(/\./g, "");
}
