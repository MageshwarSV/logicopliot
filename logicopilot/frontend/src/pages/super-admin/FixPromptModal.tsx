import { useEffect, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/extractionCorrections";
import type { CorrectedField, FixInfo, FixSuggestion, FixTestResult } from "../../api/extractionCorrections";

const AREA = "w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 font-mono text-xs leading-relaxed shadow-inner focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-200 dark:border-indigo-500/30 dark:bg-slate-950 dark:focus:ring-indigo-500/30";
const READ = "w-full rounded-lg border border-slate-200 bg-white/70 px-3 py-2 font-mono text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-300";
const SUGGEST_AREA = "w-full rounded-lg border border-violet-200 bg-white px-3 py-2 font-mono text-xs leading-relaxed focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-200 dark:border-violet-500/30 dark:bg-slate-950";

/** A small coloured label: what kind of fact it is shows in its colour. */
function Chip({ tone, label, value }: { tone: "slate" | "rose" | "sky" | "violet"; label: string; value: string }) {
  const tones = {
    slate: "border-slate-200 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200",
    rose: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300",
    sky: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200",
    violet: "border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-200",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${tones[tone]}`}>
      <span className="opacity-70">{label}</span>
      <span className="font-semibold">{value}</span>
    </span>
  );
}

function errorText(e: unknown, fallback: string) {
  if (axios.isAxiosError(e)) {
    const d = e.response?.data?.detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

/** Fix one field's extraction prompt: current vs new, AI Suggestion (shown, never saved),
 *  Test on the latest corrected jobs (read-only), Apply Changes (saves into the template),
 *  history with Revert. */
export function FixPromptModal({ field, onClose }: { field: CorrectedField; onClose: () => void }) {
  const [info, setInfo] = useState<FixInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [hint, setHint] = useState("");
  const [suggestion, setSuggestion] = useState<FixSuggestion | null>(null);
  const [suggestPrompt, setSuggestPrompt] = useState("");
  const [suggestHint, setSuggestHint] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [testResult, setTestResult] = useState<FixTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  function load(i: FixInfo) {
    setInfo(i);
    setPrompt(i.prompt);
    setHint(i.format_hint ?? "");
  }

  useEffect(() => {
    api.getFix(field).then(load).catch((e) => setLoadError(errorText(e, "Could not load this field's prompt.")));
  }, [field]);

  const changed = !!info && (prompt.trim() !== info.prompt.trim() || (info.has_format_hint && hint.trim() !== (info.format_hint ?? "").trim()));

  async function runSuggest() {
    setSuggesting(true);
    setMessage(null);
    try {
      const s = await api.suggestFix(field);
      setSuggestion(s);
      setSuggestPrompt(s.prompt);
      setSuggestHint(s.format_hint ?? "");
    } catch (e) {
      setMessage({ kind: "error", text: errorText(e, "The AI suggestion could not be generated. Try again.") });
    } finally {
      setSuggesting(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setMessage(null);
    setTestResult(null);
    try {
      setTestResult(await api.testFix(field, prompt, info?.has_format_hint ? hint : null));
    } catch (e) {
      setMessage({ kind: "error", text: errorText(e, "The test could not be run. Try again.") });
    } finally {
      setTesting(false);
    }
  }

  async function runApply() {
    if (!info) return;
    if (!window.confirm(`Save this prompt into the template for "${field.label_name}"? New extractions and re-runs will use it. Existing job values are not changed.`)) return;
    setApplying(true);
    setMessage(null);
    try {
      load(await api.applyFix(field, prompt, info.has_format_hint ? hint : null));
      setTestResult(null);
      setMessage({ kind: "ok", text: "Saved to the template. New extractions and re-runs use the new prompt." });
    } catch (e) {
      setMessage({ kind: "error", text: errorText(e, "The prompt could not be saved.") });
    } finally {
      setApplying(false);
    }
  }

  async function runRevert(changeId: string) {
    if (!window.confirm("Put the prompt from before this change back into the template?")) return;
    setMessage(null);
    try {
      load(await api.revertFix(field, changeId));
      setMessage({ kind: "ok", text: "Reverted. The earlier prompt is back in the template." });
    } catch (e) {
      setMessage({ kind: "error", text: errorText(e, "Could not revert.") });
    }
  }

  return (
    <Modal open onClose={onClose} title={`Fix prompt: ${field.label_name}`} maxWidth="max-w-6xl">
      <div className="grid gap-5 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone="slate" label="Template" value={field.template} />
          <Chip tone="slate" label="Document" value={field.document} />
          <Chip tone="rose" label="Corrected on" value={`${field.jobs_corrected} job(s)`} />
          {info && <Chip tone="sky" label="Extraction model now" value={`${info.model}${info.reads_images ? " · reads page images" : ""}`} />}
          {suggestion && <Chip tone="violet" label="Suggestion made with" value={suggestion.model} />}
        </div>

        {loadError && <p className="rounded-lg bg-rose-50 p-3 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{loadError}</p>}
        {info && !info.editable && (
          <p className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
            This field has no prompt to fix. {info.reason}
          </p>
        )}

        {info?.editable && (
          <>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid min-w-0 content-start gap-2 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-800/40">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">Current prompt</h3>
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300">In use now</span>
                </div>
                <textarea id="current-prompt" readOnly value={info.prompt} rows={14} className={READ} />
                {info.has_format_hint && (
                  <label className="grid gap-1 text-xs text-slate-500">Current format hint
                    <textarea id="current-hint" readOnly value={info.format_hint ?? ""} rows={2} className={READ} />
                  </label>
                )}
              </div>
              <div className="grid min-w-0 content-start gap-2 rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 dark:border-indigo-500/30 dark:bg-indigo-500/10">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">New prompt</h3>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${changed ? "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300" : "bg-white text-slate-500 dark:bg-slate-800 dark:text-slate-400"}`}>
                    {changed ? "Edited · not saved" : "Same as current"}
                  </span>
                </div>
                <textarea id="new-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={14} className={AREA} />
                {info.has_format_hint && (
                  <label className="grid gap-1 text-xs text-slate-500">New format hint
                    <textarea id="new-hint" value={hint} onChange={(e) => setHint(e.target.value)} rows={2} className={AREA} />
                  </label>
                )}
              </div>
            </div>
            {info.format_rule && (
              <p className="text-xs text-slate-500">Data Transformation rule applied after reading (not changed here): <span className="font-mono">{info.format_rule}</span></p>
            )}

            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <button type="button" onClick={runSuggest} disabled={suggesting}
                className="inline-flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-semibold text-violet-800 hover:bg-violet-100 disabled:opacity-60 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-200">
                {suggesting ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-violet-300 border-t-violet-700" /> : "✦"} AI Suggestion
              </button>
              <button type="button" onClick={runTest} disabled={testing || !info.test_supported || !prompt.trim()}
                title={info.test_supported ? "Read this field again with the New prompt on the latest corrected jobs" : "Not available for a computed field with one value per line"}
                className="inline-flex items-center gap-2 rounded-lg border border-teal-200 bg-teal-50 px-4 py-2 text-sm font-semibold text-teal-800 hover:bg-teal-100 disabled:opacity-60 dark:border-teal-500/30 dark:bg-teal-500/10 dark:text-teal-200">
                {testing && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-teal-300 border-t-teal-700" />} Test new prompt
              </button>
              <span className="flex-1" />
              <Button onClick={runApply} isLoading={applying} disabled={!changed || !prompt.trim()} className="rounded-lg px-5 py-2">Apply Changes</Button>
            </div>
            {(suggesting || testing) && (
              <p className="text-xs text-slate-500">{suggesting ? "Reading the corrections and pages…" : "Re-reading the latest corrected jobs…"} This can take up to a minute.</p>
            )}
            {message && (
              <p className={`rounded-lg p-3 text-sm ${message.kind === "ok" ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>{message.text}</p>
            )}

            {suggestion && (
              <section className="grid gap-3 rounded-xl border border-violet-200 bg-gradient-to-br from-violet-50 to-fuchsia-50/60 p-4 dark:border-violet-500/30 dark:from-violet-500/10 dark:to-fuchsia-500/5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-violet-800 dark:text-violet-300">✦ AI suggestion · not saved</h3>
                  <span className="text-xs text-violet-700/80 dark:text-violet-300/80">
                    from {suggestion.corrections_used} correction(s) and {suggestion.images_used} page image(s) · made with <b>{suggestion.model}</b>
                  </span>
                </div>
                {suggestion.analysis && (
                  <div className="rounded-lg border border-violet-100 bg-white/80 p-3 text-sm text-slate-700 dark:border-violet-500/20 dark:bg-slate-950/50 dark:text-slate-200">
                    <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">Why the AI got it wrong</span>
                    {suggestion.analysis}
                  </div>
                )}
                <textarea id="suggested-prompt" value={suggestPrompt} onChange={(e) => setSuggestPrompt(e.target.value)} rows={10} className={SUGGEST_AREA} />
                {info.has_format_hint && (
                  <label className="grid gap-1 text-xs text-slate-500">Suggested format hint
                    <textarea id="suggested-hint" value={suggestHint} onChange={(e) => setSuggestHint(e.target.value)} rows={2} className={SUGGEST_AREA} />
                  </label>
                )}
                <div>
                  <button type="button" onClick={() => { setPrompt(suggestPrompt); if (info.has_format_hint) setHint(suggestHint); }}
                    className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-500">
                    Copy into New prompt
                  </button>
                </div>
              </section>
            )}

            {testResult && (
              <section className="grid gap-3 rounded-xl border border-teal-200 bg-teal-50/60 p-4 dark:border-teal-500/30 dark:bg-teal-500/5">
                <div className="flex flex-wrap items-center gap-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-teal-800 dark:text-teal-300">Test on the latest corrected jobs</h3>
                  <span className="rounded-full bg-white px-3 py-1 text-xs text-slate-700 dark:bg-slate-900 dark:text-slate-200">
                    Current prompt: <b>{testResult.old_correct}</b> / {testResult.total} right
                  </span>
                  <span className={`rounded-full px-3 py-1 text-xs font-semibold ${testResult.new_correct >= testResult.old_correct ? "bg-emerald-600 text-white" : "bg-rose-600 text-white"}`}>
                    New prompt: {testResult.new_correct} / {testResult.total} right
                  </span>
                  {info && <span className="text-xs text-teal-800/70 dark:text-teal-300/70">read with {info.model}</span>}
                </div>
                <div className="overflow-x-auto rounded-lg border border-teal-100 bg-white dark:border-teal-500/20 dark:bg-slate-950">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-teal-50 text-teal-900 dark:bg-teal-500/10 dark:text-teal-200">
                      <tr>
                        <th className="px-3 py-2 font-medium">Job</th>
                        <th className="px-3 py-2 font-medium">Line</th>
                        <th className="px-3 py-2 font-medium">AI value (before)</th>
                        <th className="px-3 py-2 font-medium">New prompt reads</th>
                        <th className="px-3 py-2 font-medium">Operator saved</th>
                      </tr>
                    </thead>
                    <tbody>
                      {testResult.lines.map((l, i) => (
                        <tr key={i} className="border-t border-slate-100 align-top dark:border-slate-800">
                          <td className="whitespace-nowrap px-3 py-2 font-mono">
                            <Link to={`/jobs/${l.job_id}`} target="_blank" className="text-indigo-600 hover:underline dark:text-indigo-300">{l.job_reference}</Link>
                          </td>
                          <td className="px-3 py-2 text-slate-500">{l.row_index ?? "—"}</td>
                          <td className="px-3 py-2 font-mono break-all">{l.old_matches ? "✓ " : "✗ "}{l.ai_value || <i className="text-slate-400">blank</i>}</td>
                          <td className="px-3 py-2 font-mono break-all">
                            {l.error ? <span className="font-sans text-rose-600">{l.error}</span>
                              : <span className={l.new_matches ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}>{l.new_matches ? "✓ " : "✗ "}{l.new_value || <i>blank</i>}</span>}
                          </td>
                          <td className="px-3 py-2 font-mono break-all">{l.corrected_value || <i className="text-slate-400">blank</i>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-slate-500">Read-only: no job was changed. The field is read on its own here, so a line-item value can differ slightly from a full extraction.</p>
              </section>
            )}

            {info.history.length > 0 && (
              <section className="grid gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">History of this prompt</h3>
                <ol className="grid gap-2">
                  {info.history.map((h) => (
                    <li key={h.id} className={`flex flex-wrap items-start justify-between gap-2 rounded-lg border-l-4 bg-slate-50 p-3 text-xs dark:bg-slate-800/40 ${h.action === "revert" ? "border-amber-400" : "border-indigo-400"}`}>
                      <div className="grid min-w-0 flex-1 gap-1">
                        <span className="text-slate-500">{formatDateTime(h.at)} · {h.by ?? "—"} · {h.action === "revert" ? "Reverted" : "Applied"}</span>
                        <details>
                          <summary className="cursor-pointer text-indigo-600 dark:text-indigo-300">Show before / after</summary>
                          <div className="mt-2 grid gap-2 md:grid-cols-2">
                            <pre className={`${READ} whitespace-pre-wrap`}>{h.old_prompt}{h.old_format_hint ? `\n\nFormat hint: ${h.old_format_hint}` : ""}</pre>
                            <pre className={`${READ} whitespace-pre-wrap`}>{h.new_prompt}{h.new_format_hint ? `\n\nFormat hint: ${h.new_format_hint}` : ""}</pre>
                          </div>
                        </details>
                      </div>
                      <Button size="sm" variant="ghost" onClick={() => runRevert(h.id)}>Revert</Button>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
