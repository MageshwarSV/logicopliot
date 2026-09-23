/** A small picture of how a shipment travelled, and which way.
 *
 *   Sea Import   a ship sailing LEFT      — arriving
 *   Sea Export   the same ship, mirrored  — leaving
 *   Air Import   a plane nose-down        — landing
 *   Air Export   a plane nose-up          — taking off
 *
 * Direction is carried by the drawing itself rather than by an arrow bolted beside it, so the
 * whole thing reads at 14px in a table row where a second glyph would not.
 *
 * The mode string is parsed rather than passed as an enum, because it is worked out on the
 * server and can legitimately be half an answer — "Import" with no mode at all. A ship would
 * be a lie there, so it draws nothing and lets the words stand alone.
 */
export function ModeIcon({ mode, className = "" }: { mode?: string | null; className?: string }) {
  if (!mode) return null;
  const m = mode.toLowerCase();
  const sea = m.includes("sea");
  const air = m.includes("air");
  const isImport = m.includes("import");
  const isExport = m.includes("export");
  if (!sea && !air) return null;

  const cls = `h-4 w-4 shrink-0 ${className}`;

  if (sea) {
    // Sails left by default (arriving); mirrored for an export, which is leaving.
    return (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={cls}
        aria-hidden="true"
        style={isExport ? { transform: "scaleX(-1)" } : undefined}
      >
        {/* hull */}
        <path d="M3 16h18l-2 4H5z" />
        {/* the water it sits in */}
        <path d="M2.5 20.5c1.4 0 1.4 1 2.8 1s1.4-1 2.8-1 1.4 1 2.8 1 1.4-1 2.8-1 1.4 1 2.8 1 1.4-1 2.8-1" />
        {/* cabin, stepped down towards the bow so the direction is legible */}
        <path d="M7 16V9h5l3 3v4" />
        <path d="M9.5 9V6h2v3" />
      </svg>
    );
  }

  // Air: the same plane, tipped nose-up to take off or nose-down to land.
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cls}
      aria-hidden="true"
      style={{ transform: `rotate(${isImport ? 20 : -20}deg)` }}
    >
      <path d="M3.5 12.5 21 5l-4.5 15-3.5-6z" />
      <path d="M13 14 21 5" />
      {/* the ground it leaves or meets, so up and down are not left to the eye alone */}
      <path d="M3 21h18" strokeDasharray="2 3" opacity={0.6} />
    </svg>
  );
}

/** The mode as a pill: the picture, then the words. */
export function ModeBadge({ mode }: { mode?: string | null }) {
  if (!mode) return <span className="text-xs text-slate-300 dark:text-slate-600">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
      <ModeIcon mode={mode} />
      {mode}
    </span>
  );
}
