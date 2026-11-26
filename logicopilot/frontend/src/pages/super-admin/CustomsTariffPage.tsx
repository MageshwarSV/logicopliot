import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { Pagination } from "../../components/ui/Pagination";
import { DownloadIcon, SearchIcon, UploadIcon } from "../../components/ui/icons";
import { FIELD_CLASS, OUTLINE_BTN } from "../../components/tenant-admin/masterDataUi";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/customsTariff";
import type { TariffCode, TariffPreview, TariffSummary, TariffUpload } from "../../api/customsTariff";

const PAGE_SIZES = [25, 50, 100, 200];

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Indian Customs Tariff (ICEGATE) - the one CTH master for the whole platform, every tenant and
 *  every stream. SaaS Admin replaces it from Excel/CSV (preview first, then apply); Tenant Admin
 *  sees the same list read-only. A code dropped by a new file is marked inactive, never deleted.
 *  Separate from each customer's own CTH dump (Master Data -> CTH Master), which is unchanged. */
export function CustomsTariffPage({ readOnly = false }: { readOnly?: boolean }) {
  const [summary, setSummary] = useState<TariffSummary | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [data, setData] = useState<{ total: number; rows: TariffCode[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploads, setUploads] = useState<TariffUpload[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<TariffPreview | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await api.getTariffSummary());
      if (!readOnly) setUploads(await api.listTariffUploads());
    } catch (err) {
      setError(errorText(err, "Could not load the tariff."));
    }
  }, [readOnly]);

  useEffect(() => { void loadSummary(); }, [loadSummary]);

  useEffect(() => {
    const t = window.setTimeout(() => { setQuery(search.trim()); setPage(1); }, 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const loadRows = useCallback(async () => {
    try {
      setData(await api.searchTariff({ q: query || undefined, include_inactive: showInactive, page, page_size: pageSize }));
    } catch (err) {
      setError(errorText(err, "Could not load the tariff codes."));
    }
  }, [query, showInactive, page, pageSize]);

  useEffect(() => { void loadRows(); }, [loadRows]);

  async function choose(f: File | undefined) {
    if (fileRef.current) fileRef.current.value = "";
    if (!f) return;
    setFile(f);
    setPreview(null);
    setError(null);
    setBusy("preview");
    try {
      setPreview(await api.previewTariffUpload(f));
    } catch (err) {
      setFile(null);
      setError(errorText(err, "Could not read that file."));
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!file) return;
    setBusy("apply");
    try {
      const r = await api.applyTariffUpload(file);
      setNotice(`Tariff updated: ${r.total_codes.toLocaleString()} codes · ${r.added} added · ${r.changed} changed · ${r.deactivated} made inactive.`);
      setFile(null);
      setPreview(null);
      await Promise.all([loadSummary(), loadRows()]);
    } catch (err) {
      setError(errorText(err, "Could not update the tariff."));
    } finally {
      setBusy(null);
    }
  }

  const last = summary?.last_upload;
  const offset = (page - 1) * pageSize;

  return (
    <AppShell
      title="Indian Customs Tariff (ICEGATE)"
      subtitle="The one CTH master for every customer and stream - only codes in this list can be suggested, picked or sent to an ERP"
    >
      <div className="flex flex-col gap-4">
        {error && <Alert>{error} <button type="button" className="ml-2 underline" onClick={() => setError(null)}>dismiss</button></Alert>}
        {notice && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">
            {notice} <button type="button" className="ml-2 text-xs underline" onClick={() => setNotice(null)}>dismiss</button>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Active CTH codes" value={summary ? summary.active.toLocaleString() : "…"} tone="indigo" />
          <Stat label="Inactive (dropped by a later file)" value={summary ? summary.inactive.toLocaleString() : "…"} tone="slate" />
          <Stat label="With description" value={summary ? summary.with_description.toLocaleString() : "…"} tone={summary && summary.active > 0 && summary.with_description === 0 ? "amber" : "emerald"}
            hint={summary && summary.active > 0 && summary.with_description === 0 ? "This file has no descriptions" : undefined} />
          <Stat label="Last updated" value={last?.uploaded_at ? formatDateTime(last.uploaded_at, { year: true }) : "Never"} tone="slate"
            hint={last ? `${last.file_name}${last.uploaded_by ? ` · ${last.uploaded_by}` : ""}` : "No tariff uploaded yet"} small />
        </div>

        {!readOnly && (
          <Card className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">Replace the tariff</h2>
                <p className="mt-1 max-w-3xl text-sm text-slate-500 dark:text-slate-400">
                  Upload the whole list (Excel or CSV) with a <b>CTH</b> column; <b>Description</b>, <b>Rate</b>, <b>Unit</b> and <b>Preferential Rate</b> are read when present.
                  You see what changes before anything is saved. Codes missing from the new file become inactive - never deleted.
                </p>
              </div>
              <div className="flex gap-2">
                <button type="button" className={OUTLINE_BTN} disabled={!summary?.active} onClick={() => void api.downloadTariff(false).catch((e) => setError(errorText(e, "Could not download.")))}>
                  <DownloadIcon className="h-4 w-4" /> Download current
                </button>
                <Button size="sm" className="px-4 py-2" isLoading={busy === "preview"} onClick={() => fileRef.current?.click()}>
                  <UploadIcon className="h-4 w-4" /> Upload new file
                </Button>
                <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv" className="hidden" onChange={(e) => void choose(e.target.files?.[0])} />
              </div>
            </div>

            {uploads.length > 0 && (
              <details className="mt-4">
                <summary className="cursor-pointer text-sm font-medium text-indigo-700 dark:text-indigo-300">Upload history ({uploads.length})</summary>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-slate-500">
                      <tr><th className="py-1.5 pr-4">When</th><th className="pr-4">File</th><th className="pr-4">By</th><th className="pr-4 text-right">Codes</th><th className="pr-4 text-right">Added</th><th className="pr-4 text-right">Changed</th><th className="pr-4 text-right">Inactive</th><th className="text-right">Skipped rows</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {uploads.map((u) => (
                        <tr key={u.id} className="text-slate-700 dark:text-slate-300">
                          <td className="py-1.5 pr-4">{u.uploaded_at ? formatDateTime(u.uploaded_at, { year: true }) : "—"}</td>
                          <td className="pr-4">{u.file_name}</td>
                          <td className="pr-4">{u.uploaded_by ?? "—"}</td>
                          <td className="pr-4 text-right">{u.total_codes.toLocaleString()}</td>
                          <td className="pr-4 text-right">{u.added}</td>
                          <td className="pr-4 text-right">{u.changed}</td>
                          <td className="pr-4 text-right">{u.deactivated}</td>
                          <td className="text-right">{u.skipped_rows}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </Card>
        )}

        <Card className="overflow-hidden">
          <div className="flex flex-col gap-3 px-6 py-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">CTH codes</h2>
              {data && (
                <span className="rounded-lg bg-indigo-50 px-2.5 py-1 text-sm font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
                  {data.total.toLocaleString()} {query ? "matching" : ""}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={showInactive} onChange={(e) => { setShowInactive(e.target.checked); setPage(1); }} />
                Show inactive
              </label>
              <div className="relative">
                <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                  placeholder="CTH digits (e.g. 8517) or words…" className={`${FIELD_CLASS} w-72 pl-9`} />
              </div>
              {readOnly && (
                <button type="button" className={OUTLINE_BTN} disabled={!summary?.active} onClick={() => void api.downloadTariff(false).catch((e) => setError(errorText(e, "Could not download.")))}>
                  <DownloadIcon className="h-4 w-4" /> Export
                </button>
              )}
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-indigo-50/60 text-slate-800 dark:bg-slate-800/60 dark:text-slate-200">
                  <th className="w-20 px-6 py-3 font-semibold">S.No</th>
                  <th className="px-4 py-3 font-semibold">CTH</th>
                  <th className="px-4 py-3 font-semibold">Description</th>
                  <th className="px-4 py-3 font-semibold">Rate</th>
                  <th className="px-4 py-3 font-semibold">Unit</th>
                  <th className="px-4 py-3 font-semibold">Preferential Rate</th>
                  {showInactive && <th className="px-4 py-3 font-semibold">Status</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {!data ? (
                  <tr><td colSpan={7} className="py-16 text-center text-slate-500">Loading…</td></tr>
                ) : data.rows.length === 0 ? (
                  <tr><td colSpan={7} className="py-16 text-center text-slate-500">
                    {summary && summary.active + summary.inactive === 0 ? "No tariff uploaded yet." : "No codes match this search."}
                  </td></tr>
                ) : data.rows.map((r, i) => (
                  <tr key={r.cth} className={`hover:bg-slate-50/60 dark:hover:bg-slate-800/30 ${r.is_active ? "" : "opacity-60"}`}>
                    <td className="px-6 py-2.5 text-slate-500">{offset + i + 1}</td>
                    <td className="px-4 py-2.5">
                      <span className="rounded-md bg-emerald-50 px-2 py-0.5 font-mono font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
                        {r.cth.slice(0, 4)} {r.cth.slice(4, 6)} {r.cth.slice(6)}
                      </span>
                    </td>
                    <td className="max-w-xl px-4 py-2.5 text-slate-700 dark:text-slate-300">{r.description || <span className="text-slate-400">—</span>}</td>
                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{r.rate ?? "—"}</td>
                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{r.unit ?? "—"}</td>
                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{r.preferential_rate ?? "—"}</td>
                    {showInactive && (
                      <td className="px-4 py-2.5">
                        {r.is_active
                          ? <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">Active</span>
                          : <span className="text-xs font-medium text-slate-500">Inactive{r.deactivated_at ? ` since ${formatDateTime(r.deactivated_at, { year: true })}` : ""}</span>}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data && data.total > 0 && (
            <div className="px-6 pb-4">
              <Pagination page={page} pageSize={pageSize} total={data.total} onPageChange={setPage} noun="codes" showEnds
                extra={
                  <label className="mr-3 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                    Rows per page:
                    <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} className={`${FIELD_CLASS} py-1`}>
                      {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </label>
                } />
            </div>
          )}
        </Card>
      </div>

      <Modal open={!!preview} onClose={() => { if (busy !== "apply") { setPreview(null); setFile(null); } }} title="Check before replacing the tariff" maxWidth="max-w-2xl">
        {preview && (
          <div className="flex flex-col gap-4 text-sm">
            <p className="text-slate-600 dark:text-slate-300">
              <b>{file?.name}</b> has <b>{preview.total_codes.toLocaleString()}</b> valid CTH codes.
              Columns read: {preview.columns.map((c) => c.replace("_", " ")).join(", ")}.
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <MiniStat label="New codes" value={preview.added} tone="emerald" />
              <MiniStat label="Changed" value={preview.changed} tone="indigo" />
              <MiniStat label="Back to active" value={preview.reactivated} tone="indigo" />
              <MiniStat label="Become inactive" value={preview.deactivated} tone={preview.large_removal ? "rose" : "amber"} />
            </div>
            {preview.large_removal && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
                This file would make <b>{preview.deactivated.toLocaleString()}</b> of the {preview.active_now.toLocaleString()} active codes inactive. Check it is the full, latest tariff.
              </div>
            )}
            {preview.skipped_rows > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                <p className="font-medium">{preview.skipped_rows} row(s) skipped - fix them in the file if they should be codes:</p>
                <ul className="mt-1 list-disc pl-5 text-xs">
                  {preview.examples_skipped.map((s) => <li key={s.row}>Row {s.row}: "{s.value}" - {s.reason}</li>)}
                </ul>
              </div>
            )}
            {(preview.examples_deactivated.length > 0 || preview.examples_changed.length > 0 || preview.examples_added.length > 0) && (
              <details>
                <summary className="cursor-pointer text-indigo-700 dark:text-indigo-300">Examples</summary>
                <div className="mt-2 space-y-1 text-xs text-slate-600 dark:text-slate-300">
                  {preview.examples_added.length > 0 && <p><b>New:</b> {preview.examples_added.join(", ")}</p>}
                  {preview.examples_deactivated.length > 0 && <p><b>Become inactive:</b> {preview.examples_deactivated.join(", ")}</p>}
                  {preview.examples_changed.map((c) => (
                    <p key={c.cth}><b>{c.cth}:</b> {Object.entries(c.changes).map(([k, v]) => `${k.replace("_", " ")} ${v.from ?? "—"} → ${v.to ?? "—"}`).join("; ")}</p>
                  ))}
                </div>
              </details>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" disabled={busy === "apply"} onClick={() => { setPreview(null); setFile(null); }}>Cancel</Button>
              <Button isLoading={busy === "apply"} onClick={() => void apply()}>Replace the tariff</Button>
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}

const TONES: Record<string, string> = {
  indigo: "text-indigo-700 dark:text-indigo-300",
  emerald: "text-emerald-700 dark:text-emerald-300",
  amber: "text-amber-700 dark:text-amber-300",
  rose: "text-rose-700 dark:text-rose-300",
  slate: "text-slate-800 dark:text-slate-100",
};

function Stat({ label, value, tone, hint, small }: { label: string; value: string; tone: string; hint?: string; small?: boolean }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
      <p className={`mt-1 font-semibold ${small ? "text-base" : "text-2xl"} ${TONES[tone]}`}>{value}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400" title={hint}>{hint}</p>}
    </Card>
  );
}

function MiniStat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className={`text-lg font-semibold ${TONES[tone]}`}>{value.toLocaleString()}</p>
    </div>
  );
}
