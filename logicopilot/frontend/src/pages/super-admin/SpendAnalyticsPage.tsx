import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as systemSettingsApi from "../../api/systemSettings";
import type { OpenAICosts, SpendAnalytics, SystemSettings, UsdInrRate } from "../../api/systemSettings";

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

/** Same shape as the shared StatCard, plus one small secondary line - the INR conversion,
 *  an "across N jobs" note, an expiry date - none of which fit StatCard's plain string value. */
function StatCardWithSub({ label, value, sub }: { label: string; value: string; sub: string | null }) {
  return (
    <Card className="p-5">
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">{value}</p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </Card>
  );
}

/** Super Admin's "how much did this actually cost us" screen. Two very different numbers
 *  live side by side on purpose: REAL spend from OpenAI's own Admin API (once connected in
 *  Settings) and this app's own local counts, including an ESTIMATED token figure that is
 *  clearly labeled as such — the two should never be presented as if they were the same
 *  thing, since they can differ by an order of magnitude. */
export function SpendAnalyticsPage() {
  const [range, setRange] = useState(() => preset(30));
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
      })
      .catch(() => setError("Could not load spend analytics."))
      .finally(() => setLoading(false));
  }, [range]);

  const activeMetric = METRICS.find((m) => m.value === metric)!;

  return (
    <AppShell
      title="Spend Analytics"
      subtitle="Jobs, documents, pages, and token usage — platform-wide, across every tenant."
    >
      <Card className="mb-6 p-5">
        <div className="flex flex-wrap items-end gap-3">
          {[
            { label: "Today", days: 1 },
            { label: "This week", days: 7 },
            { label: "This month", days: 30 },
            { label: "Last 90 days", days: 90 },
          ].map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => setRange(preset(p.days))}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:border-indigo-300 hover:text-indigo-700 dark:border-slate-700 dark:text-slate-300"
            >
              {p.label}
            </button>
          ))}
          <div className="flex items-end gap-2">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-slate-500 dark:text-slate-400">From</label>
              <input
                type="date"
                value={range.start}
                onChange={(e) => setRange((r) => ({ ...r, start: e.target.value }))}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-slate-500 dark:text-slate-400">To</label>
              <input
                type="date"
                value={range.end}
                onChange={(e) => setRange((r) => ({ ...r, end: e.target.value }))}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
          </div>
        </div>
      </Card>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      {!loading && data && (
        <>
          {realCosts?.connected ? (
            <div className="mb-6">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                Real spend — from OpenAI
              </p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-5">
                <StatCardWithSub
                  label="Actual Spend (USD)"
                  value={`$${realCosts.total_usd.toFixed(2)}`}
                  sub={formatInr(realCosts.total_usd, rate)}
                />
                <StatCard label="Real Input Tokens" value={realCosts.total_input_tokens.toLocaleString()} />
                <StatCard label="Real Output Tokens" value={realCosts.total_output_tokens.toLocaleString()} />
                <StatCardWithSub
                  label="Average Spend / Job"
                  value={data.totals.jobs > 0 ? `$${(realCosts.total_usd / data.totals.jobs).toFixed(4)}` : "—"}
                  sub={
                    data.totals.jobs > 0
                      ? [formatInr(realCosts.total_usd / data.totals.jobs, rate), `across ${data.totals.jobs.toLocaleString()} job${data.totals.jobs === 1 ? "" : "s"}`]
                          .filter(Boolean)
                          .join(" · ")
                      : "No jobs in this range"
                  }
                />
                <Card className="flex flex-col justify-between gap-2 p-5">
                  <div>
                    <p className="text-sm text-slate-500 dark:text-slate-400">OpenAI Account Balance</p>
                    {settings?.openai_balance_usd != null ? (
                      <>
                        <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
                          ${settings.openai_balance_usd.toFixed(2)}
                        </p>
                        <p className="text-xs text-slate-400">
                          {[
                            formatInr(settings.openai_balance_usd, rate),
                            settings.openai_balance_expiry
                              ? `Expires ${new Date(settings.openai_balance_expiry + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}`
                              : "No expiry set",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </>
                    ) : (
                      <p className="text-sm text-slate-400">Not set yet</p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={openBalanceModal}
                    className="self-start text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                  >
                    {settings?.openai_balance_usd != null ? "Update" : "+ Add balance"}
                  </button>
                </Card>
              </div>
            </div>
          ) : realCostsError ? (
            <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
              Your OpenAI Admin key is connected, but reading real spend failed: {realCostsError}
            </div>
          ) : (
            <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
              Real dollar spend isn't connected yet. <Link to="/super-admin/settings" className="underline">Connect your OpenAI Admin API key in Settings</Link>{" "}
              to see the actual bill here instead of an estimate.
            </div>
          )}

          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            This app's own counts (estimate)
          </p>
          <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            <StatCard label="Jobs Created" value={data.totals.jobs.toLocaleString()} />
            <StatCard label="Documents Uploaded" value={data.totals.documents.toLocaleString()} />
            <StatCard label="Total Pages" value={data.totals.pages.toLocaleString()} />
            <StatCard label="Characters Read" value={data.totals.characters.toLocaleString()} />
            <StatCard label="Est. Tokens Spent" value={data.totals.estimated_tokens.toLocaleString()} />
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Card className="p-5 lg:col-span-2">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-semibold text-slate-900 dark:text-slate-50">
                  {activeMetric.label} per day
                </h2>
                <div className="flex flex-wrap gap-1.5">
                  {METRICS.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => setMetric(m.value)}
                      className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                        metric === m.value
                          ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                          : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
              <BarChart days={data.days} metric={metric} color={activeMetric.color} />
            </Card>

            <Card className="p-5">
              <h2 className="mb-1 font-semibold text-slate-900 dark:text-slate-50">Job Outcomes</h2>
              <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">
                Every job costs a call the moment it extracts — this is the share that came
                back with nothing usable at all.
              </p>
              <PieChart
                slices={[
                  {
                    label: "Returned data",
                    value: data.totals.jobs - data.totals.zero_result_jobs,
                    color: "#10b981",
                  },
                  { label: "Zero result", value: data.totals.zero_result_jobs, color: "#f43f5e" },
                ]}
              />
            </Card>
          </div>

          <p className="mt-4 text-xs text-slate-400">
            "Est. Tokens" is an ESTIMATE, not the real OpenAI bill — it is computed from the
            real OCR text volume this system read for every document ({data.totals.characters.toLocaleString()}{" "}
            characters across {data.totals.jobs.toLocaleString()} job{data.totals.jobs === 1 ? "" : "s"}
            ), divided by 4 (OpenAI's own published rule of thumb for English text).
            {realCosts?.connected
              ? " The \"Real spend\" figures above are the actual OpenAI numbers, not an estimate."
              : " Connect an OpenAI Admin API key in Settings to replace this estimate with the real billed figure."}
          </p>
        </>
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
    </AppShell>
  );
}
