import { useEffect, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Toggle } from "../../components/ui/Toggle";
import * as systemSettingsApi from "../../api/systemSettings";
import { ReferenceSheetsSection } from "./ReferenceSheetsSection";
import { ReferenceValuesSection } from "./ReferenceValuesSection";

const PROVIDER_LABEL: Record<string, string> = { gmail: "Gmail", zoho: "Zoho" };

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
  const [engineInput, setEngineInput] =
    useState<systemSettingsApi.SystemSettings["extraction_engine"]>("ocr_gpt4o_mini");
  const [busyEngine, setBusyEngine] = useState(false);
  const [engineError, setEngineError] = useState<string | null>(null);
  const [engineSaved, setEngineSaved] = useState(false);
  const [mailboxesOpen, setMailboxesOpen] = useState(false);
  const [mailboxes, setMailboxes] = useState<systemSettingsApi.Mailbox[] | null>(null);
  const [mailboxesLoading, setMailboxesLoading] = useState(false);
  const [mailboxesError, setMailboxesError] = useState<string | null>(null);
  const [busyMailboxId, setBusyMailboxId] = useState<string | null>(null);

  async function load() {
    try {
      const s = await systemSettingsApi.getSystemSettings();
      setSettings(s);
      setWorkerInput(String(s.email_poll_workers));
      setEngineInput(s.extraction_engine);
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

  async function toggleMailboxesOpen() {
    const next = !mailboxesOpen;
    setMailboxesOpen(next);
    if (next && mailboxes === null) {
      setMailboxesLoading(true);
      setMailboxesError(null);
      try {
        setMailboxes(await systemSettingsApi.listMailboxes());
      } catch {
        setMailboxesError("Could not load connected mailboxes.");
      } finally {
        setMailboxesLoading(false);
      }
    }
  }

  async function toggleMailboxPaused(box: systemSettingsApi.Mailbox) {
    if (!mailboxes) return;
    setBusyMailboxId(box.user_id);
    setMailboxesError(null);
    try {
      const result = await systemSettingsApi.setMailboxPaused(box.user_id, !box.mail_paused);
      setMailboxes(
        mailboxes.map((m) => (m.user_id === box.user_id ? { ...m, mail_paused: result.mail_paused } : m)),
      );
    } catch {
      setMailboxesError(`Could not update ${box.mail_email}.`);
    } finally {
      setBusyMailboxId(null);
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

  async function saveExtractionEngine() {
    setBusyEngine(true);
    setEngineError(null);
    setEngineSaved(false);
    try {
      const result = await systemSettingsApi.setExtractionEngine(engineInput);
      setSettings((prev) => (prev ? { ...prev, extraction_engine: result.extraction_engine } : prev));
      setEngineSaved(true);
    } catch (err) {
      setEngineError(
        axios.isAxiosError(err)
          ? err.response?.data?.detail ?? "Could not update the extraction engine."
          : "Could not update the extraction engine.",
      );
    } finally {
      setBusyEngine(false);
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
            <div className="flex-1 rounded-xl border border-slate-200 dark:border-slate-800">
              {/* A <div>, not a <button>, because it wraps a REAL button (Pause/Resume) -
                  nesting a <button> inside a <button> is invalid HTML and browsers will
                  silently break the inner one. Keyboard/AT users still get a proper toggle
                  via role="button" + tabIndex + onKeyDown below. */}
              <div
                role="button"
                tabIndex={0}
                onClick={toggleMailboxesOpen}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggleMailboxesOpen();
                  }
                }}
                aria-expanded={mailboxesOpen}
                className="flex w-full cursor-pointer items-center justify-between gap-3 px-4 py-3 text-left"
              >
                <div className="flex items-center gap-2">
                  <svg
                    className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${mailboxesOpen ? "rotate-90" : ""}`}
                    viewBox="0 0 20 20"
                    fill="currentColor"
                  >
                    <path
                      fillRule="evenodd"
                      d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
                      clipRule="evenodd"
                    />
                  </svg>
                  <div>
                    <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Email work</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {settings.email_pull_paused
                        ? "Paused — no mailbox is being checked."
                        : "Running — mailboxes are checked on schedule."}
                      {" "}Click to see connected mailboxes.
                    </p>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant={settings.email_pull_paused ? "primary" : "danger"}
                  disabled={busyEmail}
                  onClick={(e) => {
                    // Stop this from also toggling the mailbox list open/closed - Pause/
                    // Resume and expand/collapse are two independent actions on this card.
                    e.stopPropagation();
                    toggleEmailPull();
                  }}
                >
                  {settings.email_pull_paused ? "Resume Email Work" : "Stop Email Work"}
                </Button>
              </div>

              {mailboxesOpen && (
                <div className="border-t border-slate-200 px-4 py-3 dark:border-slate-800">
                  {mailboxesLoading && (
                    <p className="text-xs text-slate-500 dark:text-slate-400">Loading mailboxes…</p>
                  )}
                  {mailboxesError && (
                    <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                      {mailboxesError}
                    </p>
                  )}
                  {!mailboxesLoading && mailboxes && mailboxes.length === 0 && (
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      No operator has connected a mailbox yet.
                    </p>
                  )}
                  {!mailboxesLoading && mailboxes && mailboxes.length > 0 && (
                    <ul className="flex flex-col gap-2">
                      {mailboxes.map((box) => (
                        <li
                          key={box.user_id}
                          className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/60"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm text-slate-900 dark:text-slate-100">
                              {box.mail_email}
                              <span className="ml-2 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                                {PROVIDER_LABEL[box.mail_provider ?? ""] ?? box.mail_provider ?? "—"}
                              </span>
                            </p>
                            <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                              {box.full_name}
                              {box.tenant_name ? ` · ${box.tenant_name}` : ""}
                              {!box.is_active ? " · account inactive" : ""}
                            </p>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <span className="text-xs text-slate-500 dark:text-slate-400">
                              {box.mail_paused ? "Off" : "On"}
                            </span>
                            <Toggle
                              checked={!box.mail_paused}
                              disabled={busyMailboxId === box.user_id}
                              onChange={() => toggleMailboxPaused(box)}
                              label={`Poll ${box.mail_email}`}
                            />
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
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
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Extraction engine</h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Which engine production document classification and field extraction use.
              "Document OCR + GPT-4o-mini" is today's pipeline. "GPT-5-mini Vision" reads the
              page image directly instead of OCR text for both steps. Takes effect on the
              very next job, no restart. Never affects the Template Wizard's own training
              flow, which always stays on OCR text + GPT-4o-mini either way.
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="flex-1 sm:max-w-xs">
                <label className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">
                  Engine
                </label>
                <select
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                  value={engineInput}
                  onChange={(e) =>
                    setEngineInput(e.target.value as systemSettingsApi.SystemSettings["extraction_engine"])
                  }
                >
                  {systemSettingsApi.EXTRACTION_ENGINE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <Button size="sm" onClick={saveExtractionEngine} disabled={busyEngine}>
                {busyEngine ? "Saving…" : "Save"}
              </Button>
            </div>
            {engineSaved && (
              <p className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                Saved — the next job uses this engine.
              </p>
            )}
            {engineError && (
              <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                {engineError}
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
