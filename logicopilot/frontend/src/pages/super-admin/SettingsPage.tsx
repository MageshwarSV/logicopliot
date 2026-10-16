import { useEffect, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import * as systemSettingsApi from "../../api/systemSettings";
import * as tenantsApi from "../../api/tenants";
import type { Tenant } from "../../types/tenant";
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
  const [replacingApiKey, setReplacingApiKey] = useState(false);
  const [keyPool, setKeyPool] = useState<systemSettingsApi.OpenAIPoolKey[] | null>(null);
  const [poolLabelInput, setPoolLabelInput] = useState("");
  const [poolKeyInput, setPoolKeyInput] = useState("");
  const [busyPool, setBusyPool] = useState(false);
  const [poolError, setPoolError] = useState<string | null>(null);
  const [busyPoolKeyId, setBusyPoolKeyId] = useState<string | null>(null);
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

  // Per-tenant pricing reference (CARGORA + TrackContainer.in) - pure record-keeping, not
  // tied to any billing enforcement. Picking a tenant loads (or defaults) its own numbers.
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [billingTenantId, setBillingTenantId] = useState("");
  const [billingDraft, setBillingDraft] = useState<tenantsApi.TenantBillingConfig | null>(null);
  const [loadingBilling, setLoadingBilling] = useState(false);
  const [busyBilling, setBusyBilling] = useState(false);
  const [billingError, setBillingError] = useState<string | null>(null);
  const [billingSaved, setBillingSaved] = useState(false);

  async function load() {
    try {
      const s = await systemSettingsApi.getSystemSettings();
      setSettings(s);
      setWorkerInput(String(s.email_poll_workers));
      setEngineInput(s.extraction_engine);
    } catch {
      setError("Could not load system settings.");
    }
    try {
      setKeyPool(await systemSettingsApi.listOpenAIKeyPool());
    } catch {
      setPoolError("Could not load the key pool.");
    }
  }

  useEffect(() => {
    load();
    tenantsApi.listTenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  useEffect(() => {
    if (!billingTenantId) {
      setBillingDraft(null);
      return;
    }
    let cancelled = false;
    setLoadingBilling(true);
    setBillingError(null);
    setBillingSaved(false);
    tenantsApi
      .getTenantBillingConfig(billingTenantId)
      .then((config) => {
        if (!cancelled) setBillingDraft(config);
      })
      .catch(() => {
        if (!cancelled) setBillingError("Could not load this tenant's pricing.");
      })
      .finally(() => {
        if (!cancelled) setLoadingBilling(false);
      });
    return () => {
      cancelled = true;
    };
  }, [billingTenantId]);

  function patchBillingDraft(patch: Partial<tenantsApi.TenantBillingConfig>) {
    setBillingDraft((prev) => (prev ? { ...prev, ...patch } : prev));
  }

  async function saveBillingConfig() {
    if (!billingTenantId || !billingDraft) return;
    setBusyBilling(true);
    setBillingError(null);
    setBillingSaved(false);
    try {
      const result = await tenantsApi.updateTenantBillingConfig(billingTenantId, billingDraft);
      setBillingDraft(result);
      setBillingSaved(true);
    } catch (err) {
      setBillingError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save this tenant's pricing." : "Could not save this tenant's pricing.",
      );
    } finally {
      setBusyBilling(false);
    }
  }

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
      setReplacingApiKey(false);
    } catch (err) {
      // OpenAI rejected it live — nothing was saved, the old key (if any) is still in effect.
      setKeyError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not verify that key." : "Could not verify that key.",
      );
    } finally {
      setBusyKey(false);
    }
  }

  async function addPoolKey() {
    if (!poolLabelInput.trim() || !poolKeyInput.trim()) return;
    setBusyPool(true);
    setPoolError(null);
    try {
      const row = await systemSettingsApi.addOpenAIKeyPoolKey(poolLabelInput.trim(), poolKeyInput.trim());
      setKeyPool((prev) => [...(prev ?? []), row]);
      setPoolLabelInput("");
      setPoolKeyInput("");
      setSettings((prev) =>
        prev ? { ...prev, openai_key_pool_active_count: prev.openai_key_pool_active_count + 1 } : prev,
      );
    } catch (err) {
      setPoolError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not verify that key." : "Could not verify that key.",
      );
    } finally {
      setBusyPool(false);
    }
  }

  async function togglePoolKeyActive(row: systemSettingsApi.OpenAIPoolKey) {
    setBusyPoolKeyId(row.id);
    setPoolError(null);
    try {
      const updated = await systemSettingsApi.setOpenAIKeyPoolKeyActive(row.id, !row.is_active);
      setKeyPool((prev) => (prev ?? []).map((k) => (k.id === updated.id ? updated : k)));
      setSettings((prev) =>
        prev
          ? { ...prev, openai_key_pool_active_count: prev.openai_key_pool_active_count + (updated.is_active ? 1 : -1) }
          : prev,
      );
    } catch {
      setPoolError("Could not update that key.");
    } finally {
      setBusyPoolKeyId(null);
    }
  }

  async function removePoolKey(row: systemSettingsApi.OpenAIPoolKey) {
    setBusyPoolKeyId(row.id);
    setPoolError(null);
    try {
      await systemSettingsApi.deleteOpenAIKeyPoolKey(row.id);
      setKeyPool((prev) => (prev ?? []).filter((k) => k.id !== row.id));
      if (row.is_active) {
        setSettings((prev) =>
          prev ? { ...prev, openai_key_pool_active_count: prev.openai_key_pool_active_count - 1 } : prev,
        );
      }
    } catch {
      setPoolError("Could not remove that key.");
    } finally {
      setBusyPoolKeyId(null);
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
            <div className="flex flex-1 items-center justify-between rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-800">
              <div>
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Email work</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {settings.email_pull_paused
                    ? "Paused — no mailbox is being checked."
                    : "Running — mailboxes are checked on schedule."}
                  {" "}Connect or manage mailboxes on the{" "}
                  <Link to="/super-admin/email-puller" className="underline">Email Puller</Link> page.
                </p>
              </div>
              <Button
                size="sm"
                variant={settings.email_pull_paused ? "primary" : "danger"}
                disabled={busyEmail}
                onClick={toggleEmailPull}
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

        <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Tenant pricing</h3>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            CARGORA's own platform fee and AI page-processing rate, and which TrackContainer.in
            plan a tenant is on. Pure reference numbers for your own records — nothing here
            changes what the tenant can actually do on the platform.
          </p>
          <div className="mt-3 sm:max-w-xs">
            <label className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">Tenant</label>
            <select
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              value={billingTenantId}
              onChange={(e) => setBillingTenantId(e.target.value)}
            >
              <option value="">Choose a tenant…</option>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </div>

          {loadingBilling && <p className="mt-3 text-xs text-slate-400">Loading…</p>}

          {billingDraft && !loadingBilling && (
            <div className="mt-4 flex flex-col gap-5">
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  CARGORA
                </p>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Input
                    label="Monthly fee (₹)"
                    type="number"
                    min={0}
                    value={billingDraft.cargora_monthly_fee}
                    onChange={(e) => patchBillingDraft({ cargora_monthly_fee: Number(e.target.value) })}
                  />
                  <Input
                    label="Rate up to limit (₹/page)"
                    type="number"
                    min={0}
                    step="0.01"
                    value={billingDraft.cargora_tier1_rate_per_page}
                    onChange={(e) => patchBillingDraft({ cargora_tier1_rate_per_page: Number(e.target.value) })}
                  />
                  <Input
                    label="Page limit"
                    type="number"
                    min={0}
                    value={billingDraft.cargora_tier1_page_limit}
                    onChange={(e) => patchBillingDraft({ cargora_tier1_page_limit: Number(e.target.value) })}
                  />
                  <Input
                    label="Rate above limit (₹/page)"
                    type="number"
                    min={0}
                    step="0.01"
                    value={billingDraft.cargora_tier2_rate_per_page}
                    onChange={(e) => patchBillingDraft({ cargora_tier2_rate_per_page: Number(e.target.value) })}
                  />
                </div>
              </div>

              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  TrackContainer.in
                </p>
                <div className="mb-3 sm:max-w-xs">
                  <label className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">
                    Plan
                  </label>
                  <select
                    className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    value={billingDraft.trackcontainer_plan ?? ""}
                    onChange={(e) =>
                      patchBillingDraft({
                        trackcontainer_plan: (e.target.value || null) as tenantsApi.TenantBillingConfig["trackcontainer_plan"],
                      })
                    }
                  >
                    <option value="">Not subscribed</option>
                    <option value="starter">Starter</option>
                    <option value="professional">Professional</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Input
                    label="Starter price (₹ + GST)"
                    type="number"
                    min={0}
                    value={billingDraft.trackcontainer_starter_price}
                    onChange={(e) => patchBillingDraft({ trackcontainer_starter_price: Number(e.target.value) })}
                  />
                  <Input
                    label="Starter container limit"
                    type="number"
                    min={0}
                    value={billingDraft.trackcontainer_starter_container_limit}
                    onChange={(e) =>
                      patchBillingDraft({ trackcontainer_starter_container_limit: Number(e.target.value) })
                    }
                  />
                  <Input
                    label="Professional price (₹ + GST)"
                    type="number"
                    min={0}
                    value={billingDraft.trackcontainer_professional_price}
                    onChange={(e) =>
                      patchBillingDraft({ trackcontainer_professional_price: Number(e.target.value) })
                    }
                  />
                  <Input
                    label="Professional container limit"
                    type="number"
                    min={0}
                    value={billingDraft.trackcontainer_professional_container_limit}
                    onChange={(e) =>
                      patchBillingDraft({ trackcontainer_professional_container_limit: Number(e.target.value) })
                    }
                  />
                </div>
              </div>

              <div>
                <Button size="sm" onClick={saveBillingConfig} disabled={busyBilling}>
                  {busyBilling ? "Saving…" : "Save"}
                </Button>
              </div>
              {billingSaved && (
                <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                  Saved.
                </p>
              )}
              {billingError && (
                <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                  {billingError}
                </p>
              )}
            </div>
          )}
        </div>

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
            {settings.openai_api_key_set && !replacingApiKey ? (
              <div className="mt-3 flex items-center justify-between gap-2 text-sm">
                <span className="text-emerald-600 dark:text-emerald-400">Connected</span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setReplacingApiKey(true);
                    setKeySaved(false);
                    setKeyError(null);
                  }}
                >
                  Replace Key
                </Button>
              </div>
            ) : (
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
                  {busyKey ? "Verifying…" : settings.openai_api_key_set ? "Save New Key" : "Save Key"}
                </Button>
                {settings.openai_api_key_set && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setReplacingApiKey(false);
                      setApiKeyInput("");
                      setKeyError(null);
                    }}
                    disabled={busyKey}
                  >
                    Cancel
                  </Button>
                )}
              </div>
            )}
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
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">OpenAI key pool</h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Add several keys and calls are spread across them in rotation, so one key's rate
              limit never blocks everything. This only raises real throughput when each key
              comes from a SEPARATE OpenAI Project (platform.openai.com → Settings → Projects
              → API keys) — OpenAI meters rate limits per project, so several keys from the
              SAME project still share one budget. With no keys added here, every call keeps
              using the single key above, exactly as before.
              {" "}Write-only, same as the key above — never shown back once saved.
              {settings.openai_key_pool_active_count > 0 && (
                <span className="font-medium text-emerald-700 dark:text-emerald-400">
                  {" "}{settings.openai_key_pool_active_count} active key{settings.openai_key_pool_active_count === 1 ? "" : "s"} in rotation.
                </span>
              )}
            </p>

            {keyPool && keyPool.length > 0 && (
              <ul className="mt-3 flex flex-col gap-2">
                {keyPool.map((row) => (
                  <li
                    key={row.id}
                    className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700"
                  >
                    <span className="truncate text-slate-800 dark:text-slate-200">{row.label}</span>
                    <span className="flex items-center gap-2">
                      {row.is_active ? (
                        <span className="text-xs text-emerald-600 dark:text-emerald-400">Active</span>
                      ) : (
                        <span className="text-xs text-slate-400">Paused</span>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => togglePoolKeyActive(row)}
                        disabled={busyPoolKeyId === row.id}
                      >
                        {row.is_active ? "Pause" : "Resume"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => removePoolKey(row)}
                        disabled={busyPoolKeyId === row.id}
                      >
                        Remove
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="w-full sm:w-40">
                <Input
                  label="Label"
                  value={poolLabelInput}
                  onChange={(e) => setPoolLabelInput(e.target.value)}
                  placeholder="Project A"
                />
              </div>
              <div className="flex-1">
                <Input
                  label="API key"
                  type="password"
                  autoComplete="off"
                  value={poolKeyInput}
                  onChange={(e) => setPoolKeyInput(e.target.value)}
                  placeholder="sk-..."
                />
              </div>
              <Button
                size="sm"
                onClick={addPoolKey}
                disabled={busyPool || !poolLabelInput.trim() || !poolKeyInput.trim()}
              >
                {busyPool ? "Verifying…" : "Add Key"}
              </Button>
            </div>
            {poolError && (
              <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                {poolError}
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
