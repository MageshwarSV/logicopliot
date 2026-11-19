import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import * as jobsApi from "../../api/jobs";
import type { JobDetail } from "../../types/jobs";

/** A job held as "Waiting for Password": a zip in its mail is password-protected and no saved
 *  password opened it, so NOTHING has been read yet. The operator enters the password (and may
 *  save it for this customer), or reads the job without the locked zips. Either way the whole
 *  mail is read once, into this job, in the background - this panel then watches for the end. */
export function HeldPasswordPanel({
  job,
  readOnly,
  onJob,
}: {
  job: JobDetail;
  readOnly: boolean;
  onJob: (job: JobDetail) => void;
}) {
  const locked = job.attachment_notes?.locked ?? [];
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [attachments, setAttachments] = useState<jobsApi.PrealertAttachment[] | null>(null);
  const shortPath = (p: string) => p.split("/").slice(-3).join(" / ");

  // After the password: keep re-reading the job until it is no longer being read.
  useEffect(() => {
    if (!reading) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const started = Date.now();
    async function tick() {
      try {
        const j = await jobsApi.getJob(job.id);
        if (stop) return;
        if (j.stage !== "Document Classifying" && j.outer_status !== "Document Classifying") {
          onJob(j);
          setReading(false);
          return;
        }
      } catch {
        /* the next tick will try again */
      }
      if (!stop && Date.now() - started < 45 * 60 * 1000) timer = setTimeout(tick, 5000);
    }
    timer = setTimeout(tick, 5000);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [reading, job.id, onJob]);

  async function submit(e: FormEvent, skip = false) {
    e.preventDefault();
    if (!skip && !password.trim()) {
      setError("Enter the zip password.");
      return;
    }
    if (skip && !window.confirm("Read this job without the password-protected zip(s)? Their files will not be in the job.")) return;
    setBusy(true);
    setError(null);
    try {
      const j = await jobsApi.unlockHeldJob(job.id, skip
        ? { skip_locked: true }
        : { password: password.trim(), remember, label: label.trim() || undefined });
      onJob(j);
      setPassword("");
      setReading(true);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not unlock it." : "Could not unlock it.");
    } finally {
      setBusy(false);
    }
  }

  async function showAttachments() {
    try {
      setAttachments((await jobsApi.getPrealert(job.id)).attachments ?? []);
    } catch {
      setAttachments([]);
    }
  }

  async function download(a: jobsApi.PrealertAttachment) {
    const url = await jobsApi.prealertAttachmentUrl(job.id, a.stored_as);
    const link = document.createElement("a");
    link.href = url;
    link.download = a.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  if (reading) {
    return (
      <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200">
        Reading all the documents now - this page updates by itself when it is done.
      </div>
    );
  }
  return (
    <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      <div className="font-medium">🔒 Waiting for the zip password - nothing in this job has been read yet</div>
      <ul className="mt-1 space-y-1 text-xs">
        {locked.map((l, i) => (
          <li key={i}>
            <span className="break-all font-medium">{shortPath(l.path)}</span>
            <details className="inline">
              <summary className="ml-1 inline cursor-pointer">({l.files.length} file{l.files.length === 1 ? "" : "s"} inside)</summary>
              <ul className="mt-1 list-disc pl-5">
                {l.files.map((f, k) => <li key={k} className="break-all">{f}</li>)}
              </ul>
            </details>
          </li>
        ))}
      </ul>
      {!readOnly && (
        <form onSubmit={(e) => submit(e)} className="mt-3 flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Zip password"
              autoComplete="off"
              className="min-w-[200px] flex-1 rounded-md border border-rose-300 bg-white px-2 py-1 text-sm text-slate-800 dark:border-rose-500/40 dark:bg-slate-900 dark:text-slate-100"
            />
            <button type="submit" disabled={busy} className="rounded-md bg-rose-600 px-3 py-1 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50">
              {busy ? "Checking…" : "Unlock and read"}
            </button>
          </div>
          <label className="flex flex-wrap items-center gap-2 text-xs">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Remember this password for this customer
            {remember && (
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label, e.g. Flex"
                className="rounded-md border border-rose-300 bg-white px-2 py-0.5 text-xs text-slate-800 dark:border-rose-500/40 dark:bg-slate-900 dark:text-slate-100"
              />
            )}
          </label>
          {error && <p className="text-xs font-medium text-rose-700 dark:text-rose-300">{error}</p>}
          <div className="flex flex-wrap gap-3 text-xs">
            <button type="button" onClick={(e) => submit(e as unknown as FormEvent, true)} disabled={busy} className="underline">
              Process without locked files
            </button>
            <button type="button" onClick={showAttachments} className="underline">
              Download the attached zip
            </button>
          </div>
          {attachments && (
            <ul className="text-xs">
              {attachments.length === 0 && <li>No attachment kept for this mail.</li>}
              {attachments.map((a) => (
                <li key={a.stored_as}>
                  <button type="button" onClick={() => download(a)} className="underline">{a.name}</button>
                </li>
              ))}
            </ul>
          )}
        </form>
      )}
    </div>
  );
}
