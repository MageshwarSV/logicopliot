import { useEffect, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/extractionCorrections";
import type { CorrectedField, FixInfo, FixSuggestion, FixTestResult } from "../../api/extractionCorrections";

const AREA = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs leading-relaxed dark:border-slate-700 dark:bg-slate-900";
const READ = "w-full rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 font-mono text-xs leading-relaxed text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-300";

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
        <p className="text-xs text-slate-500">
          {field.template} · {field.document} · corrected on {field.jobs_corrected} job(s)
          {info && <> · runs on <span className="font-mono">{info.model}</span>{info.reads_images ? " (reads page images)" : ""}</>}
        </p>

        {loadError && <p className="rounded-lg bg-rose-50 p-3 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{loadError}</p>}
        {info && !info.editable && (
          <p className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
            This field has no prompt to fix. {info.reason}
          </p>
        )}

        {info?.editable && (
          <>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid min-w-0 content-start gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Current prompt</h3>
                <textarea id="current-prompt" readOnly value={info.prompt} rows={14} className={READ} />
                {info.has_format_hint && (
                  <label className="grid gap-1 text-xs text-slate-500">Current format hint
                    <textarea id="current-hint" readOnly value={info.format_hint ?? ""} rows={2} className={READ} />
                  </label>
                )}
              </div>
              <div className="grid min-w-0 content-start gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-300">New prompt</h3>
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

            <div className="flex flex-wrap items-center gap-2">
              <Button variant="secondary" onClick={runSuggest} isLoading={suggesting}>AI Suggestion</Button>
              <Button variant="secondary" onClick={runTest} isLoading={testing} disabled={!info.test_supported || !prompt.trim()}
                title={info.test_supported ? "Read this field again with the New prompt on the latest corrected jobs" : "Not available for a computed field with one value per line"}>
                Test new prompt
              </Button>
              <span className="flex-1" />
              <Button onClick={runApply} isLoading={applying} disabled={!changed || !prompt.trim()}>Apply Changes</Button>
            </div>
            {(suggesting || testing) && (
              <p className="text-xs text-slate-500">{suggesting ? "Reading the corrections and pages…" : "Re-reading the latest corrected jobs…"} This can take up to a minute.</p>
            )}
            {message && (
              <p className={`rounded-lg p-3 text-sm ${message.kind === "ok" ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>{message.text}</p>
            )}

            {suggestion && (
              <section className="grid gap-2 rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 dark:border-indigo-500/30 dark:bg-indigo-500/5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">AI suggestion (not saved)</h3>
                  <span className="text-xs text-slate-500">{suggestion.corrections_used} correction(s), {suggestion.images_used} page image(s) · {suggestion.model}</span>
                </div>
                {suggestion.analysis && <p className="text-sm text-slate-700 dark:text-slate-200">{suggestion.analysis}</p>}
                <textarea id="suggested-prompt" value={suggestPrompt} onChange={(e) => setSuggestPrompt(e.target.value)} rows={10} className={AREA} />
                {info.has_format_hint && (
                  <label className="grid gap-1 text-xs text-slate-500">Suggested format hint
                    <textarea id="suggested-hint" value={suggestHint} onChange={(e) => setSuggestHint(e.target.value)} rows={2} className={AREA} />
                  </label>
                )}
                <div>
                  <Button size="sm" variant="secondary" onClick={() => { setPrompt(suggestPrompt); if (info.has_format_hint) setHint(suggestHint); }}>
                    Copy into New prompt
                  </Button>
                </div>
              </section>
            )}

            {testResult && (
              <section className="grid gap-2">
                <div className="flex flex-wrap items-baseline gap-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Test on the latest corrected jobs</h3>
                  <span className="text-sm">
                    Current prompt matched the operator on <b>{testResult.old_correct}</b> of {testResult.total} ·
                    New prompt: <b className={testResult.new_correct >= testResult.old_correct ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}>{testResult.new_correct}</b> of {testResult.total}
                  </span>
                </div>
                <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/50">
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
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Changes to this prompt</h3>
                <ol className="grid gap-2">
                  {info.history.map((h) => (
                    <li key={h.id} className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-800">
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
