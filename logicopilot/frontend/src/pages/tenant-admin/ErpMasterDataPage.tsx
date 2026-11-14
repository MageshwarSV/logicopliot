import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { Pagination } from "../../components/ui/Pagination";
import { DatabaseIcon, DownloadIcon, SearchIcon } from "../../components/ui/icons";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/masterData";
import type { MasterDataCategory, MasterDataOverview, MasterDataRow, MasterDataTableDef } from "../../api/masterData";
import type { ReferenceUploadConflict } from "../../api/onboarding";

const SELECT_CLASS =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";
const PAGE_SIZES = [10, 25, 50, 100];

const PAGE_TEXT: Record<MasterDataCategory, { title: string; subtitle: string }> = {
  importer: {
    title: "ERP Master Data – Importer",
    subtitle: "Importer names, branches and addresses used to fill the ERP importer fields on a job.",
  },
  quotation: {
    title: "ERP Master Data – Quotation",
    subtitle: "Quotation names, branches and addresses used to fill the ERP quotation fields on a job.",
  },
};

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Tenant Admin -> Administration -> Master Data -> ERP Master Data (Importer / Quotation).
 *  Shows the existing reference tables ("dumps") per template, one tab per table, paged and
 *  searched on the server. Add, edit, delete, Excel upload and export all write the same rows
 *  the job screen reads, so a change here applies to the next job. */
export function ErpMasterDataPage({ category }: { category: MasterDataCategory }) {
  const text = PAGE_TEXT[category];
  const [overview, setOverview] = useState<MasterDataOverview | null>(null);
  const [groupId, setGroupId] = useState("");
  const [tableKey, setTableKey] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [rows, setRows] = useState<MasterDataRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editRow, setEditRow] = useState<MasterDataRow | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MasterDataRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflicts, setConflicts] = useState<ReferenceUploadConflict[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  // Load the template/table list; keep the current choice if it still exists.
  const loadOverview = useCallback(async () => {
    try {
      const data = await api.getOverview(category);
      setOverview(data);
      setGroupId((g) => (data.templates.some((t) => t.group_id === g) ? g : data.templates[0]?.group_id ?? ""));
      setTableKey((k) => (data.tables.some((t) => t.key === k) ? k : data.tables[0]?.key ?? ""));
    } catch (err) {
      setError(errorText(err, "Could not load the master data."));
      setLoading(false);
    }
  }, [category]);

  useEffect(() => {
    setOverview(null);
    setGroupId("");
    setTableKey("");
    setSearch("");
    setQuery("");
    setPage(1);
    setError(null);
    loadOverview();
  }, [loadOverview]);

  // Debounce the search box so typing doesn't fire a request per keystroke.
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

  const template = overview?.templates.find((t) => t.group_id === groupId);
  const table: MasterDataTableDef | undefined = overview?.tables.find((t) => t.key === tableKey);
  const fieldId = template?.tables[tableKey]?.field_id ?? "";

  const loadRows = useCallback(async () => {
    if (!fieldId) {
      setRows([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await api.listRows(category, fieldId, { q: query || undefined, page, page_size: pageSize });
      setRows(data.rows);
      setTotal(data.total);
    } catch (err) {
      setError(errorText(err, "Could not load the rows."));
    } finally {
      setLoading(false);
    }
  }, [category, fieldId, query, page, pageSize]);

  useEffect(() => {
    if (overview) loadRows();
  }, [overview, loadRows]);

  async function refreshAll() {
    await Promise.all([loadRows(), loadOverview()]);
  }

  async function handleUpload(file: File | undefined) {
    if (fileRef.current) fileRef.current.value = "";
    if (!file || !fieldId) return;
    setError(null);
    setBusy(true);
    try {
      const r = await api.uploadRows(category, fieldId, file);
      setConflicts(r.conflicts);
      setNotice(
        `Upload done: ${r.rows_added} added, ${r.rows_unchanged} already on file, ${r.rows_skipped} skipped` +
          (r.conflicts.length ? `, ${r.conflicts.length} need a decision.` : "."),
      );
      await refreshAll();
    } catch (err) {
      setError(errorText(err, "Could not upload the file."));
    } finally {
      setBusy(false);
    }
  }

  async function download(kind: "template" | "export") {
    if (!fieldId) return;
    setError(null);
    try {
      await api.downloadWorkbook(category, fieldId, kind);
    } catch (err) {
      setError(errorText(err, "Could not download the file."));
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await api.deleteRow(category, fieldId, deleteTarget.id);
      setDeleteTarget(null);
      setNotice("Row deleted.");
      if (rows.length === 1 && page > 1) setPage(page - 1);
      await refreshAll();
    } catch (err) {
      setError(errorText(err, "Could not delete the row."));
      setDeleteTarget(null);
    } finally {
      setBusy(false);
    }
  }

  async function useUploaded(c: ReferenceUploadConflict) {
    try {
      await api.updateRow(category, fieldId, c.s_no, c.uploaded_value);
      setConflicts((list) => list.filter((x) => x.s_no !== c.s_no));
      await loadRows();
    } catch (err) {
      setError(errorText(err, "Could not update the row."));
    }
  }

  const columns: Column<MasterDataRow>[] = useMemo(() => {
    if (!table) return [];
    const offset = (page - 1) * pageSize;
    const indexOf = new Map(rows.map((r, i) => [r.id, offset + i + 1]));
    return [
      { header: "S.No", className: "w-14 text-slate-500", render: (r) => indexOf.get(r.id) },
      ...table.columns.map((col, i) => ({
        header: col,
        render: (r: MasterDataRow) => <span className="break-words">{r.match_values[i] || "—"}</span>,
      })),
      {
        header: table.value,
        render: (r) => <span className="font-medium text-slate-900 dark:text-slate-100 break-words">{r.resolved_value}</span>,
      },
      {
        header: "Updated",
        className: "whitespace-nowrap text-slate-500",
        render: (r) => (r.updated_at ? formatDateTime(r.updated_at, { year: true }) : "—"),
      },
      {
        header: "",
        className: "w-32 text-right",
        render: (r) => (
          <div className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => setEditRow(r)}>Edit</Button>
            <Button size="sm" variant="ghost" className="text-rose-600 dark:text-rose-400" onClick={() => setDeleteTarget(r)}>
              Delete
            </Button>
          </div>
        ),
      },
    ];
  }, [table, rows, page, pageSize]);

  const noTemplates = overview && overview.templates.length === 0;

  return (
    <AppShell title={text.title} subtitle={text.subtitle}>
      <div className="space-y-6">
        {error && <Alert>{error}</Alert>}
        {notice && (
          <div
            role="status"
            className="rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
          >
            {notice}
          </div>
        )}

        {noTemplates ? (
          <Card className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
            No template in your account has {category} master data yet.
          </Card>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="flex flex-wrap gap-2" role="tablist">
                {overview?.tables.map((t) => {
                  const active = t.key === tableKey;
                  const count = template?.tables[t.key]?.rows;
                  const available = Boolean(template?.tables[t.key]);
                  return (
                    <button
                      key={t.key}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      disabled={!available}
                      onClick={() => {
                        setTableKey(t.key);
                        setPage(1);
                        setConflicts([]);
                      }}
                      className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                        active
                          ? "border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-300"
                          : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                      }`}
                    >
                      <DatabaseIcon className="h-4 w-4" />
                      {t.title}
                      {count !== undefined && (
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {count.toLocaleString()}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                Template
                <select
                  value={groupId}
                  onChange={(e) => {
                    setGroupId(e.target.value);
                    setPage(1);
                    setConflicts([]);
                  }}
                  className={SELECT_CLASS}
                >
                  {overview?.templates.map((t) => (
                    <option key={t.group_id} value={t.group_id}>{t.name}</option>
                  ))}
                </select>
              </label>
            </div>

            <Card>
              <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-4 lg:flex-row lg:items-center lg:justify-between dark:border-slate-800">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">{table?.title ?? "—"}</h2>
                  <p className="text-sm text-slate-500 dark:text-slate-400">
                    {total.toLocaleString()} {query ? "matching " : ""}{total === 1 ? "record" : "records"}
                    {table ? ` · ${table.description}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative">
                    <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search any column..."
                      className={`${SELECT_CLASS} w-64 pl-9`}
                    />
                  </div>
                  <Button size="sm" variant="secondary" disabled={!fieldId} onClick={() => download("export")}>
                    <DownloadIcon className="h-4 w-4" /> Export
                  </Button>
                  <Button size="sm" variant="secondary" disabled={!fieldId} onClick={() => download("template")}>
                    Template
                  </Button>
                  <Button size="sm" variant="secondary" disabled={!fieldId} isLoading={busy && !deleteTarget} onClick={() => fileRef.current?.click()}>
                    Upload Excel
                  </Button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".xlsx,.xlsm"
                    className="hidden"
                    onChange={(e) => handleUpload(e.target.files?.[0])}
                  />
                  <Button size="sm" disabled={!fieldId} onClick={() => setAddOpen(true)}>
                    + Add Record
                  </Button>
                </div>
              </div>

              {conflicts.length > 0 && table && (
                <ConflictList
                  table={table}
                  conflicts={conflicts}
                  onUseUploaded={useUploaded}
                  onKeep={(c) => setConflicts((list) => list.filter((x) => x.s_no !== c.s_no))}
                />
              )}

              {loading ? (
                <p className="py-16 text-center text-sm text-slate-500 dark:text-slate-400">Loading…</p>
              ) : (
                <DataTable
                  columns={columns}
                  rows={rows}
                  keyFor={(r) => String(r.id)}
                  emptyMessage={query ? "No records match this search." : "No records yet. Use \"+ Add Record\" or upload an Excel file."}
                />
              )}

              {!loading && total > 0 && (
                <div className="px-5 pb-4">
                  <Pagination
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={setPage}
                    noun="records"
                    extra={
                      <label className="mr-3 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                        Rows per page
                        <select
                          value={pageSize}
                          onChange={(e) => {
                            setPageSize(Number(e.target.value));
                            setPage(1);
                          }}
                          className={`${SELECT_CLASS} py-1`}
                        >
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

      {table && (
        <AddRecordModal
          open={addOpen}
          table={table}
          onClose={() => setAddOpen(false)}
          onSave={async (matchValues, value) => {
            const r = await api.addRows(category, fieldId, [{ match_values: matchValues, resolved_value: value }]);
            if (r.conflicts.length) {
              const c = r.conflicts[0];
              throw new Error(`This record already exists with ${table.value} "${c.existing_value}". Edit that row instead.`);
            }
            if (!r.rows_added) throw new Error("This record is already on file.");
            setAddOpen(false);
            setNotice("Record added.");
            await refreshAll();
          }}
        />
      )}

      {table && editRow && (
        <EditRecordModal
          row={editRow}
          table={table}
          onClose={() => setEditRow(null)}
          onSave={async (value) => {
            await api.updateRow(category, fieldId, editRow.id, value);
            setEditRow(null);
            setNotice("Record updated.");
            await loadRows();
          }}
        />
      )}

      <Modal open={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Delete record?">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          <span className="font-medium text-slate-900 dark:text-slate-100">{deleteTarget?.resolved_value}</span> will no
          longer be suggested on jobs for{" "}
          {deleteTarget?.match_values.filter(Boolean).join(" · ") || "this key"}.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button variant="danger" isLoading={busy} onClick={confirmDelete}>Delete</Button>
        </div>
      </Modal>
    </AppShell>
  );
}

function ConflictList({
  table,
  conflicts,
  onUseUploaded,
  onKeep,
}: {
  table: MasterDataTableDef;
  conflicts: ReferenceUploadConflict[];
  onUseUploaded: (c: ReferenceUploadConflict) => void;
  onKeep: (c: ReferenceUploadConflict) => void;
}) {
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-5 py-4 dark:border-amber-500/20 dark:bg-amber-500/10">
      <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
        {conflicts.length} uploaded {conflicts.length === 1 ? "row differs" : "rows differ"} from what is on file. Nothing
        was overwritten; choose which {table.value} to keep.
      </p>
      <ul className="mt-3 max-h-72 space-y-2 overflow-y-auto">
        {conflicts.map((c) => (
          <li
            key={c.s_no}
            className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-white p-3 text-sm sm:flex-row sm:items-center sm:justify-between dark:border-amber-500/20 dark:bg-slate-900"
          >
            <div className="min-w-0">
              <p className="truncate text-slate-500 dark:text-slate-400">
                {[c.match_value_1, c.match_value_2, c.match_value_3, c.match_value_4].filter(Boolean).join(" · ")}
              </p>
              <p className="text-slate-700 dark:text-slate-200">
                On file: <span className="font-medium">{c.existing_value}</span> → Uploaded:{" "}
                <span className="font-medium">{c.uploaded_value}</span>
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button size="sm" variant="ghost" onClick={() => onKeep(c)}>Keep current</Button>
              <Button size="sm" variant="secondary" onClick={() => onUseUploaded(c)}>Use uploaded</Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AddRecordModal({
  open,
  table,
  onClose,
  onSave,
}: {
  open: boolean;
  table: MasterDataTableDef;
  onClose: () => void;
  onSave: (matchValues: string[], value: string) => Promise<void>;
}) {
  const [keys, setKeys] = useState<string[]>([]);
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setKeys(table.columns.map(() => ""));
      setValue("");
      setError(null);
    }
  }, [open, table]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (keys.some((k) => !k.trim()) || !value.trim()) {
      setError("Fill in every field.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(keys.map((k) => k.trim()), value.trim());
    } catch (err) {
      setError(err instanceof Error && !axios.isAxiosError(err) ? err.message : errorText(err, "Could not add the record."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`Add ${table.title} record`} maxWidth="max-w-lg">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        {table.columns.map((col, i) => (
          <Input
            key={col}
            id={`add-${table.key}-${i}`}
            label={col}
            value={keys[i] ?? ""}
            onChange={(e) => setKeys((k) => k.map((v, j) => (j === i ? e.target.value : v)))}
            autoFocus={i === 0}
          />
        ))}
        <Input id={`add-${table.key}-value`} label={table.value} value={value} onChange={(e) => setValue(e.target.value)} />
        <div className="flex justify-end gap-3 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" isLoading={saving}>Add record</Button>
        </div>
      </form>
    </Modal>
  );
}

function EditRecordModal({
  row,
  table,
  onClose,
  onSave,
}: {
  row: MasterDataRow;
  table: MasterDataTableDef;
  onClose: () => void;
  onSave: (value: string) => Promise<void>;
}) {
  const [value, setValue] = useState(row.resolved_value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) {
      setError(`${table.value} cannot be empty.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(value.trim());
    } catch (err) {
      setError(errorText(err, "Could not save the record."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Edit ${table.title} record`} maxWidth="max-w-lg">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <dl className="grid grid-cols-1 gap-3 rounded-lg bg-slate-50 p-3 text-sm sm:grid-cols-2 dark:bg-slate-800/50">
          {table.columns.map((col, i) => (
            <div key={col}>
              <dt className="text-xs text-slate-500 dark:text-slate-400">{col}</dt>
              <dd className="font-medium text-slate-800 dark:text-slate-100 break-words">{row.match_values[i] || "—"}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          To change the fields above, delete this record and add a new one.
        </p>
        <Input id={`edit-${table.key}-value`} label={table.value} value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        <div className="flex justify-end gap-3 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" isLoading={saving} disabled={value.trim() === row.resolved_value}>Save changes</Button>
        </div>
      </form>
    </Modal>
  );
}
