import { Fragment, useState } from "react";
import axios from "axios";
import { Card } from "./ui/Card";
import { Button } from "./ui/Button";
import { FIELD_CLASS } from "./tenant-admin/masterDataUi";
import * as api from "../api/customsTariff";
import type { DumpCheck, TariffTryResult } from "../api/customsTariff";

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

const HOW: Record<string, { label: string; cls: string }> = {
  document: { label: "Document CTH is in the tariff", cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" },
  ai: { label: "AI nearest match", cls: "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" },
  fallback: { label: "Fallback - subheading's 'Other' code", cls: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300" },
};

/** SaaS Admin: try the nearest-CTH engine on one line (nothing is saved). */
export function TariffTryCard() {
  const [form, setForm] = useState({ doc_cth: "", part_code: "", description: "" });
  const [result, setResult] = useState<TariffTryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.tryTariffMatch(form));
    } catch (err) {
      setError(errorText(err, "Could not run the match."));
    } finally {
      setBusy(false);
    }
  }

  const how = result ? HOW[result.how] : null;
  return (
    <Card className="p-5">
      <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">Try the matcher</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        What the AI would suggest for a product line that the customer's CTH dump has no code for. Nothing is saved.
      </p>
      <div className="mt-3 grid gap-2 md:grid-cols-[10rem_12rem_minmax(0,1fr)_auto]">
        <input className={FIELD_CLASS} placeholder="Document CTH (optional)" value={form.doc_cth}
          onChange={(e) => setForm({ ...form, doc_cth: e.target.value })} />
        <input className={FIELD_CLASS} placeholder="Part code (optional)" value={form.part_code}
          onChange={(e) => setForm({ ...form, part_code: e.target.value })} />
        <input className={FIELD_CLASS} placeholder="Description, e.g. Printed circuit board assembly for base station" value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          onKeyDown={(e) => { if (e.key === "Enter") void run(); }} />
        <Button size="sm" className="px-4 py-2" isLoading={busy}
          disabled={!form.doc_cth.trim() && !form.part_code.trim() && !form.description.trim()} onClick={() => void run()}>
          Suggest CTH
        </Button>
      </div>
      {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
      {result && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 px-4 py-3 text-sm dark:border-slate-700">
          {result.cth ? (
            <span className="rounded-md bg-emerald-50 px-2 py-0.5 font-mono text-base font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
              {result.cth.slice(0, 4)} {result.cth.slice(4, 6)} {result.cth.slice(6)}
            </span>
          ) : <span className="font-medium text-slate-500">No code</span>}
          {how && <span className={`rounded px-2 py-0.5 text-xs font-medium ${how.cls}`}>{how.label}</span>}
          {result.confidence && <span className="text-xs text-slate-500">confidence: <b>{result.confidence}</b></span>}
          {result.code && <span className="text-xs text-slate-500">Unit {result.code.unit ?? "—"} · Rate {result.code.rate ?? "—"}</span>}
          <span className="w-full text-xs text-slate-500">{result.note}</span>
        </div>
      )}
    </Card>
  );
}

/** SaaS Admin: each customer CTH dump against the tariff (read-only). */
export function DumpCheckCard() {
  const [items, setItems] = useState<DumpCheck[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setItems(await api.getDumpCheck());
    } catch (err) {
      setError(errorText(err, "Could not check the dumps."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">Customer CTH dumps vs this tariff</h2>
          <p className="mt-1 max-w-3xl text-sm text-slate-500 dark:text-slate-400">
            Codes in a customer's dump that are not in the Indian Customs Tariff (old, wrong or placeholder values like "NA").
            Once switched on for a template, these will not be used - the AI suggests an allowed code instead. Fix them in Master Data → CTH Master.
          </p>
        </div>
        <Button size="sm" variant="secondary" isLoading={busy} onClick={() => void load()}>{items ? "Check again" : "Check dumps"}</Button>
      </div>
      {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
      {items && items.length === 0 && <p className="mt-3 text-sm text-slate-500">No template has a CTH dump.</p>}
      {items && items.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-500">
              <tr><th className="py-1.5 pr-4">Customer</th><th className="pr-4">Template</th><th className="pr-4 text-right">Rows with CTH</th><th className="pr-4 text-right">Distinct codes</th><th className="pr-4 text-right">Codes not in tariff</th><th className="pr-4 text-right">Rows affected</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {items.map((t) => (
                <Fragment key={t.group_id}>
                  <tr className="text-slate-700 dark:text-slate-300">
                    <td className="py-2 pr-4">{t.tenant}</td>
                    <td className="pr-4 font-medium">{t.template}</td>
                    {t.ready ? (
                      <>
                        <td className="pr-4 text-right">{t.rows_with_cth?.toLocaleString()}</td>
                        <td className="pr-4 text-right">{t.distinct_codes?.toLocaleString()}</td>
                        <td className={`pr-4 text-right font-semibold ${t.codes_not_in_tariff ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300"}`}>{t.codes_not_in_tariff}</td>
                        <td className="pr-4 text-right">{t.rows_not_in_tariff?.toLocaleString()}</td>
                        <td className="text-right">
                          {!!t.examples?.length && (
                            <button type="button" className="text-xs text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => setOpen(open === t.group_id ? null : t.group_id)}>
                              {open === t.group_id ? "Hide" : "Examples"}
                            </button>
                          )}
                        </td>
                      </>
                    ) : <td colSpan={5} className="text-xs text-slate-500">Reading the dump… press Check again in a minute.</td>}
                  </tr>
                  {open === t.group_id && (
                    <tr>
                      <td colSpan={7} className="bg-slate-50 px-3 py-2 dark:bg-slate-800/40">
                        <ul className="space-y-0.5 text-xs text-slate-600 dark:text-slate-300">
                          {t.examples!.map((e, i) => (
                            <li key={i}><span className="font-mono font-semibold">{e.cth}</span> · {e.part_code || "—"} · {e.description}</li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
