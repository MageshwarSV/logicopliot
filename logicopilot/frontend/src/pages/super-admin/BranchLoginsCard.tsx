import { useCallback, useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { Input, Select } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import * as api from "../../api/erpBranchSessions";
import type { BranchLogin } from "../../api/erpBranchSessions";
import type { ErpScript } from "../../types/erpScript";

const STATUS_STYLE: Record<string, { label: string; cls: string }> = {
  live: { label: "Live", cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" },
  logging_in: { label: "Logging in…", cls: "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300" },
  starting: { label: "Starting…", cls: "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300" },
  error: { label: "Can't log in", cls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" },
  off: { label: "Off", cls: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300" },
};

function ago(iso: string | null): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/** Always-on ERP logins, one per branch. Jobs of a branch-routed script run on these. */
export function BranchLoginsCard({ tenantId, scripts }: { tenantId: string; scripts: ErpScript[] }) {
  const [rows, setRows] = useState<BranchLogin[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [branch, setBranch] = useState("");
  const [scriptId, setScriptId] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [maxTabs, setMaxTabs] = useState("3");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // "Check now": which rows are being checked (with the last-check time before the press), and
  // what each check found - otherwise a live login stays "Live" and the press looks like nothing.
  const [checking, setChecking] = useState<Record<string, string | null>>({});
  const [checked, setChecked] = useState<Record<string, string>>({});

  const loginScripts = scripts.filter((s) => s.checkpoint_index !== null && s.checkpoint_index !== undefined);
  const scriptName = new Map(scripts.map((s) => [s.id, s.name]));

  const load = useCallback(async () => {
    try {
      setRows(await api.listBranchLogins(tenantId));
      setError(null);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load branch logins." : "Could not load branch logins.");
    }
  }, [tenantId]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 10000); // status changes as the keepers log in / check
    return () => window.clearInterval(t);
  }, [load]);

  function openNew() {
    setEditId(null);
    setBranch("");
    setScriptId(loginScripts[0]?.id ?? "");
    setUsername("");
    setPassword("");
    setMaxTabs("3");
    setEnabled(true);
    setFormError(null);
    setOpen(true);
  }

  function openEdit(r: BranchLogin) {
    setEditId(r.id);
    setBranch(r.branch);
    setScriptId(r.login_script_id);
    setUsername(r.username ?? "");
    setPassword("");
    setMaxTabs(String(r.max_tabs));
    setEnabled(r.enabled);
    setFormError(null);
    setOpen(true);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    const tabs = Number(maxTabs);
    if (!Number.isInteger(tabs) || tabs < 1) {
      setFormError("Jobs at once must be a whole number of at least 1.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const body = {
        branch: branch.trim(),
        login_script_id: scriptId,
        username: username.trim() || null,
        max_tabs: tabs,
        enabled,
        ...(password ? { password } : {}),
      };
      if (editId) await api.updateBranchLogin(editId, body);
      else await api.createBranchLogin(tenantId, body);
      setOpen(false);
      load();
    } catch (err) {
      setFormError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  async function checkNow(r: BranchLogin) {
    setChecking((c) => ({ ...c, [r.id]: r.last_check_at }));
    setChecked((c) => {
      const n = { ...c };
      delete n[r.id];
      return n;
    });
    try {
      await api.checkBranchLogin(r.id);
    } catch (err) {
      setChecking((c) => {
        const n = { ...c };
        delete n[r.id];
        return n;
      });
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not check." : "Could not check.");
      return;
    }
    // The keeper picks the request up within a few seconds; poll until it has answered.
    const before = r.last_check_at;
    const started = Date.now();
    const poll = async () => {
      let rowsNow: BranchLogin[] = [];
      try {
        rowsNow = await api.listBranchLogins(tenantId);
        setRows(rowsNow);
      } catch {
        /* keep polling */
      }
      const now = rowsNow.find((x) => x.id === r.id);
      const answered = now && (now.last_check_at !== before || now.status !== "live");
      if (answered || Date.now() - started > 90000) {
        const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        setChecked((c) => ({
          ...c,
          [r.id]: !now
            ? "Check sent - refresh to see the result."
            : now.status === "live" && now.last_check_at !== before
              ? `Checked ${t} — login is live and reaches the entry screen.`
              : now.status === "live"
                ? "No answer from the keeper yet - it may be busy; the status above is current."
                : `Checked ${t} — the login had dropped, logging in again…`,
        }));
        setChecking((c) => {
          const n = { ...c };
          delete n[r.id];
          return n;
        });
        return;
      }
      window.setTimeout(poll, 2000);
    };
    window.setTimeout(poll, 2000);
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      load();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "That did not work." : "That did not work.");
    }
  }

  return (
    <Card className="mt-8">
      <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4 dark:border-slate-800">
        <div>
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Branch logins</h2>
          <p className="mt-0.5 max-w-3xl text-xs text-slate-500">
            One ERP user per branch, kept logged in all the time and checked every 5 minutes. A script
            with a <b>branch field</b> runs each job on its branch's login, from after the checkpoint,
            several at once. The ERP keeps one branch per login, so each branch needs its <b>own ERP
            user</b> — and nobody else may log in as it, or the live session drops.
          </p>
        </div>
        <Button size="sm" onClick={openNew} disabled={loginScripts.length === 0}>+ Add branch login</Button>
      </div>
      {error && <div className="px-5 pt-4"><Alert>{error}</Alert></div>}
      {loginScripts.length === 0 && (
        <p className="px-5 pt-4 text-xs text-amber-700 dark:text-amber-300">
          Mark the checkpoint (the step where login ends) on a script first — that script's steps up to
          the checkpoint are how a branch logs in.
        </p>
      )}
      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {rows.length === 0 && <p className="px-5 py-4 text-sm text-slate-400">No branch logins yet.</p>}
        {rows.map((r) => {
          // Turned off wins over whatever the keeper last wrote - it may not have stopped yet.
          const st = r.enabled ? (STATUS_STYLE[r.status] ?? STATUS_STYLE.off) : STATUS_STYLE.off;
          return (
            <div key={r.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{r.branch}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${st.cls}`}>{st.label}</span>
                  <span className="text-xs text-slate-500">up to {r.max_tabs} job{r.max_tabs === 1 ? "" : "s"} at once</span>
                </div>
                <p className="text-xs text-slate-400">
                  User: {r.username || "the login script's own"} · Logs in with: {scriptName.get(r.login_script_id) ?? "—"} ·
                  Last check: {ago(r.last_check_at)}
                  {r.enabled && r.live_since && r.status === "live" ? ` · live since ${ago(r.live_since)}` : ""}
                </p>
                {r.status_note && r.enabled && <p className="text-xs text-rose-600 dark:text-rose-400">{r.status_note}</p>}
                {checked[r.id] && r.enabled && <p className="text-xs text-emerald-700 dark:text-emerald-400">{checked[r.id]}</p>}
              </div>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" variant="secondary" onClick={() => checkNow(r)} disabled={!r.enabled || r.id in checking}>
                  {r.id in checking ? "Checking…" : "Check now"}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => openEdit(r)}>Edit</Button>
                <Button size="sm" variant="secondary" onClick={() => act(() => api.updateBranchLogin(r.id, { enabled: !r.enabled }))}>
                  {r.enabled ? "Turn off" : "Turn on"}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    if (window.confirm(`Delete the ${r.branch} branch login? Its jobs will fail until a login for ${r.branch} is added again.`)) {
                      act(() => api.deleteBranchLogin(r.id));
                    }
                  }}
                >
                  Delete
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={editId ? "Edit branch login" : "Add branch login"} maxWidth="max-w-lg">
        <form className="flex flex-col gap-4" onSubmit={save}>
          <Input label="Branch" value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="e.g. Chennai" required />
          <p className="-mt-3 text-xs text-slate-400">Exactly as the job's branch field carries it. It is also what the login picks as the branch.</p>
          <Select label="Logs in with (steps up to the checkpoint)" value={scriptId} onChange={(e) => setScriptId(e.target.value)}>
            {loginScripts.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
          <div className="grid grid-cols-2 gap-3">
            <Input label="ERP user ID" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="the script's own if empty" />
            <Input
              label="ERP password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={editId ? "leave blank to keep" : "the script's own if empty"}
              autoComplete="new-password"
            />
          </div>
          <div className="grid grid-cols-2 items-end gap-3">
            <Input label="Jobs at once" type="number" min={1} max={20} value={maxTabs} onChange={(e) => setMaxTabs(e.target.value)} />
            <label className="flex items-center gap-2 pb-2.5 text-sm text-slate-700 dark:text-slate-300">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Keep this branch logged in
            </label>
          </div>
          <p className="text-xs text-slate-400">
            Jobs at once is per branch; the overall limit is still the ERP script workers setting (or Auto) under Settings.
          </p>
          {formError && <Alert>{formError}</Alert>}
          <Button type="submit" isLoading={saving} disabled={!branch.trim() || !scriptId}>{editId ? "Save" : "Add branch login"}</Button>
        </form>
      </Modal>
    </Card>
  );
}
