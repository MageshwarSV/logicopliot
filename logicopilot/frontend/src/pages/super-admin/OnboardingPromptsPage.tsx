import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import {
  OnboardingCustomer, OnboardingSteps, useOnboardingCustomer, useOnboardingFullScreen,
} from "../../components/onboarding/OnboardingSteps";
import { TemplateFieldEditor, type TemplateFieldSection } from "../../components/onboarding/TemplateFieldEditor";
import { apiError, getLiveGroup, type FieldRef, type LiveGroup } from "../../api/onboardingTemplate";
import { HistoryTable, TestSection, TransformSection, type OpenField } from "./OnboardingTemplateSettingsPage";
import { PromptsReview } from "../../components/onboarding/PromptsReview";

// Customer Onboarding - Step 3: Prompts & Test (recommended workflow, 2026-11-26).
// The one place for HOW each document value is read and checked: format rules with a check on
// the field's example value, reading the template samples, and the prompt history. It took these
// over from Template Settings. The Prompts tab (phase D) builds every box's prompt in one click and
// tests them all on the samples against the customer's ERP Excel.

type Tab = "prompts" | "format" | "test" | "history";
const TABS: { key: Tab; label: string; help: string }[] = [
  { key: "prompts", label: "Prompts", help: "Every field's reading prompt and label variations, tested on the samples against the ERP Excel." },
  { key: "format", label: "Format rules & check", help: "The format rule each value goes through after it is read - check it on the field's example value." },
  { key: "test", label: "Test on samples", help: "Read each document's template sample with the current prompts and see every value." },
  { key: "history", label: "Prompt history", help: "Every prompt change on this template, newest first." },
];

export function OnboardingPromptsPage() {
  const groupId = useOnboardingCustomer("customer");
  const [group, setGroup] = useState<LiveGroup | null>(null);
  const [tab, setTab] = useState<Tab>("prompts");
  const [message, setMessage] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [fullScreen, setFullScreen] = useOnboardingFullScreen();
  const [edit, setEdit] = useState<{ ref: FieldRef; section?: TemplateFieldSection } | null>(null);
  const openField: OpenField = (ref, section) => setEdit({ ref, section: section ?? "reading" });

  async function reload() {
    if (!groupId) return;
    try { setGroup(await getLiveGroup(groupId)); } catch (e) { setMessage({ tone: "warn", text: apiError(e, "Could not load the template.") }); }
  }
  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [groupId]);

  useEffect(() => {
    if (!fullScreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullScreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullScreen, setFullScreen]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      await reload();
      setMessage({ tone: "ok", text: ok });
    } catch (e) {
      setMessage({ tone: "warn", text: apiError(e, "Could not save.") });
    }
  }

  const info = TABS.find((t) => t.key === tab)!;
  // Building prompts writes the live template: on a live one, ask once per session.
  const confirmLiveEdit = () => {
    if (group?.status !== "approved") return true;
    const key = `onboarding.liveEditOk.${groupId}`;
    try { if (sessionStorage.getItem(key)) return true; } catch { /* ignore */ }
    const ok = window.confirm("This template is LIVE. Building prompts changes the live template, and new jobs use them straight away.\n\nContinue?");
    if (ok) { try { sessionStorage.setItem(key, "1"); } catch { /* ignore */ } }
    return ok;
  };

  return (
    <AppShell title="Prompts & Test" subtitle="Customer Onboarding · Step 3 - how each document value is read, formatted and tested">
      <div className={fullScreen ? "fixed inset-0 z-40 overflow-auto bg-slate-50 p-4 dark:bg-slate-950" : ""}>
        <OnboardingSteps current={3} groupId={groupId} />
        <Card className="mb-4 flex flex-wrap items-center gap-3 p-4">
          <OnboardingCustomer name={group?.name} groupId={groupId} />
          <div className="ml-auto">
            <Button size="sm" variant="ghost" onClick={() => setFullScreen((v) => !v)}>{fullScreen ? "✕ Close full screen" : "⛶ Full screen"}</Button>
          </div>
        </Card>

        {message && (
          <div className={`mb-4 rounded-lg border px-4 py-2 text-sm ${message.tone === "ok"
            ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
            : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"}`}>
            {message.text}
            <button type="button" className="ml-3 text-xs underline" onClick={() => setMessage(null)}>dismiss</button>
          </div>
        )}

        {!groupId && (
          <Card className="p-10 text-center text-sm text-slate-500">
            No customer template chosen yet. <Link className="text-indigo-600 underline" to="/super-admin/onboarding/start">Choose one in Step 0</Link>.
          </Card>
        )}
        {groupId && !group && <Card className="p-10 text-center text-sm text-slate-500">Loading…</Card>}

        {group && (
          <>
            <div className="mb-3 flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-900" role="tablist">
              {TABS.map((t) => (
                <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
                  className={`rounded-lg px-4 py-2 text-sm font-medium ${tab === t.key ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
                  {t.label}
                </button>
              ))}
            </div>
            <p className="mb-3 px-1 text-xs text-slate-500 dark:text-slate-400">
              {info.help} Click a field's name for its full reading settings (prompt, label variations, AI suggestion).
            </p>
            <div className="flex flex-col gap-4">
              {tab === "prompts" && (
                <PromptsReview group={group} groupId={groupId} onChanged={reload} openField={(ref) => openField(ref)}
                  beforeSave={confirmLiveEdit} onMessage={(tone, text) => setMessage({ tone, text })} />
              )}
              {tab === "format" && <TransformSection group={group} act={act} openField={openField} />}
              {tab === "test" && <TestSection group={group} />}
              {tab === "history" && <HistoryTable group={group} />}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Link to={`/super-admin/onboarding/document-mapping?customer=${groupId}`} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">← Step 2</Link>
              <Link to={`/super-admin/onboarding/field-config?customer=${groupId}`} className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">Next: Field Configuration →</Link>
            </div>
          </>
        )}
      </div>
      {group && (
        <TemplateFieldEditor groupId={group.id} fieldRef={edit?.ref ?? null} open={!!edit} initialSection={edit?.section}
          onClose={() => setEdit(null)} onSaved={() => void reload()} />
      )}
    </AppShell>
  );
}
