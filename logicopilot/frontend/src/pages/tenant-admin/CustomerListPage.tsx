import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import * as jobsApi from "../../api/jobs";
import * as onboardingApi from "../../api/onboarding";
import * as emailApi from "../../api/email";
import * as usersApi from "../../api/users";
import * as tenantsApi from "../../api/tenants";
import type { User } from "../../types/auth";
import { MODES } from "../../types/onboarding";
import { useAuth } from "../../auth/useAuth";

export function CustomerListPage() {
  const { user } = useAuth();
  const [customers, setCustomers] = useState<jobsApi.AvailableGroup[]>([]);
  const [operators, setOperators] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [assignFor, setAssignFor] = useState<jobsApi.AvailableGroup | null>(null);
  const [editFor, setEditFor] = useState<jobsApi.AvailableGroup | null>(null);
  const [editName, setEditName] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [operatorId, setOperatorId] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [mailStatus, setMailStatus] = useState<emailApi.EmailStatus | null>(null);
  const [pulling, setPulling] = useState(false);
  const [pullResult, setPullResult] = useState<emailApi.PullResult | null>(null);
  // Which modes THIS tenant is licensed for — empty means unrestricted, offer all of them.
  const [allowedModes, setAllowedModes] = useState<string[]>([]);
  const [modeSavingId, setModeSavingId] = useState<string | null>(null);

  async function checkMailbox() {
    setMailStatus(null);
    try {
      setMailStatus(await emailApi.emailStatus());
    } catch {
      setMailStatus({ ok: false, error: "Could not reach the mailbox." });
    }
  }

  async function pullNow() {
    setPulling(true);
    setPullResult(null);
    setError(null);
    try {
      const res = await emailApi.pullEmail();
      setPullResult(res);
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Pull failed.");
    } finally {
      setPulling(false);
    }
  }

  async function refresh() {
    try {
      const [groups, users] = await Promise.all([jobsApi.listAvailableGroups(), usersApi.listUsers()]);
      setCustomers(groups);
      setOperators(users.filter((u) => u.role === "operator"));
    } catch {
      setError("Failed to load customers.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    checkMailbox();
    if (user?.tenant_id) {
      tenantsApi
        .getTenant(user.tenant_id)
        .then((t) => setAllowedModes(t.allowed_modes ?? []))
        .catch(() => {
          /* non-fatal: falls back to offering every mode */
        });
    }
  }, [user?.tenant_id]);

  async function handleSetMode(groupId: string, mode: string) {
    if (!mode) return;
    setModeSavingId(groupId);
    setError(null);
    try {
      await onboardingApi.setGroupMode(groupId, mode);
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not set the mode.");
    } finally {
      setModeSavingId(null);
    }
  }

  const modeOptions = allowedModes.length ? allowedModes : [...MODES];

  function openAssign(c: jobsApi.AvailableGroup) {
    setAssignFor(c);
    setEmail(c.pull_email ?? "");
    setOperatorId(c.pull_operator_id ?? "");
    setError(null);
  }

  async function saveEmail(e: FormEvent) {
    e.preventDefault();
    if (!assignFor) return;
    if (!operatorId) { setError("Pick the operator who should receive these jobs."); return; }
    setSaving(true);
    setError(null);
    try {
      await onboardingApi.setPullEmail(assignFor.id, email.trim() || null, operatorId || null);
      setAssignFor(null);
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  const operatorName = (id?: string | null) => operators.find((o) => o.id === id)?.full_name ?? null;

  function openEdit(c: jobsApi.AvailableGroup) {
    setEditFor(c);
    setEditName(c.name);
    setEditError(null);
  }

  async function saveEdit(e: FormEvent) {
    e.preventDefault();
    if (!editFor) return;
    const name = editName.trim();
    if (!name) { setEditError("Name cannot be empty."); return; }
    setEditSaving(true);
    setEditError(null);
    try {
      await onboardingApi.renameGroup(editFor.id, name);
      setEditFor(null);
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setEditError(err.response?.data?.detail ?? "Could not save.");
    } finally {
      setEditSaving(false);
    }
  }

  return (
    <AppShell title="Shipment Type" subtitle="Customers assigned to you — assign a mailbox to auto-pull their documents.">
      {error && !assignFor && <div className="mb-6"><Alert>{error}</Alert></div>}

      {/* Mailbox auto-pull */}
      <Card className="mb-6 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Document mailbox</h2>
            {mailStatus === null ? (
              <p className="text-xs text-slate-400">Checking mailbox…</p>
            ) : mailStatus.ok ? (
              <p className="text-xs text-slate-500">
                Connected to <span className="font-medium text-indigo-600 dark:text-indigo-400">{mailStatus.user}</span> ·{" "}
                {mailStatus.unseen} unread of {mailStatus.total}
              </p>
            ) : (
              <p className="text-xs text-rose-500">Mailbox unavailable: {mailStatus.error}</p>
            )}
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={checkMailbox} disabled={pulling}>Refresh status</Button>
            <Button size="sm" onClick={pullNow} isLoading={pulling} disabled={!mailStatus?.ok}>Pull email now</Button>
          </div>
        </div>

        {pullResult && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs dark:border-slate-800 dark:bg-slate-900/40">
            {pullResult.note && <p className="text-slate-500">{pullResult.note}</p>}
            {pullResult.error && <p className="text-rose-500">{pullResult.error}</p>}
            {pullResult.processed && pullResult.processed.length === 0 && !pullResult.note && (
              <p className="text-slate-500">No new matching messages.</p>
            )}
            {pullResult.processed && pullResult.processed.length > 0 && (
              <ul className="space-y-1.5">
                {pullResult.processed.map((m, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${m.job_id ? "bg-emerald-500" : "bg-slate-300"}`} />
                    <span className="min-w-0">
                      <span className="font-medium text-slate-700 dark:text-slate-200">{m.subject}</span>
                      <span className="text-slate-400"> · {m.from}</span>
                      {m.job_id ? (
                        <span className="block text-emerald-600 dark:text-emerald-400">
                          → job created for <b>{m.matched}</b>
                          {m.filled_slots?.length ? ` · filled: ${m.filled_slots.join(", ")}` : " · no slots matched"}
                        </span>
                      ) : (
                        <span className="block text-slate-400">skipped — {m.reason}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && customers.length === 0 && (
        <Card className="p-8 text-center text-sm text-slate-400">
          No customers assigned to you yet.
        </Card>
      )}
      <Card className="divide-y divide-slate-100 dark:divide-slate-800">
        {customers.map((c) => (
          <div key={c.id} className="flex items-center justify-between gap-3 px-5 py-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{c.name}</p>
                <select
                  value={c.mode ?? ""}
                  onChange={(e) => handleSetMode(c.id, e.target.value)}
                  disabled={modeSavingId === c.id}
                  className={`rounded-md border px-1.5 py-0.5 text-xs ${
                    c.mode
                      ? "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"
                      : "border-amber-300 text-amber-700 dark:border-amber-500/40 dark:text-amber-400"
                  }`}
                  title="Which transport mode this template is for"
                >
                  <option value="" disabled>
                    Set mode…
                  </option>
                  {modeOptions.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              {c.pull_email ? (
                <p className="text-xs text-slate-500">
                  Mail: <span className="font-medium text-indigo-600 dark:text-indigo-400">{c.pull_email}</span>
                  {c.pull_operator_id ? (
                    <> · to <span className="font-medium text-emerald-600 dark:text-emerald-400">{operatorName(c.pull_operator_id) ?? "operator"}</span></>
                  ) : (
                    <> · <span className="text-amber-600">no operator set</span></>
                  )}
                </p>
              ) : (
                <p className="text-xs text-slate-400">No mailbox assigned</p>
              )}
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => openEdit(c)}>Edit</Button>
              <Button size="sm" variant={c.pull_email ? "secondary" : "primary"} onClick={() => openAssign(c)}>
                {c.pull_email ? "Change mail" : "Assign mail"}
              </Button>
            </div>
          </div>
        ))}
      </Card>

      <Modal open={editFor !== null} onClose={() => setEditFor(null)} title={`Edit — ${editFor?.name ?? ""}`}>
        <form className="flex flex-col gap-4" onSubmit={saveEdit}>
          <Input label="Customer name" value={editName} onChange={(e) => setEditName(e.target.value)} />
          {editError && <Alert>{editError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setEditFor(null)}>Cancel</Button>
            <Button type="submit" isLoading={editSaving}>Save</Button>
          </div>
        </form>
      </Modal>

      <Modal open={assignFor !== null} onClose={() => setAssignFor(null)} title={`Assign mail — ${assignFor?.name ?? ""}`}>
        <form className="flex flex-col gap-4" onSubmit={saveEmail}>
          {/* Step 1 — choose the operator who will own these jobs */}
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">1. Which operator handles this customer?</p>
            {operators.length === 0 ? (
              <p className="text-xs text-slate-400">No operators yet — create one under Operator Creation first.</p>
            ) : (
              <div className="flex max-h-44 flex-col gap-1 overflow-y-auto rounded-lg border border-slate-200 p-1.5 dark:border-slate-700">
                {operators.map((o) => (
                  <label
                    key={o.id}
                    className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm ${operatorId === o.id ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"}`}
                  >
                    <input type="radio" name="op" checked={operatorId === o.id} onChange={() => setOperatorId(o.id)} />
                    <span className="min-w-0">
                      <span className="font-medium">{o.full_name}</span> <span className="text-xs text-slate-400">{o.email}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Step 2 — the sender address for that operator's jobs */}
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">2. Sender email for {operatorId ? (operatorName(operatorId) ?? "this operator") : "this operator"}</p>
            <Input label="" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="client@company.com" />
            <p className="mt-1 text-xs text-slate-400">
              Documents emailed from this address are auto-pulled, classified, and the job is shown to <b>this operator alone</b>.
            </p>
          </div>

          {error && <Alert>{error}</Alert>}
          <div className="flex justify-end gap-2">
            {assignFor?.pull_email && (
              <Button type="button" variant="ghost" onClick={() => { setEmail(""); setOperatorId(""); }}>Clear</Button>
            )}
            <Button type="submit" isLoading={saving}>Save</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
