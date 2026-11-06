/** Active vs Deleted job distribution - a donut (not the wedge-path solid pies already used
 *  elsewhere, e.g. BillingUsagePage's own ShipmentPieChart) via stroke-dasharray/dashoffset on
 *  stacked ring segments, with a centered total label. No charting library - same hand-rolled
 *  SVG convention already used by every other chart in this codebase. */
const SIZE = 180;
const STROKE = 28;
const R = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * R;

interface Segment {
  label: string;
  value: number;
  textClassName: string; // stroke/text color (currentColor), so dark mode just works
  dotClassName: string; // literal bg-* for the legend swatch - Tailwind needs the full class name
}

export function DonutChart({ active, deleted }: { active: number; deleted: number }) {
  const total = active + deleted;
  const segments: Segment[] = [
    { label: "Active Jobs", value: active, textClassName: "text-indigo-500", dotClassName: "bg-indigo-500" },
    { label: "Deleted Jobs", value: deleted, textClassName: "text-rose-400", dotClassName: "bg-rose-400" },
  ];

  if (total === 0) {
    return (
      <div className="flex h-[180px] items-center justify-center text-sm text-slate-400">
        No jobs this period.
      </div>
    );
  }

  let offsetSoFar = 0;

  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="h-40 w-40 shrink-0" role="img" aria-label="Job distribution: active vs deleted">
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          fill="none"
          strokeWidth={STROKE}
          className="stroke-slate-100 dark:stroke-slate-800"
        />
        {segments
          .filter((s) => s.value > 0)
          .map((s) => {
            const fraction = s.value / total;
            const dash = fraction * CIRCUMFERENCE;
            const circle = (
              <circle
                key={s.label}
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={R}
                fill="none"
                strokeWidth={STROKE}
                strokeDasharray={`${dash} ${CIRCUMFERENCE - dash}`}
                strokeDashoffset={-offsetSoFar}
                strokeLinecap="butt"
                transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
                className={s.textClassName}
                stroke="currentColor"
              >
                <title>{`${s.label}: ${s.value.toLocaleString()} (${Math.round(fraction * 100)}%)`}</title>
              </circle>
            );
            offsetSoFar += dash;
            return circle;
          })}
        <text
          x={SIZE / 2}
          y={SIZE / 2 - 4}
          textAnchor="middle"
          fontSize={22}
          fontWeight={700}
          fill="currentColor"
          className="text-slate-900 dark:text-slate-50"
        >
          {total.toLocaleString()}
        </text>
        <text
          x={SIZE / 2}
          y={SIZE / 2 + 16}
          textAnchor="middle"
          fontSize={11}
          fill="currentColor"
          className="text-slate-400"
        >
          Jobs
        </text>
      </svg>
      <div className="flex flex-col gap-2">
        {segments.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-sm">
            <span className={`h-3 w-3 shrink-0 rounded-sm ${s.dotClassName}`} />
            <span className="text-slate-700 dark:text-slate-300">{s.label}</span>
            <span className="font-medium text-slate-900 dark:text-slate-100">{s.value.toLocaleString()}</span>
            <span className="text-xs text-slate-400">({Math.round((s.value / total) * 100)}%)</span>
          </div>
        ))}
      </div>
    </div>
  );
}
