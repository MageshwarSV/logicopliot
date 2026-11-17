import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { Pagination } from "../../components/ui/Pagination";
import { DatabaseIcon, DocumentIcon, DownloadIcon, LayersIcon, SearchIcon, UploadIcon } from "../../components/ui/icons";
import { FIELD_CLASS, OUTLINE_BTN, SortHeader } from "../../components/tenant-admin/masterDataUi";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/masterData";
import type { CthRowsPage, CthTemplate } from "../../api/masterData";

const PAGE_SIZES = [10, 25, 50, 100];
type SortKey = "part" | "description" | "cth";

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Tenant Admin -> Master Data -> CTH Master. The customer's CTH dump (their material master
 *  workbook) for one template: read-only rows, search, sort, and "Upload New Dump" whenever a
 *  new one arrives. The file stays the source - jobs keep reading it exactly as before. */
export function CthMasterDataPage() {
  const [templates, setTemplates] = useState<CthTemplate[] | null>(null);
  const [groupId, setGroupId] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "part", dir: "asc" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [data, setData] = useState<CthRowsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [justReplaced, setJustReplaced] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const wasChecking = useRef(false);

  useEffect(() => {
    api.listCthTemplates()
      .then((list) => {
        setTemplates(list);
        setGroupId((g) => (list.some((t) => t.group_id === g) ? g : list[0]?.group_id ?? ""));
      })
      .catch((err) => {
        setError(errorText(err, "Could not load the CTH master."));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (search.trim() === query) return;
    const t = window.setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(t);
  }, [search, query]);

  const loadRows = useCallback(async () => {
    if (!groupId) return;
    try {
      const d = await api.listCthRows(groupId, { q: query || undefined, sort: sort.key, direction: sort.dir, page, page_size: pageSize });
      // An upload that was being checked has finished: say so once if it went in.
      if (wasChecking.current && !d.checking_upload && !d.upload_error && d.file_name) setJustReplaced(d.file_name);
      wasChecking.current = d.checking_upload;
      setData(d);
    } catch (err) {
      setError(errorText(err, "Could not load the CTH dump."));
    } finally {
      setLoading(false);
    }
  }, [groupId, query, sort, page, pageSize]);

  useEffect(() => {
    setLoading(true);
    loadRows();
  }, [loadRows]);

  // While the sheet is being read or a new upload is being checked, poll every 3 s.
  const busy = Boolean(data && (!data.ready || data.checking_upload));
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(loadRows, 3000);
    return () => window.clearInterval(t);
  }, [busy, loadRows]);

  useEffect(() => {
    if (!justReplaced) return;
    const t = window.setTimeout(() => setJustReplaced(null), 6000);
    return () => window.clearTimeout(t);
  }, [justReplaced]);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
    setPage(1);
  }

  async function download(kind: "download" | "template") {
    setError(null);
    try {
      await api.downloadCthFile(groupId, kind);
    } catch (err) {
      setError(errorText(err, "Could not download the file."));
    }
  }

  function pickFile(file: File | undefined) {
    if (fileRef.current) fileRef.current.value = "";
    if (!file) return;
    if (!/\.(xlsx|xlsm)$/i.test(file.name)) {
      setError("Choose an .xlsx or .xlsm workbook (an old .xls cannot be read).");
      return;
    }
    setPendingFile(file);
  }

  async function confirmUpload() {
    if (!pendingFile) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadCthDump(groupId, pendingFile);
      setPendingFile(null);
      wasChecking.current = true;
      await loadRows();
    } catch (err) {
      setError(errorText(err, "Could not upload the file."));
    } finally {
      setUploading(false);
    }
  }

  const template = templates?.find((t) => t.group_id === groupId);
  const offset = (page - 1) * pageSize;

  return (
    <AppShell
      title="ERP Master Data – CTH Master"
      subtitle="The customer's CTH dump - part codes and descriptions with the CTH used on a job. Upload the new dump whenever it arrives."
      icon={<DatabaseIcon className="h-6 w-6" />}
    >
      <div className="space-y-5">
        {error && <Alert>{error}</Alert>}
        {justReplaced && (
          <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
            New dump "{justReplaced}" is now in use for jobs.
          </div>
        )}
        {data?.checking_upload && (
          <div role="status" className="rounded-lg border border-indigo-200 bg-indigo-50 px-3.5 py-2.5 text-sm text-indigo-700 dark:border-indigo-500/20 dark:bg-indigo-500/10 dark:text-indigo-300">
            Checking the uploaded dump… this can take up to a minute for a large file. Jobs keep using the current dump until it is ready.
          </div>
        )}
        {data?.upload_error && !data.checking_upload && <Alert>{data.upload_error}</Alert>}

        {templates && templates.length === 0 ? (
          <Card className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">No template in your account uses a CTH dump.</Card>
        ) : (
          <Card className="overflow-hidden">
            <div className="flex flex-col gap-4 px-6 py-5 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <div className="flex items-center gap-3">
                  <h2 className="text-2xl font-semibold text-slate-900 dark:text-slate-50">CTH Master</h2>
                  {data?.ready && data.attached && (
                    <span className="rounded-lg bg-indigo-50 px-2.5 py-1 text-sm font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
                      {data.total.toLocaleString()} {query ? "matching" : ""} {data.total === 1 ? "Record" : "Records"}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                  {data?.attached
                    ? <>Current dump: <span className="font-medium text-slate-700 dark:text-slate-200">{data.file_name}</span>{data.uploaded_at ? ` · uploaded ${formatDateTime(data.uploaded_at, { year: true })}` : ""}</>
                    : template ? "No dump uploaded for this template yet." : ""}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2">
                  <LayersIcon className="h-5 w-5 text-slate-500" />
                  <select value={groupId} onChange={(e) => { setGroupId(e.target.value); setPage(1); setData(null); }} className={`${FIELD_CLASS} w-56`} aria-label="Template" title="Template">
                    {templates?.map((t) => <option key={t.group_id} value={t.group_id}>{t.name}</option>)}
                  </select>
                </label>
                <div className="relative">
                  <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search part code, description or CTH..." className={`${FIELD_CLASS} w-72 pl-9`} />
                </div>
                <button type="button" className={OUTLINE_BTN} disabled={!data?.attached} onClick={() => download("download")} title="Download the current dump file">
                  <DownloadIcon className="h-4 w-4" /> Export
                </button>
                <button type="button" className={OUTLINE_BTN} disabled={!groupId} onClick={() => download("template")}>
                  <DocumentIcon className="h-4 w-4" /> Template
                </button>
                <Button size="sm" className="px-4 py-2" disabled={!groupId || uploading || data?.checking_upload} onClick={() => fileRef.current?.click()}>
                  <UploadIcon className="h-4 w-4" /> Upload New Dump
                </Button>
                <input ref={fileRef} type="file" accept=".xlsx,.xlsm" className="hidden" onChange={(e) => pickFile(e.target.files?.[0])} />
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="bg-indigo-50/60 text-slate-800 dark:bg-slate-800/60 dark:text-slate-200">
                    <th className="w-20 px-6 py-3 font-semibold">S.No</th>
                    <SortHeader label="Part Code" active={sort.key === "part"} dir={sort.dir} onClick={() => toggleSort("part")} />
                    <SortHeader label="Description" active={sort.key === "description"} dir={sort.dir} onClick={() => toggleSort("description")} />
                    <SortHeader label="CTH" active={sort.key === "cth"} dir={sort.dir} onClick={() => toggleSort("cth")} />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {loading || !data ? (
                    <tr><td colSpan={4} className="py-16 text-center text-slate-500 dark:text-slate-400">Loading…</td></tr>
                  ) : !data.attached ? (
                    <tr><td colSpan={4} className="py-16 text-center text-slate-500 dark:text-slate-400">No CTH dump yet. Use "Upload New Dump" to add the customer's sheet.</td></tr>
                  ) : !data.ready ? (
                    <tr><td colSpan={4} className="py-16 text-center text-slate-500 dark:text-slate-400">Reading the dump… a large sheet can take up to a minute the first time.</td></tr>
                  ) : data.rows.length === 0 ? (
                    <tr><td colSpan={4} className="py-16 text-center text-slate-500 dark:text-slate-400">{query ? "No rows match this search." : "The dump has no rows."}</td></tr>
                  ) : (
                    data.rows.map((r, i) => (
                      <tr key={`${offset + i}`} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                        <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{offset + i + 1}</td>
                        <td className="px-4 py-3 font-mono font-medium text-slate-900 dark:text-slate-100">{r.part_code || <span className="font-sans text-slate-400">—</span>}</td>
                        <td className="max-w-2xl px-4 py-3 text-slate-700 dark:text-slate-300">{r.description || <span className="text-slate-400">—</span>}</td>
                        <td className="px-4 py-3">
                          <span className="rounded-md bg-emerald-50 px-2 py-0.5 font-mono font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" title={r.cth_as_written !== r.cth ? `As written in the sheet: ${r.cth_as_written}` : undefined}>
                            {r.cth || "—"}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {data?.ready && data.total > 0 && (
              <div className="px-6 pb-4">
                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={data.total}
                  onPageChange={setPage}
                  noun="records"
                  showEnds
                  extra={
                    <label className="mr-3 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                      Rows per page:
                      <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} className={`${FIELD_CLASS} py-1`}>
                        {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </label>
                  }
                />
              </div>
            )}
          </Card>
        )}
      </div>

      <Modal open={pendingFile !== null} onClose={() => setPendingFile(null)} title="Upload new CTH dump?" maxWidth="max-w-lg">
        <div className="space-y-3 text-sm text-slate-600 dark:text-slate-300">
          <p>
            <span className="font-medium text-slate-900 dark:text-slate-100">{pendingFile?.name}</span> will replace the
            current dump{data?.file_name ? <> (<span className="font-medium">{data.file_name}</span>)</> : ""} for{" "}
            <span className="font-medium">{template?.name}</span>.
          </p>
          <p>
            The file is checked first: row 1 must have a CTH column and a part code or description column. If it can't be
            used, the current dump stays in place. New jobs use the new dump as soon as the check finishes.
          </p>
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setPendingFile(null)}>Cancel</Button>
          <Button isLoading={uploading} onClick={confirmUpload}>Upload and replace</Button>
        </div>
      </Modal>
    </AppShell>
  );
}
