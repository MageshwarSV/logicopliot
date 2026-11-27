/** Charts for the SaaS Finance business dashboard - plain SVG, the codebase's convention (no
 *  chart library). Colours are the dataviz skill's validated categorical palette (slots in fixed
 *  order, light + dark steps), defined once as CSS variables in <FinanceVizStyles />. Every chart
 *  has a hover tooltip; bars sit on the baseline with 4px rounded tops and a 2px gap between
 *  stacked segments; grid and axes are recessive; text uses text colours, never series colours. */
import { useMemo, useRef, useState, type ReactNode } from "react";

export const SERIES = ["var(--fv-s1)", "var(--fv-s2)", "var(--fv-s3)", "var(--fv-s4)"];

export function FinanceVizStyles() {
  return (
    <style>{`
      .fin-viz { --fv-s1:#257de0; --fv-s2:#f47230; --fv-s3:#00b894; --fv-s4:#f2a600; --fv-cmp:#9aa3af; --fv-grid:#e5e7eb; --fv-surface:#ffffff; }
      @media (prefers-color-scheme: dark) {
        .fin-viz { --fv-s1:#338ced; --fv-s2:#e76624; --fv-s3:#00a888; --fv-s4:#d28e05; --fv-cmp:#6b7280; --fv-grid:#334155; --fv-surface:#0f172a; }
      }
    `}</style>
  );
}

export interface BarSeries { key: string; label: string; color: string; values: (number | null)[] }

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          {it.dashed
            ? <span className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: it.color }} />
            : <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: it.color }} />}
          {it.label}
        </span>
      ))}
    </div>
  );
}

/** Stacked (or single) bars per category with an optional comparison line in the SAME unit
 *  (e.g. last year's revenue) - one axis only. */
export function StackedBars({
  labels, series, compare, compareLabel = "Last year", height = 260, format, tooltipTitle, emptyText = "No data yet.",
}: {
  labels: string[]; series: BarSeries[]; compare?: (number | null)[]; compareLabel?: string; height?: number;
  format: (v: number) => string; tooltipTitle?: (i: number) => string; emptyText?: string;
}) {
  const wrap = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 760;
  const pad = { top: 12, right: 12, bottom: 28, left: 64 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const totals = labels.map((_, i) => series.reduce((s, ser) => s + (ser.values[i] ?? 0), 0));
  const hasAny = totals.some((t) => t > 0) || (compare ?? []).some((c) => (c ?? 0) > 0);
  const max = niceMax(Math.max(0, ...totals, ...(compare ?? []).map((c) => c ?? 0)));
  const step = plotW / Math.max(1, labels.length);
  const barW = Math.max(4, Math.min(42, step * 0.62));
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const labelEvery = labels.length > 20 ? Math.ceil(labels.length / 12) : 1;

  if (!hasAny) return <div className="flex items-center justify-center text-sm text-slate-400" style={{ height }}>{emptyText}</div>;

  const cmpPath = compare
    ? compare.map((c, i) => (c == null ? null : `${pad.left + step * i + step / 2},${y(c)}`)).filter(Boolean).join(" L ")
    : "";

  return (
    <div ref={wrap} className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Bar chart"
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="var(--fv-grid)" strokeWidth={1} />
            <text x={pad.left - 8} y={y(t) + 4} textAnchor="end" fontSize={11} className="fill-slate-400">{format(t)}</text>
          </g>
        ))}
        {labels.map((lab, i) => {
          const cx = pad.left + step * i + step / 2;
          let acc = 0;
          const segs = series.map((ser) => {
            const v = ser.values[i] ?? 0;
            const y0 = y(acc), y1 = y(acc + v);
            acc += v;
            return { ser, v, y0, y1 };
          }).filter((s) => s.v > 0);
          return (
            <g key={lab + i}>
              {segs.map((s, n) => {
                const top = n === segs.length - 1;
                const h = Math.max(0, s.y0 - s.y1 - (n > 0 ? 2 : 0));
                const r = top ? Math.min(4, h) : 0;
                const x = cx - barW / 2, yy = s.y1;
                return top && r > 0 ? (
                  <path key={s.ser.key} fill={s.ser.color} opacity={hover === null || hover === i ? 1 : 0.45}
                    d={`M${x},${yy + h} L${x},${yy + r} Q${x},${yy} ${x + r},${yy} L${x + barW - r},${yy} Q${x + barW},${yy} ${x + barW},${yy + r} L${x + barW},${yy + h} Z`} />
                ) : (
                  <rect key={s.ser.key} x={x} y={yy} width={barW} height={h} fill={s.ser.color}
                    opacity={hover === null || hover === i ? 1 : 0.45} />
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} className="fill-slate-500">{lab}</text>
              )}
              <rect x={cx - step / 2} y={pad.top} width={step} height={plotH} fill="transparent"
                onMouseEnter={() => setHover(i)} />
            </g>
          );
        })}
        {cmpPath && <path d={`M ${cmpPath}`} fill="none" stroke="var(--fv-cmp)" strokeWidth={2} strokeDasharray="5 4" pointerEvents="none" />}
        {compare && compare.map((c, i) => c == null ? null : (
          <circle key={`c${i}`} cx={pad.left + step * i + step / 2} cy={y(c)} r={hover === i ? 4.5 : 3}
            fill="var(--fv-cmp)" stroke="var(--fv-surface)" strokeWidth={2} pointerEvents="none" />
        ))}
        {hover !== null && (
          <line x1={pad.left + step * hover + step / 2} x2={pad.left + step * hover + step / 2} y1={pad.top} y2={pad.top + plotH}
            stroke="var(--fv-cmp)" strokeWidth={1} opacity={0.4} pointerEvents="none" />
        )}
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-2 z-10 min-w-44 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-800"
          style={{ left: `${Math.min(78, Math.max(2, ((pad.left + step * hover + step / 2) / width) * 100 + 2))}%` }}>
          <p className="mb-1 font-semibold text-slate-900 dark:text-slate-50">{tooltipTitle ? tooltipTitle(hover) : labels[hover]}</p>
          {series.map((ser) => (
            <p key={ser.key} className="flex items-center justify-between gap-3 text-slate-600 dark:text-slate-300">
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: ser.color }} />{ser.label}</span>
              <span className="tabular-nums">{ser.values[hover] == null ? "—" : format(ser.values[hover] as number)}</span>
            </p>
          ))}
          {series.length > 1 && (
            <p className="mt-1 flex justify-between border-t border-slate-100 pt-1 font-semibold text-slate-900 dark:border-slate-700 dark:text-slate-50">
              <span>Total</span><span className="tabular-nums">{format(totals[hover])}</span>
            </p>
          )}
          {compare && compare[hover] != null && (
            <p className="flex justify-between gap-3 text-slate-500"><span>{compareLabel}</span><span className="tabular-nums">{format(compare[hover] as number)}</span></p>
          )}
        </div>
      )}
    </div>
  );
}

/** Donut with the legend as a table: name, value, share. */
export function Donut({ items, format, centerLabel }: {
  items: { label: string; value: number; color: string }[]; format: (v: number) => string; centerLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const total = items.reduce((s, i) => s + i.value, 0);
  const size = 180, stroke = 26, r = (size - stroke) / 2, c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-44 w-44 shrink-0" role="img" aria-label={centerLabel}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--fv-grid)" strokeWidth={stroke} />
        {total > 0 && items.map((it, i) => {
          const len = (it.value / total) * c;
          const gap = items.filter((x) => x.value > 0).length > 1 ? 2 : 0;
          const seg = (
            <circle key={it.label} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={it.color}
              strokeWidth={hover === i ? stroke + 4 : stroke} strokeDasharray={`${Math.max(0, len - gap)} ${c}`}
              strokeDashoffset={-offset} transform={`rotate(-90 ${size / 2} ${size / 2})`}
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} style={{ cursor: "default" }}>
              <title>{`${it.label}: ${format(it.value)}`}</title>
            </circle>
          );
          offset += len;
          return seg;
        })}
        <text x="50%" y="46%" textAnchor="middle" fontSize={11} className="fill-slate-500">{hover === null ? centerLabel : items[hover].label}</text>
        <text x="50%" y="58%" textAnchor="middle" fontSize={15} fontWeight={700} className="fill-slate-900 dark:fill-slate-50">
          {format(hover === null ? total : items[hover].value)}
        </text>
      </svg>
      <table className="w-full text-sm">
        <tbody>
          {items.map((it, i) => (
            <tr key={it.label} className={`border-b border-slate-100 dark:border-slate-800 ${hover === i ? "bg-slate-50 dark:bg-slate-800/50" : ""}`}
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <td className="py-1.5 pr-2"><span className="inline-flex items-center gap-2 text-slate-700 dark:text-slate-200">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: it.color }} />{it.label}</span></td>
              <td className="py-1.5 text-right font-medium tabular-nums text-slate-900 dark:text-slate-50">{format(it.value)}</td>
              <td className="w-14 py-1.5 text-right tabular-nums text-slate-500">{total ? `${((100 * it.value) / total).toFixed(0)}%` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One series as a line with markers (e.g. total customers by month). */
export function LineSeries({ labels, values, color = SERIES[0], format, height = 200 }: {
  labels: string[]; values: (number | null)[]; color?: string; format: (v: number) => string; height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 760, pad = { top: 14, right: 16, bottom: 28, left: 44 };
  const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(1, ...values.map((v) => v ?? 0)));
  const step = plotW / Math.max(1, labels.length - 1);
  const pts = useMemo(() => values.map((v, i) => (v == null ? null : { x: pad.left + step * i, y: pad.top + plotH - (v / max) * plotH, v, i })),
    [values, step, max, plotH]);
  const path = pts.filter(Boolean).map((p, n) => `${n === 0 ? "M" : "L"}${p!.x},${p!.y}`).join(" ");
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Line chart" onMouseLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={pad.left} x2={width - pad.right} y1={pad.top + plotH - f * plotH} y2={pad.top + plotH - f * plotH} stroke="var(--fv-grid)" />
            <text x={pad.left - 8} y={pad.top + plotH - f * plotH + 4} textAnchor="end" fontSize={11} className="fill-slate-400">{format(f * max)}</text>
          </g>
        ))}
        <path d={path} fill="none" stroke={color} strokeWidth={2} />
        {pts.map((p) => p && (
          <circle key={p.i} cx={p.x} cy={p.y} r={hover === p.i ? 6 : 4} fill={color} stroke="var(--fv-surface)" strokeWidth={2} />
        ))}
        {labels.map((l, i) => (
          <g key={l + i}>
            <text x={pad.left + step * i} y={height - 8} textAnchor="middle" fontSize={11} className="fill-slate-500">{l}</text>
            <rect x={pad.left + step * i - step / 2} y={pad.top} width={step} height={plotH} fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        ))}
      </svg>
      {hover !== null && values[hover] != null && (
        <div className="pointer-events-none absolute top-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs shadow dark:border-slate-700 dark:bg-slate-800"
          style={{ left: `${Math.min(80, ((pad.left + step * hover) / width) * 100)}%` }}>
          <b className="text-slate-900 dark:text-slate-50">{labels[hover]}</b>
          <span className="ml-2 tabular-nums text-slate-600 dark:text-slate-300">{format(values[hover] as number)}</span>
        </div>
      )}
    </div>
  );
}

export function ChartCard({ title, icon, extra, children, footer }: {
  title: ReactNode; icon?: ReactNode; extra?: ReactNode; children: ReactNode; footer?: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5 dark:border-slate-800">
        <h2 className="flex items-center gap-2 font-semibold text-slate-900 dark:text-slate-50">
          {icon && <span className="text-indigo-600 dark:text-indigo-300">{icon}</span>}{title}
        </h2>
        {extra && <div className="flex flex-wrap items-center gap-2">{extra}</div>}
      </div>
      <div className="p-5">{children}</div>
      {footer && <div className="border-t border-slate-100 px-5 py-2.5 text-xs text-slate-500 dark:border-slate-800">{footer}</div>}
    </section>
  );
}
