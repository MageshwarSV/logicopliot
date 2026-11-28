import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { DataTable, type Column } from "../../components/ui/DataTable";
import * as onboardingApi from "../../api/onboarding";
import * as tenantsApi from "../../api/tenants";
import { getSystemSettings, type SystemSettings } from "../../api/systemSettings";
import type { TemplateGroup } from "../../types/onboarding";
import type { Tenant } from "../../types/tenant";

const STATUS_STYLE: Record<string, string> = {
  draft: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  ready: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300",
  approved: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  changes_requested: "bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
};

export function TemplateListPage() {
  const navigate = useNavigate();
  const [groups, setGroups] = useState<TemplateGroup[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TemplateGroup | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [copyTarget, setCopyTarget] = useState<TemplateGroup | null>(null);
  const [copyName, setCopyName] = useState("");
  const [copyDestTenantId, setCopyDestTenantId] = useState("");
  const [copying, setCopying] = useState(false);
  const [tenantFilter, setTenantFilter] = useState("");
  const [renameTarget, setRenameTarget] = useState<TemplateGroup | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  async function load() {
    try {
      const [g, t] = await Promise.all([onboardingApi.listGroups(), tenantsApi.listTenants()]);
      setGroups(g);
      setTenants(t);
    } catch {
      setError("Failed to load templates. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  const [models, setModels] = useState<SystemSettings["ai_models"] | null>(null);

  useEffect(() => {
    load();
    // Shown, never required: the list still works if this one call fails.
    getSystemSettings().then((s) => setModels(s.ai_models ?? null)).catch(() => setModels(null));
  }, []);

  const tenantName = useMemo(() => new Map(tenants.map((t) => [t.id, t.name])), [tenants]);

  const filteredGroups = useMemo(
    () => (tenantFilter ? groups.filter((g) => g.tenant_id === tenantFilter) : groups),
    [groups, tenantFilter],
  );

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await onboardingApi.deleteGroup(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch {
      setError("Could not delete that template.");
    } finally {
      setDeleting(false);
    }
  }

  async function confirmCopy() {
    if (!copyTarget) return;
    setCopying(true);
    setError(null);
    try {
      // Only sent when it actually differs from the source - same wording the backend uses
      // to decide "same tenant" (its own only behavior before this picker existed) vs. a real
      // cross-tenant copy, so leaving the picker on the default tenant changes nothing.
      const destTenantId = copyDestTenantId && copyDestTenantId !== copyTarget.tenant_id
        ? copyDestTenantId
        : undefined;
      const copy = await onboardingApi.duplicateGroup(copyTarget.id, copyName.trim(), destTenantId);
      setCopyTarget(null);
      await load();
      // Straight into the copy: the only reason to make one is to change something in it.
      navigate(`/super-admin/template-creation?edit=${copy.id}`);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not copy that template.");
    } finally {
      setCopying(false);
    }
  }

  async function confirmRename() {
    if (!renameTarget) return;
    setRenaming(true);
    setRenameError(null);
    try {
      await onboardingApi.renameGroup(renameTarget.id, renameName.trim());
      setRenameTarget(null);
      await load();
    } catch (err: any) {
      setRenameError(err?.response?.data?.detail ?? "Could not rename that template.");
    } finally {
      setRenaming(false);
    }
  }

  // The naming standard, checked as you type (advice only - the server refuses just an empty
  // name or one this customer already uses).
  const renameChecks = useMemo(() => {
    const n = renameName.trim();
    const mode = renameTarget?.mode ?? "";
    const others = groups.filter((g) => renameTarget && g.tenant_id === renameTarget.tenant_id && g.id !== renameTarget.id);
    const out: { ok: boolean; text: string }[] = [];
    if (!n) return out;
    out.push(mode
      ? { ok: n.toLowerCase().startsWith(mode.toLowerCase()), text: `Starts with the shipment mode ("${mode}")` }
      : { ok: /^(air|sea|road|train)\s+(import|export)/i.test(n), text: "Starts with the shipment mode (e.g. Air Import)" });
    out.push({ ok: !/(january|february|march|april|may|june|july|august|september|october|november|december|\b20\d\d\b|\b\d{1,2}[/-]\d{1,2})/i.test(n), text: "No dates in the name" });
    out.push({ ok: !/\b(copy|test|new|final|old|temp)\b|\(excel\)/i.test(n), text: 'No "copy", "test", "new", "final" or "(excel)"' });
    const tenant = renameTarget ? tenantName.get(renameTarget.tenant_id) ?? "" : "";
    out.push({ ok: !tenant || !n.toLowerCase().includes(tenant.toLowerCase()), text: "No customer name (it already shows in the Tenant column)" });
    out.push({ ok: n.length <= 40, text: `40 characters or fewer (${n.length})` });
    out.push({ ok: !others.some((g) => g.name.trim().toLowerCase() === n.toLowerCase()), text: "Not already used by this customer" });
    return out;
  }, [renameName, renameTarget, groups, tenantName]);
  const duplicateName = renameChecks.length > 0 && !renameChecks[renameChecks.length - 1].ok;

  const columns: Column<TemplateGroup>[] = [
    { header: "Template", render: (g) => <span className="font-medium text-slate-900 dark:text-slate-100">{g.name}</span> },
    { header: "Tenant", render: (g) => tenantName.get(g.tenant_id) ?? "—" },
    {
      header: "AI model",
      render: () =>
        models ? (
          <span className="inline-flex items-center rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 font-mono text-xs text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200"
            title={models.reads_page_images ? "Reads the page images" : "Reads the OCR text"}>
            {models.document_model}
          </span>
        ) : <span className="text-xs text-slate-400">—</span>,
    },
    {
      header: "Status",
      render: (g) => (
        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[g.status] ?? STATUS_STYLE.draft}`}>
          {g.status.replace(/_/g, " ")}
        </span>
      ),
    },
    {
      header: "",
      className: "text-right",
      render: (g) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/super-admin/template-creation?edit=${g.id}`)}>
            View / Edit
          </Button>
          <Button variant="ghost" size="sm" onClick={() => { setRenameTarget(g); setRenameName(g.name); setRenameError(null); }}>
            Rename
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setCopyTarget(g);
              setCopyName(`${g.name}(excel)`);
              setCopyDestTenantId(g.tenant_id);
            }}
          >
            Duplicate
          </Button>
          <Button variant="danger" size="sm" onClick={() => setDeleteTarget(g)}>
            Delete
          </Button>
        </div>
      ),
    },
  ];

  return (
    <AppShell title="Template List" subtitle="Every template you've configured. Open one to view its crops and edit.">
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}
      {models && (
        <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100">
          <span className="font-semibold">AI model used by every template</span>
          <span>Documents (classify + extract): <b className="font-mono">{models.document_model}</b>{models.reads_page_images ? " · reads page images" : " · reads OCR text"}</span>
          <span>Text calls (computed fields, helpers): <b className="font-mono">{models.text_model}</b></span>
          <span className="text-xs text-sky-700/80 dark:text-sky-300/80">Set per server in .env (OPENAI_MODEL, VISION_ENGINE_MODEL)</span>
        </div>
      )}
      <div className="mb-3 flex justify-end">
        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          Tenant
          <select
            value={tenantFilter}
            onChange={(e) => setTenantFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
          >
            <option value="">All tenants</option>
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
      </div>
      <Card>
        {!loading && (
          <DataTable
            columns={columns}
            rows={filteredGroups}
            keyFor={(g) => g.id}
            emptyMessage={tenantFilter ? "No templates for this tenant." : "No templates yet — create one under Customer with Template Creation."}
          />
        )}
      </Card>

      <Modal open={copyTarget !== null} onClose={() => setCopyTarget(null)} title="Duplicate template?">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Copy <b>{copyTarget?.name}</b> &mdash; its documents and sample files, every field with
            its prompts, the custom fields and the cross-document links. Use this when the same
            paperwork has to reach the ERP a second way, e.g. typed field by field on one and
            imported as a workbook on the other &mdash; or when a brand-new customer's paperwork is
            close to one already trained, so their own template starts from this one instead of
            from scratch.
          </p>
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
              Name for the copy
            </label>
            <input
              value={copyName}
              onChange={(e) => setCopyName(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
              Destination tenant
            </label>
            <select
              value={copyDestTenantId}
              onChange={(e) => setCopyDestTenantId(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
            >
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.id === copyTarget?.tenant_id ? `${t.name} (same tenant)` : t.name}
                </option>
              ))}
            </select>
          </div>
          <p className="text-xs text-slate-500">
            The email address and operator that route incoming documents are <b>not</b> copied
            &mdash; set those on the copy if it should pull its own mail. Picking a{" "}
            <b>different</b> tenant also leaves behind any hardcoded values (they belong to{" "}
            {copyTarget?.name ?? "the original"}, not the new customer), the material-master
            workbook, and any ERP automation script &mdash; the new tenant's copy is otherwise
            fully independent and editing it never affects the original.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCopyTarget(null)}>Cancel</Button>
            <Button onClick={confirmCopy} isLoading={copying} disabled={!copyName.trim() || !copyDestTenantId}>
              Make the copy
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={renameTarget !== null} onClose={() => setRenameTarget(null)} title="Rename template">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Only the name changes. Jobs, fields, ERP scripts, users and email routing all refer to the template
            itself, so nothing else is touched and past jobs keep working.
          </p>
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">New name</label>
            <input
              autoFocus
              value={renameName}
              onChange={(e) => setRenameName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && renameName.trim() && !duplicateName) confirmRename(); }}
              maxLength={200}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 dark:border-slate-600 dark:bg-slate-900"
            />
            {renameTarget?.mode && !renameName.trim().toLowerCase().startsWith(renameTarget.mode.toLowerCase()) && (
              <button type="button" onClick={() => setRenameName(renameTarget.mode ?? "")}
                className="mt-1 text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400">
                Use "{renameTarget.mode}"
              </button>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300">
            <p className="mb-1.5 font-semibold text-slate-800 dark:text-slate-100">Naming standard</p>
            <p className="mb-1"><b>Shipment mode</b> first, then <b>– what makes it different</b> only if the customer has more than one of that mode.</p>
            <ul className="mb-2 list-disc space-y-0.5 pl-4">
              <li><span className="font-mono">Air Import</span>, <span className="font-mono">Sea Export</span> - the customer's only template of that mode</li>
              <li><span className="font-mono">Sea Import – Excel</span>, <span className="font-mono">Air Import – Bangalore</span>, <span className="font-mono">Sea Import – Nokia</span> - when there are two</li>
              <li>Title Case, short (40 characters or fewer). No dates, no customer name, no "copy / test / final".</li>
            </ul>
            {renameChecks.length > 0 && (
              <ul className="space-y-0.5 border-t border-slate-200 pt-2 dark:border-slate-700">
                {renameChecks.map((c) => (
                  <li key={c.text} className={c.ok ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}>
                    {c.ok ? "✓" : "!"} {c.text}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {renameError && <p className="text-sm text-rose-600">{renameError}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setRenameTarget(null)}>Cancel</Button>
            <Button onClick={confirmRename} isLoading={renaming}
              disabled={!renameName.trim() || renameName.trim() === renameTarget?.name || duplicateName}>
              Rename
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Delete template?">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Delete <b>{deleteTarget?.name}</b>? This permanently removes the template, its documents and crops,
            cross-document links, operator assignments, and any jobs run from it. This can't be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="danger" onClick={confirmDelete} isLoading={deleting}>Delete template</Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
