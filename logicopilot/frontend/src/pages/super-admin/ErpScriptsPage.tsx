import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { Input, Select } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import * as erpApi from "../../api/erpScripts";
import * as tenantsApi from "../../api/tenants";
import * as onboardingApi from "../../api/onboarding";
import type { ErpScript } from "../../types/erpScript";
import type { Tenant } from "../../types/tenant";
import type { TemplateGroup } from "../../types/onboarding";

export function ErpScriptsPage() {
  const navigate = useNavigate();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [groups, setGroups] = useState<TemplateGroup[]>([]);
  const [scripts, setScripts] = useState<ErpScript[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [hasLogin, setHasLogin] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    tenantsApi.listTenants().then((t) => {
      setTenants(t);
      if (t[0]) setTenantId(t[0].id);
    }).catch(() => setError("Failed to load tenants."));
  }, []);

  async function refresh(tid: string) {
    if (!tid) return;
    try {
      const [g, s] = await Promise.all([onboardingApi.listGroups(tid), erpApi.listErpScripts(tid)]);
      setGroups(g);
      setScripts(s);
    } catch {
      setError("Failed to load templates / scripts.");
    }
  }

  useEffect(() => {
    if (tenantId) refresh(tenantId);
  }, [tenantId]);

  const groupName = useMemo(() => new Map(groups.map((g) => [g.id, g.name])), [groups]);
  // group ids covered by a READY script (what operators can be assigned)
  const readyCovered = useMemo(() => {
    const s = new Set<string>();
    scripts.forEach((sc) => sc.status === "ready" && (sc.template_ids ?? []).forEach((id) => s.add(id)));
    return s;
  }, [scripts]);

  async function createScript(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const script = await erpApi.createErpScript(tenantId, {
        name,
        url,
        has_login: hasLogin,
        login_username: hasLogin ? username : null,
        login_password: hasLogin ? password : null,
        template_ids: selected,
      });
      setModalOpen(false);
      setName(""); setUrl(""); setHasLogin(false); setUsername(""); setPassword(""); setSelected([]);
      navigate(`/super-admin/erp-scripts/${script.id}`);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not create ERP script.");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleReady(s: ErpScript) {
    await erpApi.updateErpScript(s.id, { status: s.status === "ready" ? "draft" : "ready" });
    refresh(tenantId);
  }

  async function remove(s: ErpScript) {
    await erpApi.deleteErpScript(s.id);
    refresh(tenantId);
  }

  return (
    <AppShell
      title="ERP Script Creation"
      subtitle="Record how each template's data is entered into the ERP."
      actions={<Button size="sm" onClick={() => setModalOpen(true)} disabled={!tenantId}>+ New ERP Script</Button>}
    >
      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      <div className="mb-6 w-64">
        <Select label="Tenant" value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
          {tenants.length === 0 && <option value="">No tenants</option>}
          {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
      </div>

      {/* Templates + whether they have a ready ERP script (required to assign operators) */}
      <Card className="mb-8">
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Templates</h2>
          <p className="text-xs text-slate-500">An operator can only be assigned a template that has a <b>ready</b> ERP script.</p>
        </div>
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {groups.length === 0 && <p className="px-5 py-4 text-sm text-slate-400">No templates for this tenant yet.</p>}
          {groups.map((g) => (
            <div key={g.id} className="flex items-center justify-between px-5 py-3">
              <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{g.name}</span>
              {readyCovered.has(g.id) ? (
                <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">✓ ERP script ready</span>
              ) : (
                <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">needs ERP script</span>
              )}
            </div>
          ))}
        </div>
      </Card>

      {/* Existing scripts */}
      <Card>
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">ERP Scripts</h2>
        </div>
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {scripts.length === 0 && <p className="px-5 py-4 text-sm text-slate-400">No ERP scripts yet — create one.</p>}
          {scripts.map((s) => (
            <div key={s.id} className="flex items-center justify-between gap-3 px-5 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{s.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${s.status === "ready" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300"}`}>{s.status}</span>
                </div>
                <p className="truncate text-xs text-slate-400">{s.url}</p>
                <p className="text-xs text-slate-400">Templates: {(s.template_ids ?? []).map((id) => groupName.get(id) ?? id).join(", ") || "—"} · {s.steps?.length ?? 0} steps</p>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" onClick={() => navigate(`/super-admin/erp-scripts/${s.id}`)}>Open recorder</Button>
                <Button size="sm" variant="secondary" onClick={() => toggleReady(s)}>{s.status === "ready" ? "Set draft" : "Mark ready"}</Button>
                <Button size="sm" variant="danger" onClick={() => remove(s)}>Delete</Button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="New ERP Script">
        <form className="flex flex-col gap-4" onSubmit={createScript}>
          <Input label="Script name" value={name} onChange={(e) => setName(e.target.value)} placeholder="ICEGATE entry" required />
          <Input label="ERP URL" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://erp.example.com" required />
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={hasLogin} onChange={(e) => setHasLogin(e.target.checked)} />
            This ERP requires a login
          </label>
          {hasLogin && (
            <div className="grid grid-cols-2 gap-3">
              <Input label="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
              <Input label="Password" type="text" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
          )}
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">Templates (fields to enter)</p>
            {groups.length === 0 ? (
              <p className="text-xs text-slate-400">No templates for this tenant.</p>
            ) : (
              <div className="flex max-h-40 flex-col gap-1.5 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                {groups.map((g) => (
                  <label key={g.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                    <input type="checkbox" checked={selected.includes(g.id)} onChange={(e) => setSelected((arr) => (e.target.checked ? [...arr, g.id] : arr.filter((x) => x !== g.id)))} />
                    {g.name}
                  </label>
                ))}
              </div>
            )}
            <p className="mt-1 text-xs text-slate-400">You can select multiple templates whose fields feed this ERP form.</p>
          </div>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" isLoading={submitting} disabled={selected.length === 0}>Create &amp; open recorder</Button>
        </form>
      </Modal>
    </AppShell>
  );
}
