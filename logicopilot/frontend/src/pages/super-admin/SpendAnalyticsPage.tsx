import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as systemSettingsApi from "../../api/systemSettings";
import type { OpenAICosts, SpendAnalytics, SystemSettings, UsdInrRate } from "../../api/systemSettings";
import { formatTime } from "../../utils/dateTime";

function formatInr(usd: number, rate: UsdInrRate | null): string | null {
  if (rate === null) return null;
  return `₹${(usd * rate.rate).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

type Metric = "jobs" | "documents" | "pages" | "characters" | "estimated_tokens" | "zero_result_jobs";

const METRICS: { value: Metric; label: string; color: string }[] = [
  { value: "jobs", label: "Jobs", color: "#6366f1" },
  { value: "documents", label: "Documents", color: "#0ea5e9" },
  { value: "pages", label: "Pages", color: "#14b8a6" },
  { value: "characters", label: "Characters", color: "#f59e0b" },
  { value: "estimated_tokens", label: "Est. Tokens", color: "#ec4899" },
  { value: "zero_result_jobs", label: "Zero-Result Jobs", color: "#f43f5e" },
];

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function preset(days: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - (days - 1));
  return { start: isoDate(start), end: isoDate(end) };
}

/** A day's numbers are read straight off its bar's height — no separate legend to cross-
 *  reference — so the chart needs no library: a handful of <rect>s scaled to the same max. */
function BarChart({ days, metric, color }: { days: SpendAnalytics["days"]; metric: Metric; color: string }) {
  const width = 900;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 28, left: 10 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const max = Math.max(1, ...days.map((d) => d[metric]));
  const barGap = 3;
  const barW = days.length > 0 ? Math.max(2, plotW / days.length - barGap) : 0;

  if (days.length === 0) {
    return (
      <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
        No data in this range.
      </div>
    );
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label={`${metric} per day`}>
      {days.map((d, i) => {
        const value = d[metric];
        const barH = (value / max) * plotH;
        const x = padding.left + i * (plotW / days.length);
        const y = padding.top + (plotH - barH);
        const showLabel = days.length <= 14 || i % Math.ceil(days.length / 14) === 0;
        return (
          <g key={d.date}>
            <rect x={x} y={y} width={barW} height={Math.max(barH, 1)} fill={color} rx={2}>
              <title>{`${d.date}: ${value.toLocaleString()}`}</title>
            </rect>
            {showLabel && (
              <text
                x={x + barW / 2}
                y={height - 8}
                textAnchor="middle"
                fontSize={10}
                fill="currentColor"
                className="text-slate-400"
              >
                {d.date.slice(5)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

interface PieSlice {
  label: string;
  value: number;
  color: string;
}

/** A bar answers "how did this trend over time"; a pie answers "of the whole, what share is
 *  which kind" — the two questions this page's numbers actually raise, so one of each. */
function PieChart({ slices }: { slices: PieSlice[] }) {
  const total = slices.reduce((sum, s) => sum + s.value, 0);
  const size = 200;
  const r = 90;
  const cx = size / 2;
  const cy = size / 2;

  if (total === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
        No jobs in this range.
      </div>
    );
  }

  let angle = -90; // start at 12 o'clock
  const arcs = slices
    .filter((s) => s.value > 0)
    .map((s) => {
      const fraction = s.value / total;
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
      return { ...s, path, fraction };
    });

  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row sm:items-center">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-48 w-48 shrink-0" role="img" aria-label="Job outcomes">
        {arcs.map((a) => (
          <path key={a.label} d={a.path} fill={a.color}>
            <title>{`${a.label}: ${a.value.toLocaleString()} (${(a.fraction * 100).toFixed(1)}%)`}</title>
          </path>
        ))}
      </svg>
      <div className="flex flex-col gap-2">
        {slices.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-sm">
            <span className="h-3 w-3 shrink-0 rounded-sm" style={{ backgroundColor: s.color }} />
            <span className="text-slate-700 dark:text-slate-300">{s.label}</span>
            <span className="font-medium text-slate-900 dark:text-slate-100">
              {s.value.toLocaleString()}
            </span>
            <span className="text-xs text-slate-400">
              ({total > 0 ? Math.round((s.value / total) * 100) : 0}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const n = (v: number) => v.toLocaleString("en-IN");
const rupees = (v: number, digits = 2) =>
  `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const niceDate = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

const PRESETS = [
  { label: "Today", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

function SectionTitle({ title, hint, badge }: { title: string; hint?: string; badge?: { text: string; tone: "green" | "grey" } }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
      {badge && (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.tone === "green"
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
          : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
          {badge.text}
        </span>
      )}
      {hint && <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
    </div>
  );
}

/** A headline money card: what it is, the number, and one line that explains it. */
function KpiCard({ label, value, sub, accent, children }: {
  label: string; value: string; sub?: string | null; accent?: boolean; children?: ReactNode;
}) {
  return (
    <Card className={`flex flex-col p-5 ${accent ? "border-indigo-200 bg-gradient-to-br from-indigo-50 to-white dark:border-indigo-500/30 dark:from-indigo-500/10 dark:to-slate-900" : ""}`}>
      <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums text-slate-900 dark:text-slate-50">{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
      {children && <div className="mt-auto pt-3">{children}</div>}
    </Card>
  );
}

/** Several small counts side by side in one card, split by thin lines. */
function StatStrip({ items }: { items: { label: string; value: string; sub?: string; muted?: boolean }[] }) {
  return (
    <Card className="grid grid-cols-2 divide-slate-100 dark:divide-slate-800 sm:grid-cols-4 sm:divide-x">
      {items.map((it) => (
        <div key={it.label} className="border-b border-slate-100 p-4 last:border-b-0 dark:border-slate-800 sm:border-b-0">
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{it.label}</p>
          <p className={`mt-1 text-xl font-semibold tabular-nums ${it.muted ? "text-slate-500 dark:text-slate-400" : "text-slate-900 dark:text-slate-50"}`}>{it.value}</p>
          {it.sub && <p className="mt-0.5 text-[11px] text-slate-400">{it.sub}</p>}
        </div>
      ))}
    </Card>
  );
}

/** Super Admin's "how much did this actually cost us" screen. Two very different numbers
 *  live side by side on purpose: REAL spend from OpenAI's own Admin API (once connected in
 *  Settings) and this app's own local counts, including an ESTIMATED token figure that is
 *  clearly labeled as such — the two should never be presented as if they were the same
 *  thing, since they can differ by an order of magnitude. */
/** Live spend straight from OpenAI (the Admin API key in Platform Settings) next to this app's
 *  own counts, for any date range - shown as the "Live from OpenAI" tab of API Cost & Usage.
 *  (It was its own Spend Analytics page until 2026-11-23.) */
export function SpendAnalyticsPanel() {
  const [range, setRange] = useState(() => preset(30));
  const [presetDays, setPresetDays] = useState<number | null>(30);
  const [reload, setReload] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [data, setData] = useState<SpendAnalytics | null>(null);
  const [realCosts, setRealCosts] = useState<OpenAICosts | null>(null);
  const [metric, setMetric] = useState<Metric>("estimated_tokens");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // A real connect/API failure (502 from OpenAI, network error) is NOT the same thing as
  // "no Admin key saved" — collapsing both into the same "not connected" banner is exactly
  // what made a genuinely connected key that OpenAI is refusing look identical to never
  // having connected one at all.
  const [realCostsError, setRealCostsError] = useState<string | null>(null);

  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [rate, setRate] = useState<UsdInrRate | null>(null);
  const [balanceModalOpen, setBalanceModalOpen] = useState(false);
  const [balanceInput, setBalanceInput] = useState("");
  const [expiryInput, setExpiryInput] = useState("");
  const [balanceError, setBalanceError] = useState<string | null>(null);
  const [savingBalance, setSavingBalance] = useState(false);

  function refreshBalanceAndRate() {
    systemSettingsApi.getSystemSettings().then(setSettings).catch(() => {});
    systemSettingsApi.getUsdInrRate().then(setRate).catch(() => setRate(null));
  }

  useEffect(() => {
    refreshBalanceAndRate();
  }, []);

  function openBalanceModal() {
    setBalanceInput(settings?.openai_balance_usd != null ? String(settings.openai_balance_usd) : "");
    setExpiryInput(settings?.openai_balance_expiry ?? "");
    setBalanceError(null);
    setBalanceModalOpen(true);
  }

  async function saveBalance(e: FormEvent) {
    e.preventDefault();
    setSavingBalance(true);
    setBalanceError(null);
    try {
      const usd = balanceInput.trim() === "" ? null : Number(balanceInput);
      await systemSettingsApi.setOpenAIBalance(usd, expiryInput.trim() === "" ? null : expiryInput);
      setBalanceModalOpen(false);
      refreshBalanceAndRate();
    } catch (err) {
      setBalanceError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSavingBalance(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    setError(null);
    setRealCostsError(null);
    Promise.all([
      systemSettingsApi.getSpendAnalytics(range.start, range.end),
      systemSettingsApi
        .getOpenAICosts(range.start, range.end)
        .then((real) => ({ real, err: null }))
        .catch((err) => ({
          real: null,
          err: axios.isAxiosError(err)
            ? err.response?.data?.detail ?? "Could not reach OpenAI."
            : "Could not reach OpenAI.",
        })),
    ])
      .then(([local, realResult]) => {
        setData(local);
        setRealCosts(realResult.real);
        setRealCostsError(realResult.err);
        setUpdatedAt(new Date());
      })
      .catch(() => setError("Could not load spend analytics."))
      .finally(() => setLoading(false));
  }, [range, reload]);

  const activeMetric = METRICS.find((m) => m.value === metric)!;

  const usdToInr = rate ? rate.rate : null;
  const jobs = data?.totals.jobs ?? 0;
  const billed = data?.totals.billed_pages ?? 0;
  const spend = realCosts?.connected ? realCosts.total_usd : null;
  const perPageUsd = spend != null && billed > 0 ? spend / billed : null;
  const perJobUsd = spend != null && jobs > 0 ? spend / jobs : null;
  const rangeText = range.start === range.end ? niceDate(range.start) : `${niceDate(range.start)} – ${niceDate(range.end)}`;

  function pickPreset(days: number) {
    setPresetDays(days);
    setRange(preset(days));
  }

  return (
    <>
      {/* Toolbar: period on the left, what is shown and when it was fetched on the right. */}
      <Card className="mb-6 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap gap-1 rounded-xl bg-slate-100 p-1 dark:bg-slate-800" role="tablist" aria-label="Period">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                role="tab"
                aria-selected={presetDays === p.days}
                onClick={() => pickPreset(p.days)}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${presetDays === p.days
                  ? "bg-white text-indigo-700 shadow-sm dark:bg-slate-900 dark:text-indigo-300"
                  : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"}`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <input
              type="date"
              aria-label="From"
              value={range.start}
              max={range.end}
              onChange={(e) => { setPresetDays(null); setRange((r) => ({ ...r, start: e.target.value })); }}
              className={`rounded-lg border bg-white px-2.5 py-1.5 text-sm dark:bg-slate-900 dark:text-slate-100 ${presetDays === null ? "border-indigo-300 dark:border-indigo-500/50" : "border-slate-200 dark:border-slate-700"}`}
            />
            <span>to</span>
            <input
              type="date"
              aria-label="To"
              value={range.end}
              min={range.start}
              onChange={(e) => { setPresetDays(null); setRange((r) => ({ ...r, end: e.target.value })); }}
              className={`rounded-lg border bg-white px-2.5 py-1.5 text-sm dark:bg-slate-900 dark:text-slate-100 ${presetDays === null ? "border-indigo-300 dark:border-indigo-500/50" : "border-slate-200 dark:border-slate-700"}`}
            />
          </div>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-slate-500 dark:text-slate-400">
              {loading ? "Getting from OpenAI…" : updatedAt ? `Updated ${formatTime(updatedAt)}` : ""}
            </span>
            <button
              type="button"
              disabled={loading}
              onClick={() => setReload((x) => x + 1)}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Refresh
            </button>
          </div>
        </div>
      </Card>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      {!data && loading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="h-36 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800" />)}
        </div>
      )}

      {data && (
        <div className={`space-y-8 transition-opacity ${loading ? "opacity-60" : ""}`}>
          {/* 1. Money */}
          <section>
            <SectionTitle title="Spend" hint={rangeText}
              badge={realCosts?.connected ? { text: "Real bill from OpenAI", tone: "green" } : undefined} />
            {realCosts?.connected ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <KpiCard
                  accent
                  label="Total spend"
                  value={`$${realCosts.total_usd.toFixed(2)}`}
                  sub={formatInr(realCosts.total_usd, rate) ? `${formatInr(realCosts.total_usd, rate)} at ₹${rate!.rate.toFixed(2)}/$` : null}
                />
                {/* Real spend over the pages BILLED in the same range (the billing ledger), not the
                    pages still attached to documents - that left out re-runs and deleted jobs and
                    read 40% too high on 24 Nov 2026. */}
                <KpiCard
                  label="Cost per billed page"
                  value={perPageUsd == null ? "—" : usdToInr ? rupees(perPageUsd * usdToInr, 3) : `$${perPageUsd.toFixed(4)}`}
                  sub={perPageUsd == null ? "No billed pages in this range" : `$${perPageUsd.toFixed(4)} · ${n(billed)} billed page${billed === 1 ? "" : "s"}`}
                />
                <KpiCard
                  label="Cost per job"
                  value={perJobUsd == null ? "—" : usdToInr ? rupees(perJobUsd * usdToInr) : `$${perJobUsd.toFixed(4)}`}
                  sub={perJobUsd == null ? "No jobs in this range" : `$${perJobUsd.toFixed(4)} · ${n(jobs)} job${jobs === 1 ? "" : "s"}`}
                />
                <KpiCard
                  label="OpenAI balance"
                  value={settings?.openai_balance_usd != null ? `$${settings.openai_balance_usd.toFixed(2)}` : "Not set"}
                  sub={settings?.openai_balance_usd != null
                    ? [formatInr(settings.openai_balance_usd, rate),
                       settings.openai_balance_expiry ? `expires ${niceDate(settings.openai_balance_expiry)}` : "no expiry set"]
                        .filter(Boolean).join(" · ")
                    : "Enter it from platform.openai.com"}
                >
                  <button type="button" onClick={openBalanceModal}
                    className="text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400">
                    {settings?.openai_balance_usd != null ? "Update balance" : "+ Add balance"}
                  </button>
                </KpiCard>
              </div>
            ) : realCostsError ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
                Your OpenAI Admin key is connected, but reading real spend failed: {realCostsError}
              </div>
            ) : (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
                Real dollar spend isn't connected yet. <Link to="/super-admin/settings" className="underline">Connect your OpenAI Admin API key in Settings</Link>{" "}
                to see the actual bill here.
              </div>
            )}
          </section>

          {/* 2. Work done */}
          <section>
            <SectionTitle title="Usage" hint="Counted by this app" />
            {/* Billed Pages is the invoice's own count; Pages on documents is what is still attached
                to the documents of jobs that still exist, so it is lower. */}
            <StatStrip items={[
              { label: "Jobs", value: n(data.totals.jobs), sub: data.totals.zero_result_jobs > 0 ? `${n(data.totals.zero_result_jobs)} with no result` : "all returned data" },
              { label: "Documents", value: n(data.totals.documents), sub: "uploaded" },
              { label: "Billed pages", value: n(data.totals.billed_pages),
                sub: data.totals.billed_rerun_pages > 0 ? `incl. ${n(data.totals.billed_rerun_pages)} re-run` : "what the invoice counts" },
              { label: "Pages on documents", value: n(data.totals.pages), sub: "still attached to a job", muted: true },
            ]} />
          </section>

          {/* 3. Tokens: real first, the app's own estimate greyed */}
          <section>
            <SectionTitle title="Tokens" />
            <StatStrip items={[
              { label: "Input tokens", value: realCosts?.connected ? n(realCosts.total_input_tokens) : "—", sub: "real, from OpenAI" },
              { label: "Output tokens", value: realCosts?.connected ? n(realCosts.total_output_tokens) : "—", sub: "real, from OpenAI" },
              { label: "Estimated tokens", value: n(data.totals.estimated_tokens), sub: "app estimate (characters ÷ 4)", muted: true },
              { label: "Characters read", value: n(data.totals.characters), sub: "OCR text", muted: true },
            ]} />
          </section>

          {/* 4. Charts */}
          <section className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Card className="p-5 lg:col-span-2">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">{activeMetric.label} per day</h2>
                <select
                  value={metric}
                  onChange={(e) => setMetric(e.target.value as Metric)}
                  aria-label="Chart metric"
                  className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                >
                  {METRICS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
              <BarChart days={data.days} metric={metric} color={activeMetric.color} />
            </Card>

            <Card className="p-5">
              <h2 className="mb-1 font-semibold text-slate-900 dark:text-slate-50">Job outcomes</h2>
              <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">
                Every job is paid for when it extracts. Red is the share that came back with nothing usable.
              </p>
              <PieChart
                slices={[
                  { label: "Returned data", value: data.totals.jobs - data.totals.zero_result_jobs, color: "#10b981" },
                  { label: "Zero result", value: data.totals.zero_result_jobs, color: "#f43f5e" },
                ]}
              />
            </Card>
          </section>

          <details className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
            <summary className="cursor-pointer font-medium text-slate-600 dark:text-slate-300">How these numbers are worked out</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>Spend, input and output tokens are the real figures from OpenAI's Admin API, for all projects on this account. Google Document AI is not included.</li>
              <li>Cost per billed page = total spend ÷ billed pages (the pages the invoice counts, re-runs included), in rupees at today's rate.</li>
              <li>Cost per job = total spend ÷ jobs created in the same range.</li>
              <li>Estimated tokens is not the bill: it is the OCR text this app read ({n(data.totals.characters)} characters) divided by 4, OpenAI's rule of thumb for English.</li>
              <li>OpenAI has no API for the account balance, so it is entered by hand.</li>
            </ul>
          </details>
        </div>
      )}

      <Modal open={balanceModalOpen} onClose={() => setBalanceModalOpen(false)} title="OpenAI Account Balance">
        <form className="flex flex-col gap-4" onSubmit={saveBalance}>
          <p className="text-xs text-slate-400">
            OpenAI has no API for this — check platform.openai.com (Settings → Organization →
            Billing) and enter the current figures here.
          </p>
          <Input
            label="Balance (USD)"
            type="number"
            step="0.01"
            min="0"
            value={balanceInput}
            onChange={(e) => setBalanceInput(e.target.value)}
            placeholder="e.g. 250.00"
          />
          <Input
            label="Expiry date (optional)"
            type="date"
            value={expiryInput}
            onChange={(e) => setExpiryInput(e.target.value)}
          />
          {balanceError && <Alert>{balanceError}</Alert>}
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => {
                setBalanceInput("");
                setExpiryInput("");
              }}
              className="text-xs font-medium text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
            >
              Clear
            </button>
            <Button type="submit" isLoading={savingBalance}>Save</Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
