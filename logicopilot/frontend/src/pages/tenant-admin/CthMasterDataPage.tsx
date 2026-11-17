import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { Pagination } from "../../components/ui/Pagination";
import {
  DatabaseIcon, DocumentIcon, DownloadIcon, LayersIcon, PencilIcon, PlusIcon, SearchIcon, UploadIcon,
} from "../../components/ui/icons";
import { FIELD_CLASS, OUTLINE_BTN, SortHeader } from "../../components/tenant-admin/masterDataUi";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/masterData";
import type { CthAddResult, CthRow, CthRowsPage, CthTemplate, CthValues } from "../../api/masterData";

const PAGE_SIZES = [10, 25, 50, 100];
type SortKey = "part" | "description" | "cth";

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Tenant Admin -> Master Data -> CTH Master. The customer's CTH dump workbook, one per
 *  template, works as the database: list, search, sort, edit one record, add one record or a
 *  small batch from the 3-column template. Every change is written into the dump file, which
 *  jobs read for their CTH. A full replacement of the dump is done by the backend team. */
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
  const [notice, setNotice] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editRow, setEditRow] = useState<CthRow | null>(null);
  const [adding, setAdding] = useState(false);
  const [addResult, setAddResult] = useState<CthAddResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(t);
  }, [notice]);

  const loadRows = useCallback(async () => {
    if (!groupId) return;
    try {
      setData(await api.listCthRows(groupId, { q: query || undefined, sort: sort.key, direction: sort.dir, page, page_size: pageSize }));
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

  // The first read of a large dump runs in the background - poll until it is ready.
  const reading = Boolean(data && data.attached && !data.ready);
  useEffect(() => {
    if (!reading) return;
    const t = window.setInterval(loadRows, 3000);
    return () => window.clearInterval(t);
  }, [reading, loadRows]);

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

  async function addFromFile(file: File | undefined) {
    if (fileRef.current) fileRef.current.value = "";
    if (!file) return;
    setError(null);
    setAddResult(null);
    setAdding(true);
    try {
      const r = await api.addCthRowsFromFile(groupId, file);
      setAddResult(r);
      await loadRows();
    } catch (err) {
      setError(errorText(err, "Could not add the rows."));
    } finally {
      setAdding(false);
    }
  }

  const template = templates?.find((t) => t.group_id === groupId);
  const offset = (page - 1) * pageSize;
  const canWrite = Boolean(data?.attached && data.ready);

  return (
    <AppShell
      title="ERP Master Data – CTH Master"
      subtitle="Part codes and descriptions with the CTH used on a job. Changes here are saved into the customer's CTH dump."
      icon={<DatabaseIcon className="h-6 w-6" />}
    >
      <div className="space-y-5">
        {error && <Alert>{error}</Alert>}
        {notice && (
          <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
            {notice}
          </div>
        )}
        {addResult && <AddSummary result={addResult} onClose={() => setAddResult(null)} />}

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
                    ? <>Dump: <span className="font-medium text-slate-700 dark:text-slate-200">{data.file_name}</span>{data.uploaded_at ? ` · last saved ${formatDateTime(data.uploaded_at, { year: true })}` : ""}</>
                    : template ? "No CTH dump for this template yet - the backend team uploads the first one." : ""}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2">
                  <LayersIcon className="h-5 w-5 text-slate-500" />
                  <select value={groupId} onChange={(e) => { setGroupId(e.target.value); setPage(1); setData(null); setAddResult(null); }} className={`${FIELD_CLASS} w-52`} aria-label="Template" title="Template">
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
                <button type="button" className={OUTLINE_BTN} disabled={!groupId} onClick={() => download("template")} title="Part Code, Description, CTH - for adding a few rows">
                  <DocumentIcon className="h-4 w-4" /> Template
                </button>
                <button type="button" className={OUTLINE_BTN} disabled={!canWrite || adding} onClick={() => fileRef.current?.click()} title="Add new rows from the filled-in Template">
                  <UploadIcon className="h-4 w-4" /> {adding ? "Adding…" : "Add Rows (Excel)"}
                </button>
                <input ref={fileRef} type="file" accept=".xlsx,.xlsm" className="hidden" onChange={(e) => addFromFile(e.target.files?.[0])} />
                <Button size="sm" className="px-4 py-2" disabled={!canWrite} onClick={() => setAddOpen(true)}>
                  <PlusIcon className="h-4 w-4" /> Add Record
                </Button>
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
                    <th className="w-20 px-6 py-3"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {loading || !data ? (
                    <tr><td colSpan={5} className="py-16 text-center text-slate-500 dark:text-slate-400">Loading…</td></tr>
                  ) : !data.attached ? (
                    <tr><td colSpan={5} className="py-16 text-center text-slate-500 dark:text-slate-400">No CTH dump for this template yet.</td></tr>
                  ) : !data.ready ? (
                    <tr><td colSpan={5} className="py-16 text-center text-slate-500 dark:text-slate-400">Reading the dump… a large sheet can take up to a minute the first time.</td></tr>
                  ) : data.rows.length === 0 ? (
                    <tr><td colSpan={5} className="py-16 text-center text-slate-500 dark:text-slate-400">{query ? "No rows match this search." : "The dump has no rows."}</td></tr>
                  ) : (
                    data.rows.map((r, i) => (
                      <tr key={r.row} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                        <td className="px-6 py-3 text-slate-500 dark:text-slate-400">{offset + i + 1}</td>
                        <td className="px-4 py-3 font-mono font-medium text-slate-900 dark:text-slate-100">{r.part_code || <span className="font-sans text-slate-400">—</span>}</td>
                        <td className="max-w-2xl px-4 py-3 text-slate-700 dark:text-slate-300">{r.description || <span className="text-slate-400">—</span>}</td>
                        <td className="px-4 py-3">
                          <span className="rounded-md bg-emerald-50 px-2 py-0.5 font-mono font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" title={r.cth_as_written !== r.cth ? `As written in the sheet: ${r.cth_as_written}` : undefined}>
                            {r.cth || "—"}
                          </span>
                        </td>
                        <td className="px-6 py-3">
                          <div className="flex justify-end">
                            <button
                              type="button"
                              title="Edit"
                              aria-label={`Edit ${r.part_code || r.description}`}
                              onClick={() => setEditRow(r)}
                              className="rounded-lg border border-slate-200 p-2 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                            >
                              <PencilIcon className="h-4 w-4" />
                            </button>
                          </div>
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

      <CthFormModal
        open={addOpen}
        title="Add CTH record"
        onClose={() => setAddOpen(false)}
        onSave={async (v) => {
          await api.addCthRow(groupId, v);
          setAddOpen(false);
          setNotice("Record added to the dump. New jobs use it straight away.");
          await loadRows();
        }}
      />

      {editRow && (
        <CthFormModal
          open
          title="Edit CTH record"
          initial={editRow}
          onClose={() => setEditRow(null)}
          onSave={async (v) => {
            await api.editCthRow(groupId, editRow.row, { part_code: editRow.part_code, description: editRow.description, cth: editRow.cth }, v);
            setEditRow(null);
            setNotice("Record saved in the dump. New jobs use it straight away.");
            await loadRows();
          }}
        />
      )}
    </AppShell>
  );
}

function AddSummary({ result, onClose }: { result: CthAddResult; onClose: () => void }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-5 py-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-slate-700 dark:text-slate-200">
          <b className="text-emerald-700 dark:text-emerald-400">{result.added}</b> {result.added === 1 ? "row" : "rows"} added to the dump
          {result.skipped.length > 0 && <>, <b className="text-amber-700 dark:text-amber-300">{result.skipped.length}</b> skipped:</>}
        </p>
        <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
      </div>
      {result.skipped.length > 0 && (
        <ul className="mt-2 max-h-60 space-y-1 overflow-y-auto text-sm">
          {result.skipped.map((s) => (
            <li key={s.row} className="text-slate-600 dark:text-slate-300">
              <span className="font-medium">{s.row}</span>{s.part_code ? ` (${s.part_code})` : ""} – {s.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CthFormModal({
  open, title, initial, onClose, onSave,
}: {
  open: boolean;
  title: string;
  initial?: CthValues;
  onClose: () => void;
  onSave: (v: CthValues) => Promise<void>;
}) {
  const [part, setPart] = useState("");
  const [desc, setDesc] = useState("");
  const [code, setCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setPart(initial?.part_code ?? "");
    setDesc(initial?.description ?? "");
    setCode(initial?.cth ?? "");
    setError(null);
  }, [open, initial]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!part.trim() && !desc.trim()) {
      setError("Give a Part Code or a Description.");
      return;
    }
    if (code.replace(/\D/g, "").length !== 8) {
      setError("CTH must be 8 digits.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({ part_code: part.trim(), description: desc.trim(), cth: code.trim() });
    } catch (err) {
      setError(errorText(err, "Could not save the record."));
    } finally {
      setSaving(false);
    }
  }

  const unchanged = Boolean(initial) && part.trim() === initial?.part_code && desc.trim() === initial?.description && code.trim() === initial?.cth;
  return (
    <Modal open={open} onClose={onClose} title={title} maxWidth="max-w-lg">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Input id="cth-part" label="Part Code" value={part} onChange={(e) => setPart(e.target.value)} autoFocus maxLength={100} className="font-mono" />
        <Input id="cth-desc" label="Description" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} />
        <Input id="cth-code" label="CTH (8 digits)" value={code} onChange={(e) => setCode(e.target.value)} placeholder="85177990" maxLength={20} className="font-mono" inputMode="numeric" />
        <div className="flex justify-end gap-3 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" isLoading={saving} disabled={unchanged}>{initial ? "Save changes" : "Add record"}</Button>
        </div>
      </form>
    </Modal>
  );
}
