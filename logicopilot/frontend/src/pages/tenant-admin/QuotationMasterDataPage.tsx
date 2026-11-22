import { useCallback, useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { Pagination } from "../../components/ui/Pagination";
import { DatabaseIcon, DownloadIcon, LayersIcon, MapPinIcon, PencilIcon, PlusIcon, SearchIcon } from "../../components/ui/icons";
import { FIELD_CLASS, OUTLINE_BTN, SortHeader, StatusToggle } from "../../components/tenant-admin/masterDataUi";
import * as api from "../../api/masterData";
import type { QuotationRow, QuotationTemplate } from "../../api/masterData";

const PAGE_SIZES = [10, 25, 50, 100];
type SortKey = "name" | "branch";

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Tenant Admin -> Master Data -> ERP Master Data - Quotation. One row per ERP quotation name
 *  and branch, with the address the job screen shows. Template is the only filter; search
 *  matches the quotation name only. Records are never deleted, only made Inactive. */
export function QuotationMasterDataPage() {
  const [templates, setTemplates] = useState<QuotationTemplate[] | null>(null);
  const [groupId, setGroupId] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "name", dir: "asc" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [rows, setRows] = useState<QuotationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editRow, setEditRow] = useState<QuotationRow | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<QuotationRow | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  const loadTemplates = useCallback(async () => {
    try {
      const list = await api.listQuotationTemplates();
      setTemplates(list);
      setGroupId((g) => (list.some((t) => t.group_id === g) ? g : list[0]?.group_id ?? ""));
    } catch (err) {
      setError(errorText(err, "Could not load the quotation master data."));
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

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
    if (!groupId) {
      setRows([]);
      setTotal(0);
      setLoading(templates === null);
      return;
    }
    setLoading(true);
    try {
      const data = await api.listQuotationRows(groupId, {
        q: query || undefined, sort: sort.key, direction: sort.dir, page, page_size: pageSize,
      });
      setRows(data.rows);
      setTotal(data.total);
    } catch (err) {
      setError(errorText(err, "Could not load the records."));
    } finally {
      setLoading(false);
    }
  }, [groupId, query, sort, page, pageSize, templates]);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
    setPage(1);
  }

  async function exportRows() {
    if (!groupId) return;
    setError(null);
    try {
      await api.downloadQuotationExport(groupId);
    } catch (err) {
      setError(errorText(err, "Could not download the file."));
    }
  }

  async function setActive(row: QuotationRow, active: boolean) {
    setTogglingId(row.id);
    setError(null);
    try {
      const updated = await api.setQuotationRowActive(groupId, row.id, active);
      setRows((list) => list.map((r) => (r.id === updated.id ? updated : r)));
      setNotice(active ? "Record is Active - offered on jobs again." : "Record is Inactive - no longer offered on jobs.");
      setDeactivateTarget(null);
    } catch (err) {
      setError(errorText(err, "Could not change the status."));
    } finally {
      setTogglingId(null);
    }
  }

  const offset = (page - 1) * pageSize;

  return (
    <AppShell
      title="ERP Master Data – Quotation"
      subtitle="Manage quotation names, branches and addresses used to fill the ERP quotation fields on a job."
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
            No template in your account has quotation master data yet.
          </Card>
        ) : (
          <>
            <Card className="overflow-hidden">
              <div className="flex flex-col gap-4 px-6 py-5 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex items-center gap-3">
                  <h2 className="text-2xl font-semibold text-slate-900 dark:text-slate-50">Quotation Master</h2>
                  <span className="rounded-lg bg-indigo-50 px-2.5 py-1 text-sm font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
                    {total.toLocaleString()} {query ? "matching" : ""} {total === 1 ? "Record" : "Records"}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2 text-sm font-medium text-slate-600 dark:text-slate-300">
                    <LayersIcon className="h-5 w-5 text-slate-500" />
                    <span className="sr-only">Template</span>
                    <select
                      value={groupId}
                      onChange={(e) => { setGroupId(e.target.value); setPage(1); }}
                      className={`${FIELD_CLASS} w-60`}
                      aria-label="Template"
                      title="Template"
                    >
                      {templates?.map((t) => <option key={t.group_id} value={t.group_id}>{t.name}</option>)}
                    </select>
                  </label>
                  <div className="relative">
                    <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search by ERP Quotation Name..."
                      className={`${FIELD_CLASS} w-80 pl-9`}
                    />
                  </div>
                  <button type="button" className={OUTLINE_BTN} disabled={!groupId} onClick={exportRows}>
                    <DownloadIcon className="h-4 w-4" /> Export
                  </button>
                  <Button size="sm" className="px-4 py-2" disabled={!groupId} onClick={() => setAddOpen(true)}>
                    <PlusIcon className="h-4 w-4" /> Add Record
                  </Button>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="bg-indigo-50/60 text-slate-800 dark:bg-slate-800/60 dark:text-slate-200">
                      <th className="w-16 px-6 py-3 font-semibold">S.No</th>
                      <SortHeader label="ERP Quotation Name" active={sort.key === "name"} dir={sort.dir} onClick={() => toggleSort("name")} />
                      <SortHeader label="Branch" active={sort.key === "branch"} dir={sort.dir} onClick={() => toggleSort("branch")} />
                      <th className="px-4 py-3 font-semibold">Branch Address</th>
                      <th className="w-36 px-4 py-3 font-semibold">Status</th>
                      <th className="w-20 px-6 py-3"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {loading ? (
                      <tr><td colSpan={6} className="py-16 text-center text-slate-500 dark:text-slate-400">Loading…</td></tr>
                    ) : rows.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="py-16 text-center text-slate-500 dark:text-slate-400">
                          {query ? "No records match this search." : "No quotation records for this template yet."}
                        </td>
                      </tr>
                    ) : (
                      rows.map((r, i) => (
                        <tr
                          key={r.id}
                          className={`align-middle hover:bg-slate-50/60 dark:hover:bg-slate-800/30 ${r.is_active ? "" : "bg-slate-50/70 dark:bg-slate-800/20 [&>td:not(:nth-last-child(-n+2))]:opacity-55"}`}
                        >
                          <td className="px-6 py-4 text-slate-500 dark:text-slate-400">{offset + i + 1}</td>
                          <td className="max-w-xs px-4 py-4 font-semibold text-slate-900 dark:text-slate-100">{r.name}</td>
                          <td className="px-4 py-4 font-semibold text-slate-900 dark:text-slate-100">{r.branch}</td>
                          <td className="px-4 py-4">
                            <div className="flex items-start gap-3">
                              <MapPinIcon className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
                              <span className="max-w-xl text-slate-700 dark:text-slate-300">{r.address}</span>
                            </div>
                          </td>
                          <td className="px-4 py-4">
                            <StatusToggle
                              active={r.is_active}
                              busy={togglingId === r.id}
                              label={`${r.name} ${r.branch}`}
                              onChange={(next) => (next ? setActive(r, true) : setDeactivateTarget(r))}
                            />
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex justify-end">
                              <button
                                type="button"
                                title="Edit address"
                                aria-label={`Edit ${r.name} ${r.branch}`}
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

      <QuotationFormModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSave={async (v) => {
          await api.addQuotationRow(groupId, v);
          setAddOpen(false);
          setNotice("Record added. It is now available on jobs.");
          await Promise.all([loadRows(), loadTemplates()]);
        }}
      />

      {editRow && (
        <QuotationFormModal
          open
          row={editRow}
          onClose={() => setEditRow(null)}
          onSave={async (v) => {
            await api.updateQuotationRow(groupId, editRow.id, v.address);
            setEditRow(null);
            setNotice("Record updated.");
            await loadRows();
          }}
        />
      )}

      <Modal open={deactivateTarget !== null} onClose={() => setDeactivateTarget(null)} title="Make record Inactive?">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          <span className="font-medium text-slate-900 dark:text-slate-100">{deactivateTarget?.name}</span>
          {" – "}{deactivateTarget?.branch} will no longer be offered on jobs. The record is kept, and you can make it
          Active again at any time.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setDeactivateTarget(null)}>Cancel</Button>
          <Button
            variant="danger"
            isLoading={deactivateTarget !== null && togglingId === deactivateTarget.id}
            onClick={() => deactivateTarget && setActive(deactivateTarget, false)}
          >
            Make Inactive
          </Button>
        </div>
      </Modal>
    </AppShell>
  );
}

/** Add (no `row`) or edit (`row` given). Editing changes only the address - the name and
 *  branch identify the record; make it Inactive and add a new one to change those. */
function QuotationFormModal({
  open, row, onClose, onSave,
}: {
  open: boolean;
  row?: QuotationRow;
  onClose: () => void;
  onSave: (v: { name: string; branch: string; address: string }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [address, setAddress] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = Boolean(row);

  useEffect(() => {
    if (!open) return;
    setName(row?.name ?? "");
    setBranch(row?.branch ?? "");
    setAddress(row?.address ?? "");
    setError(null);
  }, [open, row]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !branch.trim() || !address.trim()) {
      setError("ERP Quotation Name, Quotation Branch and Quotation Address are required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({ name: name.trim(), branch: branch.trim(), address: address.trim() });
    } catch (err) {
      setError(errorText(err, "Could not save the record."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={editing ? "Edit quotation record" : "Add quotation record"} maxWidth="max-w-lg">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Input id="quo-name" label="ERP Quotation Name" value={name} onChange={(e) => setName(e.target.value)} disabled={editing} autoFocus={!editing} maxLength={255} />
        <Input id="quo-branch" label="Quotation Branch" value={branch} onChange={(e) => setBranch(e.target.value)} disabled={editing} maxLength={255} />
        {editing && <p className="-mt-2 text-xs text-slate-500 dark:text-slate-400">To change the name or branch, make this record Inactive and add a new one.</p>}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="quo-address" className="text-sm font-medium text-slate-700 dark:text-slate-300">Quotation Address</label>
          <textarea
            id="quo-address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            rows={3}
            autoFocus={editing}
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
