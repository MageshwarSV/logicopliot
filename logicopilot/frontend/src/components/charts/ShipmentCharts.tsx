import type { ShipmentTypeUsage } from "../../api/tenants";

const PALETTE = ["#6366f1", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899", "#f43f5e", "#84cc16", "#a855f7"];

export function colorFor(index: number): string {
  return PALETTE[index % PALETTE.length];
}

/** Pages by shipment type — each bar answers "which document type is actually driving the
 *  AI-processing bill this month," the number the tiered rate is computed from. Shared by
 *  BillingUsagePage.tsx and the SaaS Finance portal's UsageBillingPage.tsx. */
export function ShipmentBarChart({ data }: { data: ShipmentTypeUsage[] }) {
  const width = 560;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 36, left: 10 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const max = Math.max(1, ...data.map((d) => d.pages));
  const gap = 16;
  const barW = data.length > 0 ? Math.max(8, plotW / data.length - gap) : 0;

  if (data.length === 0) {
    return (
      <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
        No pages processed this period.
      </div>
    );
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Pages by shipment type">
      {data.map((d, i) => {
        const barH = (d.pages / max) * plotH;
        const x = padding.left + i * (plotW / data.length) + gap / 2;
        const y = padding.top + (plotH - barH);
        return (
          <g key={d.shipment_type}>
            <rect x={x} y={y} width={barW} height={Math.max(barH, 1)} fill={colorFor(i)} rx={4}>
              <title>{`${d.shipment_type}: ${d.pages.toLocaleString()} pages`}</title>
            </rect>
            <text x={x + barW / 2} y={padding.top + plotH - barH - 6} textAnchor="middle" fontSize={11} fontWeight={600} fill="currentColor" className="text-slate-600 dark:text-slate-300">
              {d.pages.toLocaleString()}
            </text>
            <text x={x + barW / 2} y={height - 14} textAnchor="middle" fontSize={10} fill="currentColor" className="text-slate-400">
              {d.shipment_type.length > 14 ? `${d.shipment_type.slice(0, 13)}…` : d.shipment_type}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Job distribution by shipment type — of everything run this month, what share is which kind.
 *  Used as the "Job Distribution" fallback for a finalized month with no Active/Deleted
 *  breakdown (see DonutChart, the primary chart used there once that data is available). */
export function ShipmentPieChart({ data }: { data: ShipmentTypeUsage[] }) {
  const total = data.reduce((sum, d) => sum + d.jobs, 0);
  const size = 180;
  const r = 80;
  const cx = size / 2;
  const cy = size / 2;

  if (total === 0) {
    return (
      <div className="flex h-[180px] items-center justify-center text-sm text-slate-400">
        No jobs this period.
      </div>
    );
  }

  let angle = -90;
  const arcs = data
    .filter((d) => d.jobs > 0)
    .map((d, i) => {
      const fraction = d.jobs / total;
      const startAngle = angle;
      const endAngle = angle + fraction * 360;
      angle = endAngle;
      const toXY = (deg: number): [number, number] => {
        const rad = (deg * Math.PI) / 180;
        return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
      };
      const [x1, y1] = toXY(startAngle);
      const [x2, y2] = toXY(endAngle);
      const largeArc = fraction > 0.5 ? 1 : 0;
      const path =
        fraction >= 0.9999
          ? `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy} Z`
          : `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`;
      return { ...d, path, fraction, color: colorFor(i) };
    });

  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-40 w-40 shrink-0" role="img" aria-label="Job distribution">
        {arcs.map((a) => (
          <path key={a.shipment_type} d={a.path} fill={a.color}>
            <title>{`${a.shipment_type}: ${a.jobs.toLocaleString()} (${(a.fraction * 100).toFixed(1)}%)`}</title>
          </path>
        ))}
      </svg>
      <div className="flex flex-col gap-2">
        {data.map((d, i) => (
          <div key={d.shipment_type} className="flex items-center gap-2 text-sm">
            <span className="h-3 w-3 shrink-0 rounded-sm" style={{ backgroundColor: colorFor(i) }} />
            <span className="text-slate-700 dark:text-slate-300">{d.shipment_type}</span>
            <span className="font-medium text-slate-900 dark:text-slate-100">{d.jobs.toLocaleString()}</span>
            <span className="text-xs text-slate-400">({total > 0 ? Math.round((d.jobs / total) * 100) : 0}%)</span>
          </div>
        ))}
      </div>
    </div>
  );
}
