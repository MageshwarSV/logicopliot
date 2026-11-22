import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import * as zipApi from "../../api/zipPasswords";
import * as jobsApi from "../../api/jobs";
import { formatDateTime } from "../../utils/dateTime";

/** Tenant Admin -> Zip passwords: passwords a customer's password-protected zips use. Every
 *  locked zip in this company's mail and uploads is tried with them automatically, so such a
 *  mail is read like any other. A password cannot be seen again once saved - only replaced. */
export function ZipPasswordsPage() {
  const [rows, setRows] = useState<zipApi.ZipPassword[]>([]);
  const [groups, setGroups] = useState<jobsApi.AvailableGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [password, setPassword] = useState("");
  const [groupId, setGroupId] = useState("");
  const [domains, setDomains] = useState("");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      const [r, g] = await Promise.all([zipApi.listZipPasswords(), jobsApi.listAvailableGroups()]);
      setRows(r);
      setGroups(g);
    } catch {
      setError("Could not load the zip passwords.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!label.trim() || !password.trim()) {
      setError("Enter a label and the password.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await zipApi.addZipPassword({
        label: label.trim(),
        password: password.trim(),
        group_id: groupId || null,
        sender_domains: domains.split(/[\s,]+/).map((d) => d.trim()).filter(Boolean),
      });
      setLabel("");
      setPassword("");
      setGroupId("");
      setDomains("");
      await refresh();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(r: zipApi.ZipPassword) {
    if (!window.confirm(`Delete the saved password "${r.label}"? Zips that need it will wait for a password again.`)) return;
    try {
      await zipApi.deleteZipPassword(r.id);
      await refresh();
    } catch {
      setError("Could not delete it.");
    }
  }

  return (
    <AppShell title="Zip passwords" subtitle="Passwords your customers' password-protected zips use - tried automatically on every locked zip.">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <Card className="mb-6 p-5">
        <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
          <Input label="Label" placeholder="e.g. Flex" value={label} onChange={(e) => setLabel(e.target.value)} />
          <Input label="Password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <label className="flex flex-col gap-1 text-sm text-slate-700 dark:text-slate-200">
            <span className="font-medium">Only for this shipment type (optional)</span>
            <select
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800"
            >
              <option value="">Any</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </label>
          <Input label="Sender domains (optional)" placeholder="e.g. flex.com" value={domains} onChange={(e) => setDomains(e.target.value)} />
          <p className="text-xs text-slate-500 sm:col-span-2">
            Passwords matching the shipment type or the sender's domain are tried first, then all the others.
            A customer that uses two passwords gets two rows with the same label.
          </p>
          <div className="sm:col-span-2">
            <Button type="submit" isLoading={saving}>Save password</Button>
          </div>
        </form>
      </Card>
      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && rows.length === 0 && (
        <Card className="p-8 text-center text-sm text-slate-400">No zip passwords saved yet.</Card>
      )}
      {rows.length > 0 && (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {rows.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-3 px-5 py-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium text-slate-900 dark:text-slate-100">
                  {r.label} <span className="ml-1 font-mono text-slate-400">••••••••</span>
                </p>
                <p className="text-xs text-slate-500">
                  {r.group_name ? `Only ${r.group_name}` : "Any shipment type"}
                  {r.sender_domains?.length ? ` · ${r.sender_domains.join(", ")}` : ""}
                  {" · "}
                  {r.use_count ? `opened ${r.use_count} zip${r.use_count === 1 ? "" : "s"}, last ${r.last_used_at ? formatDateTime(r.last_used_at) : "-"}` : "not used yet"}
                </p>
              </div>
              <Button size="sm" variant="ghost" onClick={() => remove(r)}>Delete</Button>
            </div>
          ))}
        </Card>
      )}
    </AppShell>
  );
}
