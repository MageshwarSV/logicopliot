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
  DatabaseIcon, DocumentIcon, DownloadIcon, GlobeIcon, LayersIcon, MapPinIcon, PencilIcon, PlusIcon, SearchIcon,
  SortIcon, TrashIcon, UploadIcon,
} from "../../components/ui/icons";
import * as api from "../../api/masterData";
import type { ImporterRow, ImporterTemplate, ImporterUploadConflict, ImporterUploadResult } from "../../api/masterData";
import { countryFlag, countryLabel } from "../../utils/countries";

const PAGE_SIZES = [10, 25, 50, 100];
const DEFAULT_COUNTRY = "INDIA";
const FIELD_CLASS =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";
const OUTLINE_BTN =
  "inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800";

type SortKey = "name" | "branch";

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Tenant Admin -> Master Data -> ERP Master Data - Importer. One row per importer branch for
 *  the chosen country and template: ERP importer name, branch with its GSTIN, and the branch
 *  address the job screen fills in. Paged, searched and sorted on the server. */
export function ImporterMasterDataPage() {
  const [templates, setTemplates] = useState<ImporterTemplate[] | null>(null);
  const [groupId, setGroupId] = useState("");
  const [country, setCountry] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "name", dir: "asc" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [rows, setRows] = useState<ImporterRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [upload, setUpload] = useState<ImporterUploadResult | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editRow, setEditRow] = useState<ImporterRow | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ImporterRow | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadTemplates = useCallback(async () => {
    try {
      const list = await api.listImporterTemplates();
      setTemplates(list);
      setGroupId((g) => (list.some((t) => t.group_id === g) ? g : list[0]?.group_id ?? ""));
    } catch (err) {
      setError(errorText(err, "Could not load the importer master data."));
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  const template = templates?.find((t) => t.group_id === groupId);
  const countries = template?.countries ?? [];

  // Keep the chosen country when switching template if it exists there; otherwise India,
  // otherwise the biggest one.
  useEffect(() => {
    if (!template) return;
    setCountry((c) =>
      template.countries.some((x) => x.country === c)
        ? c
        : template.countries.find((x) => x.country === DEFAULT_COUNTRY)?.country ?? template.countries[0]?.country ?? "",
    );
  }, [template]);

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
    const t = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(t);
  }, [notice]);

  const loadRows = useCallback(async () => {
    if (!groupId || !country) {
      setRows([]);
      setTotal(0);
      setLoading(templates === null);
      return;
    }
    setLoading(true);
    try {
      const data = await api.listImporterRows(groupId, {
        country, q: query || undefined, sort: sort.key, direction: sort.dir, page, page_size: pageSize,
      });
      setRows(data.rows);
      setTotal(data.total);
    } catch (err) {
      setError(errorText(err, "Could not load the records."));
    } finally {
      setLoading(false);
    }
  }, [groupId, country, query, sort, page, pageSize, templates]);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  async function refreshAll() {
    await Promise.all([loadRows(), loadTemplates()]);
  }

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
    setPage(1);
  }

  async function handleUpload(file: File | undefined) {
    if (fileRef.current) fileRef.current.value = "";
    if (!file || !groupId) return;
    setError(null);
    setUploading(true);
    try {
      const r = await api.uploadImporterRows(groupId, file);
      setUpload(r);
      await refreshAll();
    } catch (err) {
      setError(errorText(err, "Could not upload the file."));
    } finally {
      setUploading(false);
    }
  }

  async function download(kind: "template" | "export") {
    if (!groupId) return;
    setError(null);
    try {
      await api.downloadImporterWorkbook(groupId, kind, country);
    } catch (err) {
      setError(errorText(err, "Could not download the file."));
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.deleteImporterRow(groupId, deleteTarget.id);
      setDeleteTarget(null);
      setNotice("Record deleted.");
      if (rows.length === 1 && page > 1) setPage(page - 1);
      await refreshAll();
    } catch (err) {
      setError(errorText(err, "Could not delete the record."));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  async function resolveConflict(c: ImporterUploadConflict, useUploaded: boolean) {
    if (useUploaded) {
      try {
        await api.updateImporterRow(groupId, c.id, { [c.field]: c.uploaded_value });
        await loadRows();
      } catch (err) {
        setError(errorText(err, "Could not update the record."));
        return;
      }
    }
    setUpload((u) => u && {
      ...u,
      conflicts: u.conflicts.filter((x) => !(x.id === c.id && x.field === c.field)),
      conflict_count: u.conflict_count - 1,
    });
  }

  const offset = (page - 1) * pageSize;
  const flag = countryFlag(country);

  return (
    <AppShell
      title="ERP Master Data – Importer"
      subtitle="Manage importer names, branches and addresses used to fill the ERP importer fields on a job."
      icon={<DatabaseIcon className="h-6 w-6" />}
    >
      <div className="space-y-5">
        {error && <Alert>{error}</Alert>}
        {notice && (
          <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
            {notice}
          </div>
        )}

        {templates && templates.length === 0 ? (
          <Card className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
            No template in your account has importer master data yet.
          </Card>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-2xl border border-indigo-100 bg-indigo-50/60 px-5 py-4 dark:border-indigo-500/20 dark:bg-indigo-500/5">
              <label className="flex items-center gap-3 text-sm font-medium text-slate-700 dark:text-slate-200">
                <GlobeIcon className="h-5 w-5 text-slate-500" />
                <span>Country<span className="text-rose-500">*</span></span>
                <span className="relative">
                  {flag && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-lg leading-none">{flag}</span>}
                  <select
                    value={country}
                    onChange={(e) => { setCountry(e.target.value); setPage(1); setUpload(null); }}
                    className={`${FIELD_CLASS} w-64 py-2.5 ${flag ? "pl-10" : ""}`}
                    required
                  >
                    {countries.map((c) => (
                      <option key={c.country} value={c.country}>
                        {countryLabel(c.country)} ({c.rows.toLocaleString()})
                      </option>
                    ))}
                  </select>
                </span>
              </label>
              <span className="hidden h-8 w-px bg-indigo-200 sm:block dark:bg-indigo-500/20" />
              <label className="flex items-center gap-3 text-sm font-medium text-slate-700 dark:text-slate-200">
                <LayersIcon className="h-5 w-5 text-slate-500" />
                Template
                <select
                  value={groupId}
                  onChange={(e) => { setGroupId(e.target.value); setPage(1); setUpload(null); }}
                  className={`${FIELD_CLASS} w-72 py-2.5`}
                >
                  {templates?.map((t) => <option key={t.group_id} value={t.group_id}>{t.name}</option>)}
                </select>
              </label>
            </div>

            <Card className="overflow-hidden">
              <div className="flex flex-col gap-4 px-6 py-5 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex items-center gap-3">
                  <h2 className="text-2xl font-semibold text-slate-900 dark:text-slate-50">{country ? countryLabel(country) : "—"}</h2>
                  <span className="rounded-lg bg-indigo-50 px-2.5 py-1 text-sm font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
                    {total.toLocaleString()} {query ? "matching" : ""} {total === 1 ? "Record" : "Records"}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative">
                    <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search importer name, branch, GSTIN or address..."
                      className={`${FIELD_CLASS} w-80 pl-9`}
                    />
                  </div>
                  <button type="button" className={OUTLINE_BTN} disabled={!groupId} onClick={() => download("export")}>
                    <DownloadIcon className="h-4 w-4" /> Export
                  </button>
                  <button type="button" className={OUTLINE_BTN} disabled={!groupId} onClick={() => download("template")}>
                    <DocumentIcon className="h-4 w-4" /> Template
                  </button>
                  <button type="button" className={OUTLINE_BTN} disabled={!groupId || uploading} onClick={() => fileRef.current?.click()}>
                    <UploadIcon className="h-4 w-4" /> {uploading ? "Uploading…" : "Upload Excel"}
                  </button>
                  <input ref={fileRef} type="file" accept=".xlsx,.xlsm" className="hidden" onChange={(e) => handleUpload(e.target.files?.[0])} />
                  <Button size="sm" className="px-4 py-2" disabled={!groupId || !country} onClick={() => setAddOpen(true)}>
                    <PlusIcon className="h-4 w-4" /> Add Record
                  </Button>
                </div>
              </div>

              {upload && <UploadSummary result={upload} onResolve={resolveConflict} onClose={() => setUpload(null)} />}

              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="bg-slate-50 text-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
                      <th className="w-16 px-6 py-3 font-semibold">S.No</th>
                      <SortHeader label="ERP Importer Name" active={sort.key === "name"} dir={sort.dir} onClick={() => toggleSort("name")} />
                      <SortHeader label="Branch / GSTIN" active={sort.key === "branch"} dir={sort.dir} onClick={() => toggleSort("branch")} />
                      <th className="px-4 py-3 font-semibold">Branch Address</th>
                      <th className="w-28 px-6 py-3"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {loading ? (
                      <tr><td colSpan={5} className="py-16 text-center text-slate-500 dark:text-slate-400">Loading…</td></tr>
                    ) : rows.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="py-16 text-center text-slate-500 dark:text-slate-400">
                          {query ? "No records match this search." : "No records for this country yet."}
                        </td>
                      </tr>
                    ) : (
                      rows.map((r, i) => (
                        <tr key={r.id} className="align-middle hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                          <td className="px-6 py-4 text-slate-500 dark:text-slate-400">{offset + i + 1}</td>
                          <td className="max-w-xs px-4 py-4 font-semibold text-slate-900 dark:text-slate-100">{r.name}</td>
                          <td className="px-4 py-4">
                            <div className="font-semibold text-slate-900 dark:text-slate-100">{r.branch}</div>
                            <div className={`text-sm ${r.gstin ? "font-mono text-slate-500 dark:text-slate-400" : "text-slate-400 dark:text-slate-500"}`}>
                              {r.gstin || "GSTIN not available"}
                            </div>
                          </td>
                          <td className="px-4 py-4">
                            <div className="flex items-start gap-3">
                              <MapPinIcon className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                              <span className="max-w-xl text-slate-700 dark:text-slate-300">{r.address}</span>
                            </div>
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex justify-end gap-2">
                              <button
                                type="button"
                                title="Edit"
                                aria-label={`Edit ${r.name} ${r.branch}`}
                                onClick={() => setEditRow(r)}
                                className="rounded-lg border border-slate-200 p-2 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                              >
                                <PencilIcon className="h-4 w-4" />
                              </button>
                              <button
                                type="button"
                                title="Delete"
                                aria-label={`Delete ${r.name} ${r.branch}`}
                                onClick={() => setDeleteTarget(r)}
                                className="rounded-lg border border-rose-200 p-2 text-rose-600 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-400 dark:hover:bg-rose-500/10"
                              >
                                <TrashIcon className="h-4 w-4" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              {!loading && total > 0 && (
                <div className="px-6 pb-4">
                  <Pagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
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
          </>
        )}
      </div>

      <ImporterFormModal
        open={addOpen}
        country={country}
        onClose={() => setAddOpen(false)}
        onSave={async (v) => {
          await api.addImporterRow(groupId, v);
          setAddOpen(false);
          setNotice("Record added. It is now available on jobs.");
          await refreshAll();
        }}
      />

      {editRow && (
        <ImporterFormModal
          open
          country={editRow.country}
          row={editRow}
          onClose={() => setEditRow(null)}
          onSave={async (v) => {
            await api.updateImporterRow(groupId, editRow.id, { address: v.address, gstin: v.gstin });
            setEditRow(null);
            setNotice("Record updated.");
            await loadRows();
          }}
        />
      )}

      <Modal open={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Delete record?">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          <span className="font-medium text-slate-900 dark:text-slate-100">{deleteTarget?.name}</span>
          {" – "}{deleteTarget?.branch} ({deleteTarget ? countryLabel(deleteTarget.country) : ""}) will be removed and no
          longer offered on jobs.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button variant="danger" isLoading={deleting} onClick={confirmDelete}>Delete</Button>
        </div>
      </Modal>
    </AppShell>
  );
}

function SortHeader({ label, active, dir, onClick }: { label: string; active: boolean; dir: "asc" | "desc"; onClick: () => void }) {
  return (
    <th className="px-4 py-3 font-semibold" aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={onClick} className="inline-flex items-center gap-1.5 hover:text-indigo-600 dark:hover:text-indigo-400">
        {label}
        <SortIcon className={`h-4 w-4 ${active ? "text-indigo-600 dark:text-indigo-400" : "text-slate-400"}`} direction={active ? dir : undefined} />
      </button>
    </th>
  );
}

function UploadSummary({
  result, onResolve, onClose,
}: {
  result: ImporterUploadResult;
  onResolve: (c: ImporterUploadConflict, useUploaded: boolean) => void;
  onClose: () => void;
}) {
  return (
    <div className="border-y border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-800 dark:bg-slate-800/40">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-slate-700 dark:text-slate-200">
          Upload done: <b>{result.rows_added.toLocaleString()}</b> added, <b>{result.gstin_filled.toLocaleString()}</b> GSTIN
          filled in, {result.rows_unchanged.toLocaleString()} already on file, {result.rows_skipped.toLocaleString()} skipped
          (missing country, name, branch or address).
          {result.conflict_count > 0 && <> <b className="text-amber-700 dark:text-amber-300">{result.conflict_count.toLocaleString()}</b> differ from what is on file — nothing was overwritten.</>}
        </p>
        <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
      </div>
      {result.conflicts.length > 0 && (
        <ul className="mt-3 max-h-72 space-y-2 overflow-y-auto">
          {result.conflicts.map((c) => (
            <li key={`${c.id}-${c.field}`} className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-white p-3 text-sm sm:flex-row sm:items-center sm:justify-between dark:border-amber-500/20 dark:bg-slate-900">
              <div className="min-w-0">
                <p className="truncate text-slate-500 dark:text-slate-400">
                  {c.name} · {c.branch} · {countryLabel(c.country)} — {c.field === "gstin" ? "GSTIN" : "Address"}
                </p>
                <p className="text-slate-700 dark:text-slate-200">
                  On file: <span className="font-medium">{c.existing_value}</span> → Uploaded: <span className="font-medium">{c.uploaded_value}</span>
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button size="sm" variant="ghost" onClick={() => onResolve(c, false)}>Keep current</Button>
                <Button size="sm" variant="secondary" onClick={() => onResolve(c, true)}>Use uploaded</Button>
              </div>
            </li>
          ))}
          {result.conflict_count > result.conflicts.length && (
            <li className="text-xs text-slate-500">Showing the first {result.conflicts.length} of {result.conflict_count.toLocaleString()}.</li>
          )}
        </ul>
      )}
    </div>
  );
}

/** Add (no `row`) or edit (`row` given). Editing changes only the GSTIN and address - the
 *  name, branch and country identify the record; delete and add again to change those. */
function ImporterFormModal({
  open, country, row, onClose, onSave,
}: {
  open: boolean;
  country: string;
  row?: ImporterRow;
  onClose: () => void;
  onSave: (v: { country: string; name: string; branch: string; gstin: string; address: string }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [gstin, setGstin] = useState("");
  const [address, setAddress] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = Boolean(row);

  useEffect(() => {
    if (!open) return;
    setName(row?.name ?? "");
    setBranch(row?.branch ?? "");
    setGstin(row?.gstin ?? "");
    setAddress(row?.address ?? "");
    setError(null);
  }, [open, row]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !branch.trim() || !address.trim()) {
      setError("ERP Importer Name, Branch and Branch Address are required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({ country, name: name.trim(), branch: branch.trim(), gstin: gstin.trim(), address: address.trim() });
    } catch (err) {
      setError(errorText(err, "Could not save the record."));
    } finally {
      setSaving(false);
    }
  }

  const flag = countryFlag(country);
  return (
    <Modal open={open} onClose={onClose} title={editing ? "Edit importer record" : "Add importer record"} maxWidth="max-w-lg">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600 dark:bg-slate-800/50 dark:text-slate-300">
          <GlobeIcon className="h-4 w-4" /> Country: <span className="font-medium text-slate-900 dark:text-slate-100">{flag} {countryLabel(country)}</span>
        </div>
        <Input id="imp-name" label="ERP Importer Name" value={name} onChange={(e) => setName(e.target.value)} disabled={editing} autoFocus={!editing} maxLength={255} />
        <Input id="imp-branch" label="Branch" value={branch} onChange={(e) => setBranch(e.target.value)} disabled={editing} maxLength={255} />
        {editing && <p className="-mt-2 text-xs text-slate-500 dark:text-slate-400">To change the name or branch, delete this record and add a new one.</p>}
        <Input id="imp-gstin" label="GSTIN (optional)" value={gstin} onChange={(e) => setGstin(e.target.value.toUpperCase())} placeholder="33AABCT3125D1ZZ" maxLength={20} autoFocus={editing} className="font-mono" />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="imp-address" className="text-sm font-medium text-slate-700 dark:text-slate-300">Branch Address</label>
          <textarea
            id="imp-address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            rows={3}
            className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          />
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" isLoading={saving}>{editing ? "Save changes" : "Add record"}</Button>
        </div>
      </form>
    </Modal>
  );
}
