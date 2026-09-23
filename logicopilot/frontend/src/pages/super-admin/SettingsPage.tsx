import { useEffect, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import * as systemSettingsApi from "../../api/systemSettings";
import { ReferenceSheetsSection } from "./ReferenceSheetsSection";
import { ReferenceValuesSection } from "./ReferenceValuesSection";

/** Two live kill switches for AI-dependent work - NOT a backend shutdown. Everything else
 *  keeps running: viewing jobs, correcting values already extracted, ERP submission for
 *  jobs already extracted, every other admin screen. Meant for exactly one situation - the
 *  AI provider is out of credits or otherwise down - so nothing wastes a doomed call or
 *  files a bad verdict while it lasts. Turning email pull back on needs nothing further:
 *  every deferred message is retried automatically on the very next pull.
 *
 *  Also: a Super Admin's own OpenAI API key (write-only - never shown back, not even
 *  masked) and how many mailboxes are read at once (capped at what this server can
 *  actually sustain). */
export function SettingsPage() {
  const [settings, setSettings] = useState<systemSettingsApi.SystemSettings | null>(null);
  const [busyEmail, setBusyEmail] = useState(false);
  const [busyExtraction, setBusyExtraction] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [busyKey, setBusyKey] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keySaved, setKeySaved] = useState(false);
  const [adminKeyInput, setAdminKeyInput] = useState("");
  const [busyAdminKey, setBusyAdminKey] = useState(false);
  const [adminKeyError, setAdminKeyError] = useState<string | null>(null);
  const [adminKeySaved, setAdminKeySaved] = useState(false);
  const [workerInput, setWorkerInput] = useState("1");
  const [busyWorkers, setBusyWorkers] = useState(false);
  const [workerError, setWorkerError] = useState<string | null>(null);
  const [workerSaved, setWorkerSaved] = useState(false);

  async function load() {
    try {
      const s = await systemSettingsApi.getSystemSettings();
      setSettings(s);
      setWorkerInput(String(s.email_poll_workers));
    } catch {
      setError("Could not load system settings.");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function toggleEmailPull() {
    if (!settings) return;
    setBusyEmail(true);
    setError(null);
    setNote(null);
    try {
      const next = !settings.email_pull_paused;
      const result = await systemSettingsApi.setEmailPullPaused(next);
      setSettings({ ...settings, email_pull_paused: result.email_pull_paused });
      if (next && result.cleared_in_progress > 0) {
        setNote(
          `${result.cleared_in_progress} message(s) that were mid-read got nothing done for ` +
            `them — cleared so they're retried automatically once resumed.`,
        );
      }
    } catch {
      setError("Could not update email pull.");
    } finally {
      setBusyEmail(false);
    }
  }

  async function toggleExtraction() {
    if (!settings) return;
    setBusyExtraction(true);
    setError(null);
    try {
      const result = await systemSettingsApi.setExtractionPaused(!settings.extraction_paused);
      setSettings({ ...settings, extraction_paused: result.extraction_paused });
    } catch {
      setError("Could not update extraction.");
    } finally {
      setBusyExtraction(false);
    }
  }

  async function saveApiKey() {
    if (!apiKeyInput.trim()) return;
    setBusyKey(true);
    setKeyError(null);
    setKeySaved(false);
    try {
      const result = await systemSettingsApi.setOpenAIApiKey(apiKeyInput.trim());
      setSettings((prev) => (prev ? { ...prev, openai_api_key_set: result.openai_api_key_set } : prev));
      setApiKeyInput("");
      setKeySaved(true);
    } catch (err) {
      // OpenAI rejected it live — nothing was saved, the old key (if any) is still in effect.
      setKeyError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not verify that key." : "Could not verify that key.",
      );
    } finally {
      setBusyKey(false);
    }
  }

  async function saveAdminKey() {
    if (!adminKeyInput.trim()) return;
    setBusyAdminKey(true);
    setAdminKeyError(null);
    setAdminKeySaved(false);
    try {
      const result = await systemSettingsApi.setOpenAIAdminKey(adminKeyInput.trim());
      setSettings((prev) => (prev ? { ...prev, openai_admin_key_set: result.openai_admin_key_set } : prev));
      setAdminKeyInput("");
      setAdminKeySaved(true);
    } catch (err) {
      setAdminKeyError(
        axios.isAxiosError(err)
          ? err.response?.data?.detail ?? "Could not verify that Admin key."
          : "Could not verify that Admin key.",
      );
    } finally {
      setBusyAdminKey(false);
    }
  }

  async function disconnectAdminKey() {
    setBusyAdminKey(true);
    setAdminKeyError(null);
    try {
      const result = await systemSettingsApi.disconnectOpenAIAdminKey();
      setSettings((prev) => (prev ? { ...prev, openai_admin_key_set: result.openai_admin_key_set } : prev));
    } catch {
      setAdminKeyError("Could not disconnect the Admin key.");
    } finally {
      setBusyAdminKey(false);
    }
  }

  async function saveWorkerCount() {
    const count = Number(workerInput);
    if (!Number.isInteger(count) || count < 1) {
      setWorkerError("Enter a whole number of at least 1.");
      return;
    }
    setBusyWorkers(true);
    setWorkerError(null);
    setWorkerSaved(false);
    try {
      const result = await systemSettingsApi.setEmailPollWorkers(count);
      setSettings((prev) => (prev ? { ...prev, email_poll_workers: result.email_poll_workers } : prev));
      setWorkerSaved(true);
    } catch (err) {
      setWorkerError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not update the worker count." : "Could not update the worker count.",
      );
    } finally {
      setBusyWorkers(false);
    }
  }

  return (
    <AppShell
      title="Settings"
      subtitle="System-wide controls — visible to Super Admin only."
    >
      <Card>
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">System Controls</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Stops AI-dependent work only — not the backend. Use this if the AI provider is out
            of credits or down, so nothing wastes a call or gets wrongly recorded while it lasts.
          </p>
        </div>

        {!settings && !error && (
          <p className="px-5 py-6 text-sm text-slate-500 dark:text-slate-400">Loading…</p>
        )}

        {settings && (
          <div className="flex flex-col gap-4 p-5 sm:flex-row">
            <div className="flex flex-1 items-center justify-between rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-800">
              <div>
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Email work</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {settings.email_pull_paused
                    ? "Paused — no mailbox is being checked."
                    : "Running — mailboxes are checked on schedule."}
                </p>
              </div>
              <Button
                size="sm"
                variant={settings.email_pull_paused ? "primary" : "danger"}
                onClick={toggleEmailPull}
                disabled={busyEmail}
              >
                {settings.email_pull_paused ? "Resume Email Work" : "Stop Email Work"}
              </Button>
            </div>
            <div className="flex flex-1 items-center justify-between rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-800">
              <div>
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Extraction</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {settings.extraction_paused
                    ? "Paused — Extract and Smart Upload are refused."
                    : "Running — documents are read as normal."}
                </p>
              </div>
              <Button
                size="sm"
                variant={settings.extraction_paused ? "primary" : "danger"}
                onClick={toggleExtraction}
                disabled={busyExtraction}
              >
                {settings.extraction_paused ? "Resume Extraction" : "Stop Extraction"}
              </Button>
            </div>
          </div>
        )}

        {settings && (
          <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Email poller workers</h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              How many mailboxes are read at once. This server can run at most{" "}
              <span className="font-medium text-slate-700 dark:text-slate-300">
                {settings.max_email_poll_workers}
              </span>{" "}
              at a time (one per CPU core) — anything above that is refused, not clamped.
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="w-32">
                <Input
                  label="Workers"
                  type="number"
                  min={1}
                  max={settings.max_email_poll_workers}
                  value={workerInput}
                  onChange={(e) => setWorkerInput(e.target.value)}
                />
              </div>
              <Button size="sm" onClick={saveWorkerCount} disabled={busyWorkers}>
                {busyWorkers ? "Saving…" : "Save"}
              </Button>
            </div>
            {workerSaved && (
              <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                Saved — the next email pull uses this worker count.
              </p>
            )}
            {workerError && (
              <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                {workerError}
              </p>
            )}
          </div>
        )}

        {settings && (
          <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">OpenAI API key</h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              {settings.openai_api_key_set
                ? "A custom key is saved here and in use."
                : "Using the key configured on the server (.env)."}
              {" "}Write-only — it is never shown back once saved, not even partially.
              Verified against OpenAI before saving — a wrong key is rejected, never saved.
              Applies immediately, no restart.
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="flex-1">
                <Input
                  label="New API key"
                  type="password"
                  autoComplete="off"
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  placeholder="sk-..."
                />
              </div>
              <Button size="sm" onClick={saveApiKey} disabled={busyKey || !apiKeyInput.trim()}>
                {busyKey ? "Verifying…" : "Save Key"}
              </Button>
            </div>
            {keySaved && (
              <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                Saved and verified — every AI call from now on uses this key.
              </p>
            )}
            {keyError && (
              <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                {keyError}
              </p>
            )}
          </div>
        )}

        {settings && (
          <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">OpenAI Admin API key (for real spend)</h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              A SEPARATE key from the one above — an "Admin API key" from your OpenAI
              organization (platform.openai.com → Settings → Organization → Admin keys), not
              the regular key. The regular key makes calls and cannot read what they cost;
              this one only reads spend, never makes a call. Connect it to see the real
              dollar figure (not an estimate) on the Spend Analytics screen.
            </p>
            {settings.openai_admin_key_set ? (
              <div className="mt-3 flex items-center justify-between gap-2 text-sm">
                <span className="text-emerald-600 dark:text-emerald-400">Connected</span>
                <Button size="sm" variant="ghost" onClick={disconnectAdminKey} disabled={busyAdminKey}>
                  Disconnect
                </Button>
              </div>
            ) : (
              <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="flex-1">
                  <Input
                    label="Admin API key"
                    type="password"
                    autoComplete="off"
                    value={adminKeyInput}
                    onChange={(e) => setAdminKeyInput(e.target.value)}
                    placeholder="sk-admin-..."
                  />
                </div>
                <Button size="sm" onClick={saveAdminKey} disabled={busyAdminKey || !adminKeyInput.trim()}>
                  {busyAdminKey ? "Verifying…" : "Connect"}
                </Button>
              </div>
            )}
            {adminKeySaved && (
              <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                Connected and verified — Spend Analytics now shows real spend.
              </p>
            )}
            {adminKeyError && (
              <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                {adminKeyError}
              </p>
            )}
          </div>
        )}

        {settings && (
          <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Spendings</h3>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  Jobs, documents, pages, and an estimated token count — platform-wide, with
                  graphs and a date filter.
                </p>
              </div>
              <Link to="/super-admin/spend-analytics">
                <Button size="sm" variant="secondary">Open Spend Analytics →</Button>
              </Link>
            </div>
          </div>
        )}

        {settings && <ReferenceSheetsSection />}

        {settings && <ReferenceValuesSection />}

        {note && (
          <p className="mx-5 mb-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            {note}
          </p>
        )}
        {error && (
          <p className="mx-5 mb-4 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </p>
        )}
      </Card>
    </AppShell>
  );
}
