/** A small hand-rolled single-series line+area chart, matching this frontend's existing
 *  convention of plain SVG rather than a charting library (see BarChart/PieChart in
 *  BillingUsagePage.tsx and SpendAnalyticsPage.tsx) - this file is simply the line-chart
 *  counterpart those pages didn't need yet. Null points (a day with no completed jobs) break
 *  the line rather than drawing a false zero. */
import { useMemo } from "react";

export interface LineChartPoint {
  label: string;
  value: number | null;
}

export function LineChart({
  points, color = "#4f46e5", height = 220, valueSuffix = "", valueFormatter,
}: {
  points: LineChartPoint[];
  color?: string;
  height?: number;
  valueSuffix?: string;
  valueFormatter?: (v: number) => string;
}) {
  const width = 640;
  const padLeft = 44;
  const padRight = 12;
  const padTop = 16;
  const padBottom = 28;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const { segments, dots, yTicks, xLabels } = useMemo(() => {
    const values = points.map((p) => p.value).filter((v): v is number => v !== null);
    const max = values.length ? Math.max(...values) : 1;
    const min = values.length ? Math.min(0, Math.min(...values)) : 0;
    const span = max - min || 1;
    const n = points.length || 1;

    const xFor = (i: number) => padLeft + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yFor = (v: number) => padTop + plotH - ((v - min) / span) * plotH;

    const segs: string[] = [];
    let current: string[] = [];
    points.forEach((p, i) => {
      if (p.value === null) {
        if (current.length > 1) segs.push(current.join(" "));
        current = [];
        return;
      }
      current.push(`${xFor(i)},${yFor(p.value)}`);
    });
    if (current.length > 1) segs.push(current.join(" "));

    const dotList = points
      .map((p, i) => (p.value === null ? null : { x: xFor(i), y: yFor(p.value), label: p.label, value: p.value }))
      .filter((d): d is { x: number; y: number; label: string; value: number } => d !== null);

    const tickCount = 4;
    const ticks = Array.from({ length: tickCount + 1 }, (_, i) => {
      const v = min + (span * i) / tickCount;
      return { y: yFor(v), v };
    });

    const labelEvery = Math.max(1, Math.ceil(n / 6));
    const xLbls = points
      .map((p, i) => (i % labelEvery === 0 || i === n - 1 ? { x: xFor(i), label: p.label } : null))
      .filter((d): d is { x: number; label: string } => d !== null);

    return { segments: segs, dots: dotList, yTicks: ticks, xLabels: xLbls };
  }, [points, plotW, plotH, padLeft, padTop]);

  const fmt = (v: number) => (valueFormatter ? valueFormatter(v) : `${Math.round(v * 10) / 10}${valueSuffix}`);

  if (points.every((p) => p.value === null)) {
    return (
      <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
        No data in this range.
      </div>
    );
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Trend chart">
      {yTicks.map((t, i) => (
        <g key={i}>
          <line
            x1={padLeft} x2={width - padRight} y1={t.y} y2={t.y}
            className="stroke-slate-100 dark:stroke-slate-800"
            strokeWidth={1}
          />
          <text
            x={padLeft - 8} y={t.y} textAnchor="end" dominantBaseline="middle"
            className="fill-slate-400 text-[10px] dark:fill-slate-500"
          >
            {fmt(t.v)}
          </text>
        </g>
      ))}

      {segments.map((seg, i) => {
        const pts = seg.split(" ");
        const first = pts[0].split(",");
        const last = pts[pts.length - 1].split(",");
        const areaPath = `M${first[0]},${padTop + plotH} L${seg.replaceAll(" ", " L")} L${last[0]},${padTop + plotH} Z`;
        return (
          <g key={i}>
            <path d={areaPath} fill={color} opacity={0.08} />
            <polyline points={seg} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          </g>
        );
      })}

      {dots.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r={i === dots.length - 1 ? 3.5 : 2.5} fill={color}>
          <title>{`${d.label}: ${fmt(d.value)}`}</title>
        </circle>
      ))}

      {xLabels.map((l, i) => (
        <text
          key={i} x={l.x} y={height - 6} textAnchor="middle"
          className="fill-slate-400 text-[10px] dark:fill-slate-500"
        >
          {l.label}
        </text>
      ))}
    </svg>
  );
}
