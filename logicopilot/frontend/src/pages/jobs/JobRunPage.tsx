import { useEffect, useMemo, useRef, useState } from "react";
import { ModeIcon } from "../../components/ModeIcon";
import { Link, useNavigate, useParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { Input } from "../../components/ui/Input";
import { Modal } from "../../components/ui/Modal";
import * as jobsApi from "../../api/jobs";
import { useAuth } from "../../auth/useAuth";
import type { JobDetail, JobDocument, JobEvent, JobFieldValue } from "../../types/jobs";

// The operator's stages, as the customs desk actually works through a job. The backend still
// tracks four (Documents / Verification / ERP Entry / Completed) — these seven are how the
// work is DIVIDED on screen, and several of them share one backend stage. Keeping the two
// apart means the job list, the history and the submit gate are untouched by this.
// "dump" and "manual" used to be two more steps here (Dump Data, Manual Data Entry) — their
// content now renders directly on "extraction" (Required Details Review), below the
// extracted values, instead of on their own screens. See DumpDataView and the Manual Data
// Entry section rendered under the "extraction" tab further down.
type TabKey =
  | "capture"
  | "extraction"
  | "validation"
  | "irn"
  | "submission";

// `label` is the internal stage word - matched against the backend's _job_stage() answer
// above, and sent verbatim to recordStage() (see goNext() below), which stores it in
// job_events.stage. It must never change: existing jobs already have these exact strings in
// their history, and changing it would both break matching for them and start writing a
// different value going forward. `displayLabel` is the ONLY thing shown on screen - the rail
// button, the blocker text, the "Next: X" prompts - so the two can read differently (e.g.
// "Data Extraction" internally, "Required Details Review" on screen) without touching what
// is actually stored or compared.
const STEPS: { key: TabKey; label: string; displayLabel: string; built: boolean }[] = [
  { key: "capture", label: "Document Capture", displayLabel: "Document Capture", built: true },
  { key: "extraction", label: "Data Extraction", displayLabel: "Required Details Review", built: true },
  { key: "validation", label: "Data Validation", displayLabel: "Cross Docs Verification", built: true },
  { key: "irn", label: "IRN Processing", displayLabel: "IRN Processing", built: true },
  { key: "submission", label: "ERP Submission", displayLabel: "ERP Submission", built: true },
];

// The sidebar's document checklist under "Required Details Review" gets one extra row after
// every real document, opening the fields the system asks a person for (internally still the
// Manual Data Entry fields — nothing about how a value is asked or saved changed) instead of a
// document's extracted values. A plain string, not a document id, so it can never collide with
// a real uploaded file's UUID.
const ADDITIONAL_DETAILS_ID = "additional-details";

/** Which of the four steps this job is on.
 *
 *  The SERVER already works this out - it is the badge in the jobs list - so use its answer
 *  rather than a second rule that disagrees with it. The old rule here only knew "completed"
 *  and "extracted": every other status fell through to 1, so a job whose ERP entry FAILED
 *  opened on the Documents tab, as if nothing had happened, instead of on ERP Entry where the
 *  failure and the fields to correct actually are. `status` is only a fallback for the moment
 *  before the job has loaded its stage.
 */
/** Which stage to open a job on, from the stage the server reports.
 *
 * The server now speaks the same seven words the rail does, so most stages map straight
 * across. The rest are outcomes rather than places — a failed or running job belongs on ERP
 * Submission, which is where the outcome is shown.
 */
function tabFromStage(job: JobDetail, isGk2: boolean): TabKey {
  const { stage, status, documents } = job;
  // AI processing just finished, but nobody has looked at a single document yet — the
  // backend's own stage computation defaults straight to "Data Validation" the moment there
  // is no blocking cross-check, well before anything on Data Extraction has actually been
  // approved. Trust the real per-document gate here instead of that coarser label, so a
  // freshly-extracted job opens where there is still something to do, not past it. GK2
  // checks their OWN gk2_approved column — GK1 having approved everything already does not
  // mean GK2 has looked at a single document yet.
  if (
    status === "extracted" &&
    documents.filter((d) => d.is_uploaded).some((d) => !(isGk2 ? d.gk2_approved : d.approved))
  ) {
    return "extraction";
  }
  const direct = STEPS.find((s) => s.label === stage);
  if (direct) return direct.key;
  switch (stage) {
    case "Completed":
    case "Duplicate":
    case "Failed":
    case "Running":
      return "submission";
    // A job whose LAST recorded move (before this merge) was into one of these two now-gone
    // stages re-opens on "extraction" — that is exactly where their content lives today.
    case "Dump Data":
    case "Manual Data Entry":
      return "extraction";
  }
  if (status === "completed" || status === "duplicate" || status === "failed") return "submission";
  if (status === "extracted" || status === "processing") return "validation";
  return "capture";
}

export function JobRunPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const readOnly = user?.role === "tenant_admin" || user?.role === "super_admin" || user?.role === "manager";
  // Correcting a value is separated from the rest of readOnly. An admin watching a job should
  // not upload documents, accept its cross-checks or submit its entry — those belong to the
  // operator the job is assigned to. But a Super Admin who can rewrite the whole template was
  // barred from fixing one misread value, which is the smallest and most obvious repair there
  // is, and the person most likely to spot it. A Tenant Admin stays observing. Manager is
  // strictly read-only, no exception — unlike Super Admin, never gets canEditValues.
  const canEditValues = (!readOnly || user?.role === "super_admin") && user?.role !== "manager";
  // The step-by-step replay log, the ERP's internal URL and the progress bar are diagnostics
  // for whoever RECORDED the script — step numbers, selector names and timings mean nothing
  // to an operator, and the final-page URL exposes the ERP's internals. The operator is told
  // the outcome and shown the screenshot; the machinery behind it belongs to the Super Admin.
  const showsMachinery = user?.role === "super_admin";

  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("capture");
  // Why Next was refused. Shown as a popup rather than an inline banner, because the
  // operator has just pressed a button and expects an answer to THAT press.
  // The job's own history, for the header: when it last moved and to what. Read from the
  // recorded events rather than updated_at, so the header can say WHAT happened and not
  // merely that something did.
  const [history, setHistory] = useState<JobEvent[]>([]);
  const [blocker, setBlocker] = useState<
    { key: TabKey; label: string; displayLabel: string; why: string } | null
  >(null);
  // Asked fields that already carry a value nobody has confirmed — the duty notifications
  // arrive pre-filled, so "unanswered" and "empty" are no longer the same thing.
  const [approveOpen, setApproveOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [validationApproving, setValidationApproving] = useState(false);
  // "Hold" dismisses the popup for THIS visit only — the job stays possible_duplicate, so
  // leaving and reopening it shows the popup again until someone actually decides.
  const [dupDismissed, setDupDismissed] = useState(false);
  const [dupBusy, setDupBusy] = useState(false);
  const [dupError, setDupError] = useState<string | null>(null);
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [gk2Busy, setGk2Busy] = useState(false);
  // GK2's own "Approve & Proceed" press on IRN Processing - a dummy stand-in gate, like Data
  // Validation's "Approved and Proceed", since nothing real runs here yet for GK2. Kept
  // client-side (not persisted): this step has no real backend state of its own for GK2, and
  // it resets on a fresh page load, which is fine for a confirmation step rather than a fact
  // about data. GK1's own IRN stage (IRN Documents Upload) is now real, persisted state - see
  // job.irn_documents_done below.
  const [irnApproved, setIrnApproved] = useState(false);
  const [supportingDocs, setSupportingDocs] = useState<jobsApi.SupportingDocument[]>([]);
  const [prealert, setPrealert] = useState<jobsApi.Prealert | null>(null);
  const [irnLabel, setIrnLabel] = useState("");
  const [irnFiles, setIrnFiles] = useState<File[]>([]);
  const [irnUploading, setIrnUploading] = useState(false);
  const [irnError, setIrnError] = useState<string | null>(null);
  const [irnSkipping, setIrnSkipping] = useState(false);
  const [irnApproving, setIrnApproving] = useState(false);
  const [excelBusy, setExcelBusy] = useState(false);
  const [excelError, setExcelError] = useState<string | null>(null);
  const [smartBusy, setSmartBusy] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  // Set when Submit is pressed with something still empty. The button used to be disabled
  // instead, which says "you cannot" without ever saying WHAT is missing.
  // The first empty box, so Submit can scroll straight to it instead of leaving the operator
  // to hunt down a long invoice for whichever one it means.
  // What the ERP entry is doing RIGHT NOW. play_steps photographs the page as it goes; a job
  // run never asked for those frames, so a running entry was invisible until it finished.
  const [live, setLive] = useState<jobsApi.LiveRun | null>(null);
  // How far the entry got. Kept apart from `live`, which is deliberately null unless the job
  // is running - the progress has to outlive the run, or a job opened tomorrow says nothing
  // about how far it reached. The server works it out (from the live frame while running, from
  // the saved log afterwards) so the rule lives in one place, not two.
  const [progress, setProgress] = useState<jobsApi.RunProgress | null>(null);
  // Pressing Submit with something unfilled used to paint a red banner naming the fields and
  // leave the operator to find them. On a fourteen-line invoice that is a hunt. Instead the
  // fields are asked for one at a time, in a popup, with a count of how many are left.
  const [fixOpen, setFixOpen] = useState(false);
  const [fixAt, setFixAt] = useState(0);
  const [fixDraft, setFixDraft] = useState("");
  const [fixSaving, setFixSaving] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);
  const [ruling, setRuling] = useState<jobsApi.RulingResult | null>(null);
  const [rulingBusy, setRulingBusy] = useState(false);
  const [rulingAnswer, setRulingAnswer] = useState("");
  const [smartResult, setSmartResult] = useState<{ filename: string; matched: string[] | null; error?: string }[] | null>(null);
  const didInit = useRef(false);

  async function load() {
    try {
      const j = await jobsApi.getJob(jobId);
      setJob(j);
      if (!didInit.current) {
        didInit.current = true;
        setActiveTab(tabFromStage(j, user?.role === "gk2"));
      }
    } catch {
      setError("Failed to load the job.");
    }
  }

  useEffect(() => {
    load();
    jobsApi
      .jobHistory(jobId)
      .then(setHistory)
      .catch(() => {
        /* the header falls back to the job's own timestamp */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Loaded only once the IRN tab is actually opened - a job's supporting documents and its
  // supportingDocs are not needed anywhere else on the page.
  useEffect(() => {
    if (activeTab !== "irn") return;
    jobsApi
      .listSupportingDocuments(jobId)
      .then(setSupportingDocs)
      .catch(() => setSupportingDocs([]));
  }, [activeTab, jobId]);

  // Prealert is loaded once, unconditionally - shown on Document Capture (the job's first
  // stage, so an operator sees what actually arrived before touching anything) AND on IRN
  // Documents Upload, not gated to either tab specifically.
  useEffect(() => {
    jobsApi
      .getPrealert(jobId)
      .then(setPrealert)
      .catch(() => setPrealert({ available: false }));
  }, [jobId]);

  async function handleIrnUpload() {
    if (!irnLabel.trim() || irnFiles.length === 0) return;
    setIrnUploading(true);
    setIrnError(null);
    try {
      const doc = await jobsApi.uploadSupportingDocuments(jobId, irnLabel.trim(), irnFiles);
      setSupportingDocs((prev) => [...prev, doc]);
      setIrnLabel("");
      setIrnFiles([]);
      const j = await jobsApi.getJob(jobId);
      setJob(j);
    } catch {
      setIrnError("Could not upload that document. Try again.");
    } finally {
      setIrnUploading(false);
    }
  }

  async function handleIrnDelete(docId: string) {
    try {
      await jobsApi.deleteSupportingDocument(jobId, docId);
      setSupportingDocs((prev) => prev.filter((d) => d.id !== docId));
    } catch {
      setIrnError("Could not remove that document. Try again.");
    }
  }

  async function handleIrnSkip() {
    setIrnSkipping(true);
    setIrnError(null);
    try {
      await jobsApi.skipIrnDocuments(jobId);
      const j = await jobsApi.getJob(jobId);
      setJob(j);
    } catch {
      setIrnError("Could not mark this stage complete. Try again.");
    } finally {
      setIrnSkipping(false);
    }
  }

  async function handleIrnApprove() {
    setIrnApproving(true);
    setIrnError(null);
    try {
      await jobsApi.approveIrnDocuments(jobId);
      const j = await jobsApi.getJob(jobId);
      setJob(j);
    } catch {
      setIrnError("Could not mark this stage complete. Try again.");
    } finally {
      setIrnApproving(false);
    }
  }

  async function openSupportingDocumentFile(docId: string, storedAs: string, name: string) {
    try {
      const url = await jobsApi.supportingDocumentFileUrl(jobId, docId, storedAs);
      window.open(url, "_blank", "noopener");
    } catch {
      setIrnError(`Could not open ${name}.`);
    }
  }

  async function openPrealertOriginal() {
    try {
      const url = await jobsApi.prealertOriginalUrl(jobId);
      window.open(url, "_blank", "noopener");
    } catch {
      setIrnError("Could not open the original email.");
    }
  }

  async function openPrealertAttachment(storedAs: string, name: string) {
    try {
      const url = await jobsApi.prealertAttachmentUrl(jobId, storedAs);
      window.open(url, "_blank", "noopener");
    } catch {
      setIrnError(`Could not open ${name}.`);
    }
  }

  // Poll only while it is actually running, and stop the moment it is not: an idle job page
  // must not sit there asking the server for a screenshot every second forever.
  useEffect(() => {
    // NO JOB YET. This runs on the very first render, before the job has loaded - so there is
    // nothing to ask about and, more to the point, nothing to take an id from. Asking anyway
    // threw on `job.id` and took the whole page down to a blank screen. TypeScript did not
    // catch it because the call said `job!.id`: the `!` promises the compiler that job is not
    // null, inside a branch entered precisely because it might be.
    if (!job) {
      setLive(null);
      return;
    }
    if (job.status !== "processing") {
      setLive(null);
      // Not running: ask ONCE for how far it got, and do not poll. The answer comes from the
      // log the run saved, so it is as true tomorrow as it is now.
      let dropped = false;
      jobsApi
        .jobLive(job.id)
        .then((f) => {
          if (!dropped) setProgress(f.progress ?? null);
        })
        .catch(() => {
          /* no progress to show is not an error worth a banner */
        });
      return () => {
        dropped = true;
      };
    }
    // Taken once, here, where the guards above have already proved there IS a job. Nothing
    // below needs a `!` to promise the compiler something the code has not established - which
    // is exactly the promise that put a blank page in front of the operator.
    const runningJobId = job.id;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const f = await jobsApi.jobLive(runningJobId);
        if (stop) return;
        setLive(f);
        setProgress(f.progress ?? null);
        if (!f.live) {
          // the run has finished - pull the job again so its outcome replaces the live view
          load();
          return;
        }
      } catch {
        /* a missed frame is not worth showing an error for; the next one will do */
      }
      if (!stop) timer = setTimeout(tick, 1500);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);

  // Extraction now starts on its own — the moment every slot is filled, or on a manual
  // (Re-run) press — and runs in the background, so there is nothing here to watch a live
  // feed for. Just keep reloading the job until its status moves off "extracting" and the
  // "AI Processing" badge gives way to whatever the extraction actually decided.
  useEffect(() => {
    if (!job || job.status !== "extracting") return;
    const jobId2 = job.id;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const j = await jobsApi.getJob(jobId2);
        if (stop) return;
        setJob(j);
        if (j.status === "extracting") timer = setTimeout(tick, 1500);
      } catch {
        if (!stop) timer = setTimeout(tick, 1500);
      }
    }
    timer = setTimeout(tick, 1500);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);

  /**
   * Upload one or more files into a slot. A shipment covered by three invoices is three
   * files in the Invoice slot, so the picker takes several at once and they are sent one
   * after another — each response carries the whole job back, and sending them in parallel
   * would let the last reply win and hide the others.
   */
  async function handleUpload(
    templateDocumentId: string,
    picked: File | null | undefined | FileList,
  ) {
    const files = !picked ? [] : picked instanceof File ? [picked] : Array.from(picked);
    if (!files.length) return;
    if (files.some((f) => !/\.(pdf|png|jpe?g)$/i.test(f.name))) {
      setError("Only PDF, PNG, or JPG files are allowed.");
      return;
    }
    setError(null);
    setUploadingId(templateDocumentId);
    try {
      for (const f of files) {
        setJob(await jobsApi.uploadJobDocument(jobId, templateDocumentId, f));
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Upload failed.");
    } finally {
      setUploadingId(null);
    }
  }

  async function handleRemoveFile(jobDocumentId: string) {
    setError(null);
    setUploadingId(jobDocumentId);
    try {
      setJob(await jobsApi.deleteJobDocumentFile(jobId, jobDocumentId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not remove that file.");
    } finally {
      setUploadingId(null);
    }
  }

  async function handleSmartUpload(fileList: FileList | null | undefined) {
    if (!fileList || fileList.length === 0) return;
    setSmartBusy(true);
    setError(null);
    setSmartResult(null);
    try {
      const res = await jobsApi.smartUpload(jobId, Array.from(fileList));
      setJob(res.detail);
      setSmartResult(res.results);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Smart upload failed.");
    } finally {
      setSmartBusy(false);
    }
  }

  async function runExtract() {
    // The request now returns as soon as extraction has STARTED, not once it is done — the job
    // comes back with status "extracting", and the poll effect above takes it from there. Move
    // to Data Extraction so the operator lands where the "AI Processing" progress actually
    // shows, rather than staying on Document Capture wondering if the press did anything.
    setExtracting(true);
    setError(null);
    try {
      const j = await jobsApi.extractJob(jobId);
      setJob(j);
      setActiveTab("extraction");
      // A fresh read means a fresh review of everything downstream too. The backend already
      // clears the document/validation approvals this depends on; IRN Processing's own
      // "Approved" state lives only here in the browser, so nothing else would ever clear it
      // — without this, a job re-run after already reaching IRN Processing kept showing it as
      // approved, letting the operator skip straight to Final Submit without pressing it again.
      setIrnApproved(false);
      setActiveFileId(null);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Extraction failed.");
    } finally {
      setExtracting(false);
    }
  }

  async function decideVerification(linkId: string, accept: boolean) {
    setError(null);
    try {
      setJob(await jobsApi.setVerificationDecision(jobId, linkId, accept));
    } catch {
      setError("Could not update that check.");
    }
  }

  async function checkRuling(answer?: string) {
    setRulingBusy(true);
    setError(null);
    try {
      const res = await jobsApi.evaluateRuling(jobId, answer);
      setRuling(res);
      if (!res.needs_input) setRulingAnswer("");
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not evaluate the ruling.");
    } finally {
      setRulingBusy(false);
    }
  }

  /** Clear this job's failed run and stand it ready to enter again. Nothing is lost.
   *
   *  This used to restart from the first stage - deleting every extracted value and every
   *  verification approval - so correcting one rejected field and pressing it threw away all
   *  the other work. No confirmation is asked for any more, because there is nothing to lose:
   *  the values stay exactly as they are and the job simply becomes runnable again. It lands
   *  on ERP Entry, which is where the operator was working.
   */
  async function rerunJob() {
    setRerunning(true);
    setError(null);
    try {
      const j = await jobsApi.rerunJob(jobId);
      setJob(j);
      setActiveTab("submission");
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not make the job ready to run.");
    } finally {
      setRerunning(false);
    }
  }

  async function submitEntry() {
    setSubmitting(true);
    setError(null);
    try {
      const j = await jobsApi.completeJob(jobId);
      setJob(j);
      // Only advance to Completed if the ERP entry actually went through.
      if (j.status === "completed") setActiveTab("submission");
      else if (j.erp_status === "error" || j.erp_status === "failed")
        // Both retries keep the extracted data now: "Run failed job again" submits straight
        // away, "Rerun job" just clears the failure and stands it ready.
        setError(
          j.erp_failed_field
            ? `The ERP rejected “${j.erp_failed_field}”. It's outlined in red below — correct it, press Save, then Run failed job again.`
            : "The ERP entry failed. Check the details below, correct anything wrong, then Run failed job again.",
        );
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not submit the entry.");
    } finally {
      setSubmitting(false);
    }
  }

  /** GK1 (operator) presses "Final Submit for Approval" — hands the job to GK2 instead of
   *  running the real ERP entry (not wired up yet, see Job.gk2_status on the backend). */
  async function submitForGk2Approval() {
    setGk2Busy(true);
    setError(null);
    try {
      setJob(await jobsApi.submitForGk2Approval(jobId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not submit this for GK2 approval.");
    } finally {
      setGk2Busy(false);
    }
  }

  /** GK2 presses "Final Approve & Proceed" — the server moves the job to "preparing_erp"
   *  ("AI - Preparing for ERP") right away, builds the import workbook, then to "entering_erp"
   *  ("ERP Entry Process Started") once it actually starts replaying the ERP script for real -
   *  a real browser run (login, ~30 recorded steps, upload, submit) that can take well over a
   *  minute, nothing like the old placeholder's fixed few seconds. Poll through BOTH of those
   *  non-final states; stop only once gk2_status lands on "submitted" or "failed". */
  async function gk2ApproveAndProceed() {
    setGk2Busy(true);
    setError(null);
    try {
      setJob(await jobsApi.gk2Approve(jobId));
      // Up to 60 tries, 3s apart (3 minutes) — comfortably past a real script run.
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const fresh = await jobsApi.getJob(jobId);
        setJob(fresh);
        if (fresh.gk2_status !== "preparing_erp" && fresh.gk2_status !== "entering_erp") break;
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not record the GK2 approval.");
    } finally {
      setGk2Busy(false);
    }
  }

  /** "Download as Excel" — GK1's Final Submit for Approval and GK2's Final Approve & Proceed
   *  both offer this for an Excel-entry job: the same workbook the real submission builds,
   *  rebuilt fresh from the job's current data every time it's pressed. */
  async function downloadExcel() {
    setExcelBusy(true);
    setExcelError(null);
    try {
      const { url, filename } = await jobsApi.downloadJobExcelUrl(jobId);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setExcelError(err.response?.data?.detail ?? "Could not build the Excel file for this job.");
      }
    } finally {
      setExcelBusy(false);
    }
  }

  /** Beside every document header on the IRN tab, once GK1 has chosen Approval for IRN (not
   *  Skip) - a placeholder for a feature still being designed ("later I will tell the
   *  concept"), so it is deliberately inert for now: no click handler, no backend call. Shown
   *  to GK1 (as soon as they choose Approval) and GK2 alike, since both read the same
   *  persisted job.irn_approval_requested rather than anything role-specific. When GK1 chose
   *  Skip instead, this same spot just says so - nothing to place a placeholder beside. */
  function renderIrnPlaceholder() {
    if (!job) return null;
    if (job.irn_approval_requested) {
      return (
        <div className="mt-1 flex items-center gap-2">
          <span className="text-xs text-slate-400">IRN: —</span>
          <Button variant="secondary" disabled className="!px-2 !py-1 !text-xs">
            Get DSC + IRN Number
          </Button>
        </div>
      );
    }
    if (job.irn_documents_done) {
      return <p className="mt-1 text-xs text-slate-400">Skipped</p>;
    }
    return null;
  }

  /** The original email this job was created from, if it was created from one at all - shown
   *  on both Document Capture (so an operator sees what actually arrived, before touching
   *  anything) and IRN Documents Upload. A job started any other way (a manual upload, an
   *  Excel entry) has no email to show automatically, so the IRN tab instead lists whatever
   *  was captured on Document Capture - same header either way, so this card is never a hidden
   *  inconsistency between what GK1 saw and what GK2 sees. */
  function renderPrealert() {
    return (
      <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-700">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Prealert
        </p>
        {prealert === null ? (
          <p className="mt-2 text-sm text-slate-400">Loading…</p>
        ) : !prealert.available ? (
          <div className="mt-2 space-y-3">
            <p className="text-sm text-slate-400">
              No original email is on file for this job.
            </p>
            {/* The documents already captured on Document Capture - for a job started by hand
                instead of arriving by email, these ARE the pre-alert documents, just uploaded
                directly rather than attached to a mail. Shown here too so GK1 and GK2 see them
                on the IRN tab without switching back to Document Capture to check. */}
            {docSlots.some((s) => s.files.some((f) => f.is_uploaded)) && (
              <div className="grid gap-3 sm:grid-cols-2">
                {docSlots.map(({ tdocId, name, doc_type, files }) => {
                  const uploaded = files.filter((f) => f.is_uploaded);
                  if (uploaded.length === 0) return null;
                  return (
                    <div key={tdocId}>
                      <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                        {name} <span className="text-slate-400">({doc_type})</span>
                      </p>
                      {uploaded.map((d) => (
                        <JobDocPreview key={d.id} jobId={jobId} docId={d.id} pages={d.page_count} />
                      ))}
                      {renderIrnPlaceholder()}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <div className="mt-2 space-y-2">
            <p className="text-sm text-slate-700 dark:text-slate-300">
              <span className="font-medium">{prealert.subject}</span>
              {prealert.sender ? ` — from ${prealert.sender}` : ""}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {prealert.has_original_eml && (
                <button
                  type="button"
                  onClick={openPrealertOriginal}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  Download original email
                </button>
              )}
              {(prealert.attachments ?? []).map((a) => (
                <button
                  key={a.stored_as}
                  type="button"
                  onClick={() => openPrealertAttachment(a.stored_as, a.name)}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  {/* Labelled by its own filename, the same way a document slot elsewhere on
                      this page shows "name (doc_type)" - an attachment carries no document
                      TYPE of its own (it hasn't been classified into a slot), so its original
                      filename is the label. */}
                  {a.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  /** The last ERP entry failed and the job is still submittable — so the operator can correct
   *  the rejected value and re-run just the entry, without re-extracting the documents. */
  const entryFailed =
    !!job && job.status === "extracted" &&
    (job.erp_status === "failed" || job.erp_status === "error");
  /**
   * The document slots, each with every file uploaded into it. A slot holds as many files as
   * the shipment has — three invoices is three files under one "Invoice" heading, not three
   * headings — so the list is grouped rather than rendered flat.
   */
  const docSlots = useMemo(() => {
    const order: string[] = [];
    const by: Record<string, { tdocId: string; name: string; doc_type: string; required: boolean; files: JobDocument[] }> = {};
    for (const d of job?.documents ?? []) {
      if (!by[d.template_document_id]) {
        by[d.template_document_id] = {
          tdocId: d.template_document_id, name: d.name, doc_type: d.doc_type,
          required: d.is_required !== false, files: [],
        };
        order.push(d.template_document_id);
      }
      by[d.template_document_id].files.push(d);
    }
    for (const k of order) by[k].files.sort((a, b) => a.file_index - b.file_index);
    return order.map((k) => by[k]);
  }, [job?.documents]);
  // Every uploaded FILE, flat (a slot with three invoices is three of these) — the rail's own
  // checklist under "Required Details Review" needs one row per file, in the template's own
  // order, same as ExtractionReview shows them. Lifted up here (rather than left inside that
  // component) so the sidebar and the content pane share ONE "which file is open" state
  // instead of the sidebar being unable to drive what the content pane shows.
  const reviewFiles = useMemo(
    () => (job?.documents ?? []).filter((d) => d.is_uploaded),
    [job?.documents],
  );
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  // The sidebar's own last row, sat below every document — same list, same click-to-open
  // pattern, just showing the fields the system asks a person for (never called "Manual Data
  // Entry" out loud; that name is internal only, this is what the operator sees) instead of
  // one document's extracted values.
  const isAdditionalDetailsActive = activeFileId === ADDITIONAL_DETAILS_ID;
  const activeFile = isAdditionalDetailsActive
    ? undefined
    : reviewFiles.find((f) => f.id === activeFileId) ?? reviewFiles[0];
  // GK2 re-reviews every document independently, on its own column — never GK1's.
  const isFileApproved = (d: JobDocument) => (user?.role === "gk2" ? d.gk2_approved : d.approved);
  // Ready when every MANDATORY slot has at least one file — an optional one is never waited
  // on. Counting files instead of slots would keep the job waiting forever once an extra
  // invoice added a row of its own.
  const allUploaded =
    docSlots.length > 0 && docSlots.every((s) => !s.required || s.files.some((f) => f.is_uploaded));
  // Fields the Super Admin ticked "ask the operator": each must be explicitly confirmed
  // (a saved corrected value) before the ERP entry may run.
  const askFields = job?.field_values.filter((fv) => fv.ask_operator) ?? [];
  // A field the system worked out itself (a custom field, computed rather than read verbatim
  // off one document) used to render duplicated on whichever document tab happened to list it
  // in its own (often default-to-every-document) source_document_ids, framed as "worked out
  // from THIS document" even when it was not read from that document at all. It belongs once,
  // here under Additional Details, common to the whole job - see the same exclusion
  // ExtractionReview's own isSelfVerifyingLabel used to apply for these fields.
  const isSelfVerifyingLabel = (label: string) => {
    const l = label.toLowerCase();
    return l.includes("verification") || l.trim().endsWith("(calculated)");
  };
  const commonComputedFields =
    job?.field_values.filter(
      (fv) => fv.origin === "computed" && fv.row_index == null && !isSelfVerifyingLabel(fv.label_name),
    ) ?? [];
  // What Manual Data Entry actually asks for. Reference-sheet fields are asked too — the CTH
  // and RITC are marked "ask the operator" so a part missing from the sheet gets typed — but
  // they have their own screen, Dump Data, which shows them and refuses to pass while one is
  // blank. Listing them here as well put the same box on two screens, and two editable copies
  // of one value is how an edit gets typed into the one nobody is reading.
  const manualFields = askFields.filter((fv) => fv.self_filled !== true);
  // Only mandatory ones hold up Submit Entry. Optional ones are still asked for and still
  // highlighted, but a value the operator does not have yet must not block the entry.
  // A field with a value the system worked out itself needs no confirming. The CTH comes
  // from the customer's own reference sheet, which is more authoritative than anything an
  // operator would retype - and requiring a "correction" on it blocked Submit while the
  // screen plainly showed the value. Same rule as the server's gate, or the button and the
  // server disagree about whether the job can go.
  // A field is answered once the operator has saved a value on it. A self-filling field —
  // the CTH and RITC, looked up in the customer's own reference sheet — counts as answered
  // on its own value, because that sheet is more authoritative than anything retyped.
  //
  // Anything else keeps needing a confirmation even when it arrives pre-filled. That matters
  // for a standing value that changes: the duty notification numbers start filled in, and the
  // operator confirming them is the step that catches the year one of them rolls over. This
  // is the server's rule exactly — the two used to differ, and a pre-filled field would have
  // shown Submit as ready while the server refused it. (An OPTIONAL field — ask_operator_
  // required false — is exempt from needing an answer at all; see the two filters below. A
  // MANDATORY per-row field genuinely does require real content, not just a saved blank — the
  // backend's own per-row gate agrees, unlike its job-level one, because "line 7's duty notn.
  // is blank" cannot become "line 7's duty notn. is confirmed blank" the way a shipment-level
  // field can.)
  const answered = (fv: JobFieldValue) =>
    (fv.corrected_value ?? "").trim() !== "" ||
    (fv.self_filled === true && (fv.value ?? "").trim() !== "");
  const askPending = askFields.filter(
    (fv) => !answered(fv) && fv.ask_operator_required !== false,
  );
  const manualPending = manualFields.filter(
    (fv) => !answered(fv) && fv.ask_operator_required !== false,
  );
  // Fields asked once per LINE ITEM (the CTH on each product line). They must be shown
  // numbered and next to the product they belong to, or the operator cannot tell which
  // value goes with which row.
  const perRowAsks = useMemo(() => {
    const rows = new Map<number, { fv: JobFieldValue; context: string }[]>();
    (job?.field_values ?? []).forEach((fv) => {
      // Same exclusion as manualFields: the CTH and RITC live on Dump Data, and showing
      // them here as well is the same value in two places.
      if (!fv.ask_operator || fv.row_index == null || fv.self_filled === true) return;
      const arr = rows.get(fv.row_index) ?? [];
      arr.push({ fv, context: "" });
      rows.set(fv.row_index, arr);
    });
    // Label each line with something the operator recognises — the product description of
    // that same row, falling back to any other extracted value on it.
    const describe = (n: number) => {
      const onRow = (job?.field_values ?? []).filter((f) => f.row_index === n && !f.ask_operator);
      const desc = onRow.find((f) => /descri/i.test(f.label_name) && (f.value ?? "").trim());
      return (desc ?? onRow.find((f) => (f.value ?? "").trim()))?.value ?? "";
    };
    return [...rows.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([n, items]) => ({
        line: n,
        // Always the same order on every line. Unsorted, the six boxes appeared in whatever
        // order the values happened to come back in - a different order on each row, so the
        // eye had to re-read every label instead of running straight down a column.
        items: items
          .map((i) => i.fv)
          .sort((a, b) => a.label_name.localeCompare(b.label_name)),
        context: describe(n),
      }));
  }, [job]);
  // Everything still unanswered, as ONE list. Job-level fields first - they describe the whole
  // shipment - then the line items in row order, each carrying the product it belongs to so the
  // popup can say WHICH line it is asking about.
  const fixQueue = useMemo(() => {
    const out: { fv: JobFieldValue; where: string }[] = [];
    askFields.forEach((fv) => {
      if (!answered(fv) && fv.ask_operator_required !== false) {
        out.push({ fv, where: "This shipment" });
      }
    });
    perRowAsks.forEach((row) => {
      row.items.forEach((fv) => {
        if (!answered(fv) && fv.ask_operator_required !== false) {
          out.push({
            fv,
            where: `Line ${row.line}${row.context ? ` — ${row.context}` : ""}`,
          });
        }
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.field_values]);

  const unresolved = job?.verifications.filter((v) => v.status !== "match" && !v.accepted).length ?? 0;

  if (!job) {
    return (
      <AppShell title="Job">{error ? <Alert>{error}</Alert> : <p className="text-sm text-slate-400">Loading…</p>}</AppShell>
    );
  }


  // Why this stage is not finished yet — null when it is. The wording is what the operator
  // is shown, so it names the actual thing to do rather than "incomplete".
  const blockedBecause = (key: TabKey): string | null => {
    switch (key) {
      case "capture": {
        const missing = docSlots.filter((s) => s.required && !s.files.some((f) => f.is_uploaded));
        if (missing.length) {
          return `Every mandatory document has to be uploaded first. Still missing: ${missing
            .map((s) => s.name)
            .join(", ")}.`;
        }
        return null;
      }
      case "extraction": {
        if (job.status === "draft" || job.status === "extracting") {
          return "The documents have not been read yet. Press Extract on Document Capture.";
        }
        const uploaded = job.documents.filter((d) => d.is_uploaded);
        // GK2 re-reviews every document independently — their own gk2_approved column,
        // never GK1's approved, so GK2 must press through this screen themselves too.
        const isGk2Tab = user?.role === "gk2";
        const unapproved = uploaded.filter((d) => !(isGk2Tab ? d.gk2_approved : d.approved));
        if (unapproved.length) {
          return `${unapproved.length} document${unapproved.length === 1 ? "" : "s"} still need${
            unapproved.length === 1 ? "s" : ""
          } "${isGk2Tab ? "Approve & Proceed" : "Submit for Approval"}": ${unapproved.map((d) => d.name).join(", ")}.`;
        }
        // Formerly Dump Data's own gate: a part the reference sheet does not carry is left
        // blank on purpose rather than guessed — and a Bill of Entry cannot go without a CTH.
        // Blank is the one thing that holds this up; the rest of that section is a view.
        const blank = job.field_values.filter(
          (fv) => fv.self_filled === true && !(fv.value ?? "").trim(),
        );
        if (blank.length) {
          return `${blank.length} value(s) were not found on the reference sheet and need typing: ${[
            ...new Set(blank.map((f) => f.label_name)),
          ].join(", ")}.`;
        }
        // Formerly Manual Data Entry's own gate.
        if (manualPending.length) {
          return `${manualPending.length} field(s) still need an answer: ${manualPending
            .map((f) => f.label_name)
            .join(", ")}.`;
        }
        return null;
      }
      case "validation":
        // A single gate: the reviewer's own "Approved and Proceed" press — GK1's and GK2's
        // are two separate columns (validation_approved / gk2_validation_approved), so GK2
        // reviewing a job GK1 already approved still has to press it themselves. That press
        // now accepts the data as-is even over an open Mismatch/Review flag — the reviewer is
        // the one deciding it's fine to move on, not the system silently deciding for them -
        // so nothing here re-checks the cross-checks themselves, only whether that press
        // has happened yet.
        return (user?.role === "gk2" ? job.gk2_validation_approved : job.validation_approved)
          ? null
          : "Press \"Approved and Proceed\" once the data has been reviewed.";
      // GK1's IRN Documents Upload is real, persisted state - done once a supporting document
      // has been uploaded, or the stage explicitly skipped (it is optional). GK2's IRN
      // Processing still has nothing real behind it - its own dummy "Approve" press is the
      // one thing that finishes it.
      case "irn":
        if (user?.role === "gk2") {
          return irnApproved ? null : "Press \"Approve & Proceed\" to mark it complete.";
        }
        return job.irn_documents_done
          ? null
          : "Upload IRN documents, or skip, to mark this stage complete.";
      case "submission":
        return job.status === "completed" ? null : "The entry has not been submitted yet.";
    }
    return null;
  };

  // A stage counts as finished only when IT and everything BEFORE it is finished.
  //
  // Checking a stage alone made a brand-new job show Data Validation, Dump Data and Manual
  // Data Entry as complete before a single document had been uploaded: each asks "is anything
  // outstanding?", and on an empty job the answer is no — there are no cross-checks to fail,
  // no looked-up values to be blank and no questions to answer. An empty list of problems is
  // not the same as no problems; it means nothing has happened yet. The chain is what tells
  // them apart.
  const finishedThrough = (key: TabKey): boolean => {
    for (const step of STEPS) {
      if (blockedBecause(step.key)) return false;
      if (step.key === key) return true;
    }
    return true;
  };

  // When the job last actually moved. The newest history row whose stage matches where the
  // job is now; failing that, the newest row at all.
  const lastMoved = (() => {
    const rows = [...history].sort((a, b) => (a.at < b.at ? 1 : -1));
    const hit = rows.find((e) => e.stage && e.stage === job.stage) ?? rows[0];
    if (!hit) return null;
    const d = new Date(hit.at);
    return Number.isNaN(d.getTime())
      ? null
      : d.toLocaleString(undefined, {
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        });
  })();

  const stageIndex = STEPS.findIndex((s) => s.key === activeTab);
  const nextStage = stageIndex >= 0 ? STEPS[stageIndex + 1] : undefined;

  // The "irn" step reads differently per role everywhere it is named on screen (the rail,
  // "Next: X", the blocker banner) - GK1's IRN Documents Upload vs GK2's unchanged IRN
  // Processing. One place for that instead of repeating the same ternary at each call site.
  const displayLabelFor = (key: TabKey): string =>
    key === "irn"
      ? user?.role === "gk2"
        ? "IRN Processing"
        : "IRN Documents Upload"
      : STEPS.find((s) => s.key === key)?.displayLabel ?? key;

  // Pre-filled but unconfirmed. Refusing to move on would be right if these were blank, but
  // they are not — the operator has read them and changed nothing, which IS an answer. So ask
  // for it once, rather than making them retype six values on every line to say "yes, fine".
  const unconfirmedButFilled =
    askPending.length > 0 && askPending.every((fv) => (fv.value ?? "").trim());

  const approveDuplicateNow = async () => {
    setDupBusy(true);
    setDupError(null);
    try {
      await jobsApi.approveDuplicate(jobId);
      await load();
    } catch {
      setDupError("Could not record the decision — try again.");
    } finally {
      setDupBusy(false);
    }
  };

  const deleteDuplicateNow = async () => {
    setDupBusy(true);
    setDupError(null);
    try {
      await jobsApi.deleteJob(jobId);
      navigate("/jobs");
    } catch {
      setDupError("Could not delete the job — try again.");
      setDupBusy(false);
    }
  };

  const approveValidationNow = async () => {
    setValidationApproving(true);
    try {
      await jobsApi.approveValidation(jobId);
      await load();
    } catch {
      setError("Could not record the approval — a cross-check may still be open.");
    } finally {
      setValidationApproving(false);
    }
  };

  const approveUnchanged = async () => {
    setApproving(true);
    try {
      // Confirming is saving the value that is already there — the same corrected_value the
      // submit gate looks for, so nothing downstream needs to know this happened in bulk.
      for (const fv of askPending) {
        await jobsApi.correctFieldValue(fv.id, (fv.value ?? "").trim());
      }
      await load();
      setApproveOpen(false);
      if (nextStage) setActiveTab(nextStage.key);
    } catch {
      setError("Could not record the approval.");
    } finally {
      setApproving(false);
    }
  };

  // The FIRST unfinished stage at or before the one on screen. Jumping ahead in the rail is
  // allowed — looking is not gated — but moving forward from there was refusing with the
  // wrong reason: it complained about the stage you were on while the real blocker was one
  // you had skipped past, and never said which.
  const firstUnfinished = (): { key: TabKey; label: string; displayLabel: string; why: string } | null => {
    for (const step of STEPS) {
      const why = blockedBecause(step.key);
      if (why) return { key: step.key, label: step.label, displayLabel: step.displayLabel, why };
      if (step.key === activeTab) break;
    }
    return null;
  };

  const goNext = () => {
    const blocker = firstUnfinished();
    if (blocker) {
      // Formerly Manual Data Entry's own shortcut: a pre-filled-but-unconfirmed ask field
      // doesn't need retyping, just a bulk confirm — but only when THAT is actually what is
      // blocking "extraction" now, not an unapproved document or a still-blank reference-
      // sheet lookup, which need their own real fix rather than a rubber stamp.
      const uploaded = job.documents.filter((d) => d.is_uploaded);
      const isGk2Tab = user?.role === "gk2";
      const docsApproved = uploaded.every((d) => (isGk2Tab ? d.gk2_approved : d.approved));
      const noSelfFilledBlanks = !job.field_values.some(
        (fv) => fv.self_filled === true && !(fv.value ?? "").trim(),
      );
      if (blocker.key === "extraction" && docsApproved && noSelfFilledBlanks && unconfirmedButFilled) {
        setActiveTab("extraction");
        setApproveOpen(true);
        return;
      }
      setBlocker(blocker);
      return;
    }
    if (nextStage) {
      setActiveTab(nextStage.key);
      // Leave a trace in the history. Fire-and-forget: a job must never fail to advance
      // because the note about advancing could not be written.
      jobsApi
        .recordStage(jobId, nextStage.label)
        .then(() => jobsApi.jobHistory(jobId).then(setHistory))
        .catch(() => {
          /* the stage still moved */
        });
    }
  };

  // The job's stages belong in the app's REAL left rail, under Dashboard and Jobs — not in a
  // card floating in the content area beside it, which read as two competing navigations.
  const stageNav = (
    <div>
      {/* No top margin: this is now the whole rail, not a section under the app links. */}
      <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
        Job Progress
      </h3>
      <ul className="mt-2 space-y-1">
        {STEPS.map((step, i) => {
          const n = i + 1;
          // Green ONLY when the stage is genuinely finished — itself AND everything before
          // it — and never for one with nothing behind it. Checked exactly the way Next
          // checks it, so the tick and the button cannot disagree.
          const done = step.built && finishedThrough(step.key);
          const current = activeTab === step.key;
          return (
            <li key={step.key}>
              <button
                type="button"
                aria-current={current ? "step" : undefined}
                onClick={() => setActiveTab(step.key)}
                className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors ${
                  current
                    ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                }`}
              >
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold ${
                    done
                      ? "border-emerald-500 bg-emerald-500 text-white"
                      : current
                        ? "border-indigo-600 bg-indigo-600 text-white"
                        : "border-slate-300 bg-transparent text-slate-400 dark:border-slate-600"
                  }`}
                >
                  {done ? "✓" : n}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {step.key === "submission"
                    ? user?.role === "gk2"
                      ? "Final Approve & Proceed"
                      : "Final Submit for Approval"
                    : displayLabelFor(step.key)}
                </span>
              </button>
              {/* Every uploaded document, one line each, nested under Required Details
                  Review instead of a row of tabs above the extracted values — pressing one
                  opens it on the right, and a document already approved gets its own tick
                  the moment it is, the same way the stage above it does. Shown only while
                  this step is the one open, so the rail stays short everywhere else. */}
              {step.key === "extraction" && current && (
                <ul className="ml-7 mt-1 space-y-0.5 border-l border-slate-200 pl-3 dark:border-slate-700">
                  {reviewFiles.map((f) => {
                    const approved = isFileApproved(f);
                    const isOpen = activeFile?.id === f.id;
                    return (
                      <li key={f.id}>
                        <button
                          type="button"
                          onClick={() => setActiveFileId(f.id)}
                          className={`flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                            isOpen
                              ? "bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                              : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                          }`}
                        >
                          <span
                            className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border text-[8px] ${
                              approved
                                ? "border-emerald-500 bg-emerald-500 text-white"
                                : "border-slate-300 bg-transparent dark:border-slate-600"
                            }`}
                          >
                            {approved ? "✓" : ""}
                          </span>
                          <span className="min-w-0 flex-1 truncate">
                            {f.name}
                            {f.original_name ? <span className="opacity-60"> · {f.original_name}</span> : null}
                            {f.set_index ? <span className="opacity-60"> · set {f.set_index}</span> : null}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                  <li>
                    <button
                      type="button"
                      onClick={() => setActiveFileId(ADDITIONAL_DETAILS_ID)}
                      className={`flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                        isAdditionalDetailsActive
                          ? "bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                      }`}
                    >
                      <span
                        className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border text-[8px] ${
                          manualPending.length === 0
                            ? "border-emerald-500 bg-emerald-500 text-white"
                            : "border-slate-300 bg-transparent dark:border-slate-600"
                        }`}
                      >
                        {manualPending.length === 0 ? "✓" : ""}
                      </span>
                      <span className="min-w-0 flex-1 truncate">Additional Details</span>
                    </button>
                  </li>
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );

  return (
    <AppShell
      title={`Job · ${job.reference}`}
      subtitle={
        <span className="flex items-center gap-2">
          {job.group_name}
          {job.mode && (
            <span className="inline-flex items-center gap-1 text-slate-500 dark:text-slate-400">
              <span aria-hidden="true">·</span>
              <ModeIcon mode={job.mode} />
              {job.mode}
            </span>
          )}
        </span>
      }
      sidebar={stageNav}
      actions={
        <div className="flex items-center gap-3">
          <div className="text-right">
            {(() => {
              const outer = job.outer_status ?? job.stage ?? job.status;
              const busy =
                job.status === "processing" || job.status === "extracting" ||
                outer === "AI - Processing" || outer === "AI - Preparing for ERP" ||
                outer === "ERP Entry Process Started";
              const cls =
                job.status === "completed" || outer === "AI - ERP Submitted"
                  ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : job.status === "failed" || job.gk2_status === "failed"
                    ? "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
                    : busy
                      ? "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300"
                      : outer === "Pending GK2 Approval"
                        ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                        : outer === "GK1 Review" || outer === "GK1 Reviewing"
                          ? "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"
                          : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
              return (
                <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>
                  {busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />}
                  {outer}
                </span>
              );
            })()}
            {/* WHEN it reached this stage, from the job's own history. "Last updated" alone
                says something moved without saying what, and on a job parked for two days
                that is the question being asked. */}
            {lastMoved && (
              <p className="mt-0.5 text-[11px] text-slate-400">since {lastMoved}</p>
            )}
          </div>
          <Link
            to="/jobs"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Back to dashboard
          </Link>
        </div>
      }
    >
      <div className="mb-4 flex items-center justify-between">
        <Link to="/jobs" className="inline-block text-sm text-indigo-600 hover:underline">← All jobs</Link>
        {job.status === "failed" && (
          <Button
            size="sm"
            variant="secondary"
            onClick={rerunJob}
            isLoading={rerunning}
            title="Clears the last failed run and makes this job ready to enter again. Everything you have filled in is kept."
          >
            ↻ Rerun job
          </Button>
        )}
      </div>
      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      {/* One of this job's documents was removed after it was already extracted (an
          operator deleting the wrong file, or the custom-filter-page sweep stripping one
          that turned out to be junk-reference content) - its custom fields were cleared
          along with it, and this is the only thing that says so. Shown above every tab,
          not just Document Capture, since whoever opens the job next could land anywhere. */}
      {job.needs_reextraction && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
          <span>⚠️ One of this job's documents changed since it was last extracted — press Extract to refresh its data.</span>
          {!readOnly && (
            <Button size="sm" variant="secondary" onClick={runExtract} isLoading={extracting}>
              Extract now
            </Button>
          )}
        </div>
      )}

      {/* ---------------- Document Capture ---------------- */}
      {activeTab === "capture" && (
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">Upload documents</h2>
            {!readOnly && (
              <div className="flex items-center gap-2">
                <label className={`inline-flex cursor-pointer items-center rounded-lg border border-indigo-200 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/30 dark:text-indigo-300 dark:hover:bg-indigo-500/10 ${smartBusy ? "opacity-60" : ""}`}>
                  {smartBusy ? "Classifying…" : "Smart upload (auto-classify)"}
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.png,.jpg,.jpeg,.zip"
                    className="hidden"
                    disabled={smartBusy}
                    onChange={(e) => {
                      handleSmartUpload(e.target.files);
                      // Selecting the SAME file(s) again later must still fire onChange.
                      e.target.value = "";
                    }}
                  />
                </label>
                {job.status === "extracting" ? (
                  // Extraction now starts on its own once every slot is filled — nothing left
                  // to press here, just something to show is actually happening.
                  <span className="inline-flex items-center gap-1.5 rounded-lg border border-sky-200 bg-sky-50 px-3 py-1.5 text-sm font-medium text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
                    AI Processing…
                  </span>
                ) : (
                  <Button size="sm" onClick={runExtract} isLoading={extracting} disabled={!allUploaded}>
                    {job.status === "draft" ? "Run extraction" : "Re-run extraction"}
                  </Button>
                )}
              </div>
            )}
          </div>
          {/* What actually arrived for this job, before anything is touched - shown here only
              once loaded and only when the job really was created from an email, so a job
              started any other way (a manual upload) sees no empty "Prealert" box. */}
          {prealert?.available && <div className="mb-4">{renderPrealert()}</div>}
          {smartResult && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm dark:border-slate-800 dark:bg-slate-900">
              <p className="mb-1 font-medium text-slate-700 dark:text-slate-200">Auto-classification result</p>
              {smartResult.map((r, i) => (
                <div key={i} className="text-xs text-slate-600 dark:text-slate-400">
                  <span className="font-mono">{r.filename}</span>{" → "}
                  {r.error ? <span className="text-rose-500">{r.error}</span>
                    : r.matched && r.matched.length ? <span className="text-emerald-600">{r.matched.join(", ")}</span>
                    : <span className="text-amber-600">no match (couldn't classify)</span>}
                </div>
              ))}
            </div>
          )}
          <p className="mb-3 text-xs text-slate-400">Upload a PDF/image per slot below, or use <b>Smart upload</b> to pick several files (or a ZIP) at once and auto-route every doc — a combined file fills every type it contains, and several invoices in one PDF each become their own row.</p>

          {/* Custom ruling — which documents are required for this job */}
          {!readOnly && (
            <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50/50 p-3 text-sm dark:border-amber-500/20 dark:bg-amber-500/5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-slate-600 dark:text-slate-300">⚖️ This template may require only some documents depending on the data.</span>
                <Button size="sm" variant="secondary" onClick={() => checkRuling()} isLoading={rulingBusy}>Check required documents</Button>
              </div>
              {ruling && ruling.has_ruling === false && (
                <p className="mt-2 text-xs text-slate-400">No custom ruling on this template — all declared documents apply.</p>
              )}
              {ruling && ruling.has_ruling && (
                <div className="mt-2 text-xs">
                  {ruling.decision && <p className="text-slate-600 dark:text-slate-300">Decision: <b>{ruling.decision}</b></p>}
                  {ruling.required_documents && ruling.required_documents.length > 0 && (
                    <p className="mt-0.5 text-emerald-700 dark:text-emerald-400">Required: <b>{ruling.required_documents.join(", ")}</b></p>
                  )}
                  {ruling.reason && <p className="mt-0.5 text-slate-400">{ruling.reason}</p>}
                  {ruling.missing_documents && ruling.missing_documents.length > 0 && (
                    <p className="mt-0.5 text-amber-700 dark:text-amber-400">
                      Still to upload: <b>{ruling.missing_documents.join(", ")}</b>
                    </p>
                  )}
                  {ruling.needs_input && (
                    <div className="mt-2 rounded-md border border-amber-300 bg-white p-2 dark:border-amber-500/30 dark:bg-slate-900">
                      <p className="mb-1 font-medium text-amber-700 dark:text-amber-300">
                        ⏸ This job is on hold — {ruling.question || "the AI needs your input to decide which documents are required."}
                      </p>
                      <div className="flex gap-2">
                        {ruling.options && ruling.options.length > 0 ? (
                          <select
                            value={rulingAnswer}
                            onChange={(e) => setRulingAnswer(e.target.value)}
                            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                          >
                            <option value="">— select —</option>
                            {ruling.options.map((o) => (
                              <option key={o} value={o}>{o}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            value={rulingAnswer}
                            onChange={(e) => setRulingAnswer(e.target.value)}
                            placeholder="Type your answer (e.g. the incoterm / document type)"
                            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                          />
                        )}
                        <Button size="sm" onClick={() => checkRuling(rulingAnswer)} isLoading={rulingBusy} disabled={!rulingAnswer.trim()}>Submit</Button>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-400">
                        The ERP entry is blocked until this is answered.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            {docSlots.map(({ tdocId, name, doc_type, required, files }) => {
              const isUploading = uploadingId === tdocId;
              const uploaded = files.filter((f) => f.is_uploaded);
              return (
                <div key={tdocId}>
                  <p className="mb-1 text-sm font-medium text-slate-900 dark:text-slate-100">
                    {name} <span className="text-xs text-slate-400">({doc_type})</span>
                    {!required && (
                      <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                        optional
                      </span>
                    )}
                    {uploaded.length > 1 && (
                      <span className="ml-1 text-xs font-normal text-slate-400">
                        — {uploaded.length} files
                      </span>
                    )}
                  </p>
                  {uploaded.map((d) => (
                    <div key={d.id} className="mb-2">
                      <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                        <span className="min-w-0 truncate">
                          ✓ {d.original_name ?? `${d.page_count} page${d.page_count === 1 ? "" : "s"}`}
                          <span className="ml-1 text-xs opacity-70">
                            ({d.page_count} page{d.page_count === 1 ? "" : "s"}
                            {/* Which invoice this file was paired into — by invoice number,
                                not by the order it was uploaded. */}
                            {uploaded.length > 1 && d.set_index ? `, invoice ${d.set_index}` : ""}
                            {uploaded.length > 1 && !d.set_index ? ", whole job" : ""})
                          </span>
                        </span>
                        {!readOnly && (
                          <button
                            type="button"
                            onClick={() => handleRemoveFile(d.id)}
                            disabled={uploadingId === d.id}
                            className="ml-2 shrink-0 text-xs font-medium text-rose-600 hover:underline disabled:opacity-50"
                          >
                            {uploadingId === d.id ? "Removing…" : "Remove"}
                          </button>
                        )}
                      </div>
                      <JobDocPreview jobId={jobId} docId={d.id} pages={d.page_count} />
                    </div>
                  ))}
                  {readOnly ? (
                    uploaded.length === 0 && (
                      <div className="rounded-lg border border-dashed border-slate-300 px-3 py-4 text-center text-sm text-slate-400 dark:border-slate-700">Not uploaded</div>
                    )
                  ) : (
                    <label
                      onDragOver={(e) => { e.preventDefault(); setDragOverId(tdocId); }}
                      onDragLeave={() => setDragOverId(null)}
                      onDrop={(e) => { e.preventDefault(); setDragOverId(null); handleUpload(tdocId, e.dataTransfer.files); }}
                      className={`flex cursor-pointer items-center justify-center rounded-lg border-2 border-dashed px-3 py-3 text-center text-sm transition-colors ${
                        dragOverId === tdocId ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10" : "border-slate-300 bg-slate-50 hover:border-indigo-400 dark:border-slate-700 dark:bg-slate-900"
                      }`}
                    >
                      {isUploading ? (
                        <span className="text-indigo-600">Uploading…</span>
                      ) : (
                        <span className="text-slate-500">
                          {uploaded.length === 0
                            ? "Click or drag a file (PDF, PNG, JPG)"
                            : `Add another ${name.toLowerCase()}`}
                        </span>
                      )}
                      {/* `multiple`: three invoices are picked in one go, not one at a time. */}
                      <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg" className="hidden" disabled={isUploading} onChange={(e) => handleUpload(tdocId, e.target.files)} />
                    </label>
                  )}
                </div>
              );
            })}
          </div>
          {!allUploaded && !readOnly && <p className="mt-3 text-xs text-slate-400">Upload every document — extraction starts on its own once all of them are in.</p>}
        </Card>
      )}

      {/* ---------------- Data Validation ---------------- */}
      {activeTab === "validation" && (
        <Card>
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
            <div>
              <h2 className="font-semibold text-slate-900 dark:text-slate-50">Document Cross-Verification</h2>
              <p className="text-xs text-slate-500">The same field compared across documents.</p>
            </div>
            {job.verifications.length > 0 && (
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${job.all_checks_passed ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>
                {job.all_checks_passed ? "✓ All checks passed" : `${unresolved} to review`}
              </span>
            )}
          </div>
          {job.verifications.length === 0 ? (
            <p className="px-5 py-4 text-sm text-slate-400">No cross-document checks configured for this template.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wider text-slate-500">
                  <tr className="border-b border-slate-200 dark:border-slate-800">
                    <th className="px-5 py-3">Field</th><th className="px-5 py-3">Source 1</th><th className="px-5 py-3">Source 2</th><th className="px-5 py-3 text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {job.verifications.map((v) => {
                    const rowTint =
                      v.accepted || v.status === "match" ? ""
                      : v.status === "mismatch" ? "bg-rose-50/40 dark:bg-rose-500/5"
                      : v.status === "review" ? "bg-amber-50/40 dark:bg-amber-500/5"
                      : "bg-slate-50/60 dark:bg-slate-800/30"; // missing
                    const badge =
                      v.accepted ? { c: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300", t: "✓ Accepted" }
                      : v.status === "match" ? { c: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300", t: "✓ Match" }
                      : v.status === "mismatch" ? { c: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300", t: "✗ Mismatch" }
                      : v.status === "review" ? { c: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300", t: "≈ Review" }
                      : { c: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400", t: "— Missing" };
                    const val = (x: string | null) =>
                      x ? <span className="whitespace-pre-line text-slate-900 dark:text-slate-100">{x}</span> : <span className="italic text-slate-400">not found</span>;
                    // A field whose own prompt already reconciled several documents itself
                    // (see _self_verifying_findings on the backend) has no second document to
                    // name - target_document comes back "" for that row.
                    const selfVerifying = v.target_document === "";
                    const canAct = !readOnly && !v.accepted && (v.status === "review" || v.status === "mismatch");
                    return (
                      <tr key={v.link_id} className={`border-b border-slate-100 dark:border-slate-800/60 ${rowTint}`}>
                        <td className="px-5 py-3">
                          <div className="font-medium text-slate-900 dark:text-slate-100">{v.field_label}</div>
                          <div className="text-xs text-slate-400">
                            {selfVerifying ? "Self-verifying" : `${v.source_document} vs ${v.target_document}`}
                          </div>
                        </td>
                        <td className="px-5 py-3"><div className="text-xs text-slate-400">{v.source_document}</div>{val(v.source_value)}</td>
                        <td className="px-5 py-3">
                          {selfVerifying ? <span className="text-slate-300 dark:text-slate-600">—</span> : (
                            <>
                              <div className="text-xs text-slate-400">{v.target_document}</div>
                              {val(v.target_value)}
                            </>
                          )}
                        </td>
                        <td className="px-5 py-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${badge.c}`}>{badge.t}</span>
                            {canAct && (
                              <Button size="sm" variant="secondary" onClick={() => decideVerification(v.link_id, true)}>Accept</Button>
                            )}
                            {!readOnly && v.accepted && (
                              <button onClick={() => decideVerification(v.link_id, false)} className="text-xs text-slate-400 hover:underline">undo</button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {!readOnly && (
            <div className="flex items-center justify-end gap-3 border-t border-slate-200 px-5 py-4 dark:border-slate-800">
              {(user?.role === "gk2" ? job.gk2_validation_approved : job.validation_approved) ? (
                <>
                  <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    ✓ Approved
                  </span>
                  <Button variant="secondary" onClick={() => setActiveTab("submission")}>
                    Continue to ERP Submission →
                  </Button>
                </>
              ) : (
                <>
                  {unresolved > 0 && (
                    <span className="text-xs text-slate-400">
                      {unresolved} cross-check{unresolved === 1 ? "" : "s"} still flagged above —
                      pressing Approved and Proceed accepts the data as-is anyway.
                    </span>
                  )}
                  <Button onClick={approveValidationNow} isLoading={validationApproving}>
                    Approved and Proceed
                  </Button>
                </>
              )}
            </div>
          )}
        </Card>
      )}

      {/* ---------------- ERP Submission ---------------- */}
      {activeTab === "submission" && (
        <Card className="p-5">
          {(() => {
            // The raw field, not outer_status - "Failed" reads identically whether it came
            // from GK2's own run or an ordinary GK1 entry that never went near GK2, and only
            // gk2_status (null once no GK2 hand-off has happened) tells those apart.
            const gk2Started = job.gk2_status != null;
            const isGk2 = user?.role === "gk2";
            const gk2CanAct = job.gk2_status === "pending" || job.gk2_status === "failed";
            return (
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="font-semibold text-slate-900 dark:text-slate-50">
                    {isGk2 ? "Final Approve & Proceed" : "Final Submit for Approval"}
                  </h2>
                  <p className="text-xs text-slate-500">
                    {isGk2
                      ? "Give this job its final approval, or send it back for GK1 to fix."
                      : gk2Started
                        ? "Waiting on GK2's final approval."
                        : "Hand this job over to GK2 for a final approval and the real ERP entry."}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {job.excel_entry && (
                    <Button variant="secondary" onClick={downloadExcel} isLoading={excelBusy}>
                      Download as Excel
                    </Button>
                  )}
                  {!readOnly && isGk2 && (
                    <Button
                      onClick={gk2ApproveAndProceed}
                      isLoading={gk2Busy}
                      disabled={!gk2CanAct}
                    >
                      {job.gk2_status === "failed" ? "Retry & Proceed" : "Final Approve & Proceed"}
                    </Button>
                  )}
                  {!readOnly && !isGk2 && !gk2Started && (
                    <Button
                      onClick={() => {
                        if (fixQueue.length > 0) {
                          // Ask for them one at a time instead of naming them in a banner and
                          // leaving the operator to find them on the page.
                          setFixAt(0);
                          setFixDraft(fixQueue[0].fv.corrected_value ?? fixQueue[0].fv.value ?? "");
                          setFixError(null);
                          setFixOpen(true);
                          return;
                        }
                        submitForGk2Approval();
                      }}
                      isLoading={gk2Busy}
                      disabled={job.status !== "extracted" || !job.validation_approved}
                    >
                      Final Submit for Approval
                    </Button>
                  )}
                  {!readOnly && !isGk2 && gk2Started && (
                    <span className="text-xs font-medium text-indigo-600 dark:text-indigo-400">
                      ✓ Submitted for GK2 approval
                    </span>
                  )}
                </div>
              </div>
            );
          })()}
          {excelError && (
            <div className="mb-4">
              <Alert>{excelError}</Alert>
            </div>
          )}

          {/* GK1 chose Approval for IRN, and GK2 has pressed Final Approve & Proceed - the
              real ERP submission does not run for this job, so there is no progress to show
              here (no spinner, no "preparing"/"entering" banner - job.erp_status is never
              touched on this path). Just says where the job actually is. */}
          {job.gk2_status === "irn_document_process" && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
              This job is in IRN Document Process.
            </div>
          )}

          {/* The last entry failed. Say what was rejected, point at the red field, and make the
              retry obvious — it re-runs the ERP script with the corrected values, WITHOUT
              re-extracting the documents, so a fixed value is all that changes. */}
          {!readOnly && entryFailed && (
            <div className="mb-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm dark:border-rose-500/30 dark:bg-rose-500/10">
              <p className="font-semibold text-rose-800 dark:text-rose-300">
                ⚠ The last ERP entry failed
                {job.erp_failed_field ? <> — the ERP rejected <b>{job.erp_failed_field}</b></> : null}
              </p>
              {job.erp_diagnosis && (
                <p className="mt-1 text-xs text-rose-800 dark:text-rose-200">{job.erp_diagnosis}</p>
              )}
              {!job.erp_diagnosis && job.erp_reason && (
                <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{job.erp_reason}</p>
              )}
              <p className="mt-2 text-xs text-rose-700 dark:text-rose-400">
                {job.erp_failed_field
                  ? "Correct the value outlined in red below, press Save on it, then Run failed job again."
                  : "Correct anything wrong below, press Save, then Run failed job again."}
              </p>
            </div>
          )}
          {/* These fields have their own section on Required Details Review now. Pointing at
              it rather than repeating the boxes here — two editable copies of one field is
              how a value gets typed in the place that is not the one being read. Pressing
              Submit still walks through whatever is unanswered, one at a time, so the fast
              path is unchanged. */}
          {!readOnly && askPending.length > 0 && (
            <div className="mb-4 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm dark:border-sky-500/30 dark:bg-sky-500/10">
              <p className="font-semibold text-sky-800 dark:text-sky-300">
                ✎ {askPending.length} field{askPending.length === 1 ? "" : "s"} still need an answer before this entry can be submitted
              </p>
              <p className="mt-1 text-xs text-sky-700 dark:text-sky-400">
                {askPending.map((f) => f.label_name).join(", ")}
              </p>
              <button
                type="button"
                onClick={() => setActiveTab("extraction")}
                className="mt-2 text-xs font-medium text-sky-800 underline hover:no-underline dark:text-sky-300"
              >
                Fill them on Required Details Review →
              </button>
            </div>
          )}
          {/* The per-line boxes and the whole extracted-value listing used to sit here.
              They belong to Required Details Review now — this stage is about the run: press
              Submit, watch it go, and see how it ended. Keeping editable copies here meant
              the same value had two homes, and the one being read was not always the one
              being typed into. */}
          {/* ERP replay result — shown after Submit Entry */}
          {!submitting && job.erp_status && (
            <div
              className={`mb-4 rounded-lg border px-4 py-3 text-sm ${
                job.erp_status === "ok"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : job.erp_status === "no_script" || job.erp_status === "duplicated" || job.erp_status === "partial"
                    ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300"
                    : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300"
              }`}
            >
              <p className="font-semibold">
                {job.erp_status === "ok"
                  ? "✓ Entered into the ERP and submitted."
                  : job.erp_status === "partial"
                    ? /* Submitted, but not cleanly — the entry may be missing a date, a
                         document or a captured value. Not a re-run: that would duplicate it. */
                      `⚠ Entered and submitted, but some steps failed — check the entry in the ERP before relying on it.${job.erp_reason ? " " + job.erp_reason : ""}`
                  : job.erp_status === "duplicated"
                    ? `⧉ Duplicate — this entry already exists in the ERP, so it wasn't submitted.${job.erp_reason ? " " + job.erp_reason : ""}`
                    : job.erp_status === "no_script"
                      ? "No ERP script configured for this template — nothing was entered."
                      : `✗ ERP entry failed — reported to the Super Admin inbox.${job.erp_reason ? " " + job.erp_reason : ""}`}
              </p>
              {showsMachinery && job.erp_final_url && (
                <p className="mt-0.5 text-xs opacity-80">Final page: {job.erp_final_url}</p>
              )}
              {showsMachinery && job.erp_log && job.erp_log.length > 0 && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
                  {job.erp_log.join("\n")}
                </pre>
              )}
              {/* HOW FAR THROUGH. Shown whenever the length of the script is known - while the
                  entry is running, and afterwards from the log it left behind, so a job opened
                  tomorrow still says how far it got. Deliberately outside the screenshot block
                  below: the picture is dropped a few minutes after a run ends, and the progress
                  should not vanish with it. */}
              {/* An operator sees that it is working, without the step numbers. Hiding the
                  progress bar from them would otherwise leave a screen that looks frozen
                  through a run that takes a minute or more. */}
              {!showsMachinery && live?.live && (
                <p className="mt-2 flex items-center gap-2 text-xs">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-sky-500" />
                  Entering this job into the ERP — this takes a minute or two.
                </p>
              )}
              {showsMachinery && progress && progress.total > 0 && (
                <div className="mt-2">
                  <div className="mb-1 flex items-center justify-between text-xs font-medium">
                    <span>
                      {live?.live ? "Entering" : "Reached"} step {progress.done} of{" "}
                      {progress.total}
                    </span>
                    <span className="tabular-nums opacity-70">{progress.percent}%</span>
                  </div>
                  <div
                    className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
                    role="progressbar"
                    aria-valuenow={progress.percent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`ERP entry progress: step ${progress.done} of ${progress.total}`}
                  >
                    <div
                      className={`h-full rounded-full transition-[width] duration-500 ${
                        live?.live
                          ? "bg-sky-500"
                          : progress.percent === 100
                            ? "bg-emerald-500"
                            : "bg-amber-500"
                      }`}
                      style={{ width: `${progress.percent}%` }}
                    />
                  </div>
                </div>
              )}
              {/* THE LIVE VIEW - shown while the entry is happening, in place of the
                  after-the-fact screenshot. */}
              {live && live.screenshot && (
                <div className="mt-2">
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="inline-flex items-center gap-1.5 font-medium text-sky-700 dark:text-sky-300">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
                      {live.live ? "Entering now - live" : "Finished"}
                    </span>
                    <a
                      href={`data:image/png;base64,${live.screenshot}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      Open full size ↗
                    </a>
                  </div>
                  <div className="max-h-[32rem] overflow-auto rounded border border-sky-400 dark:border-sky-600">
                    <img
                      src={`data:image/png;base64,${live.screenshot}`}
                      alt="The ERP screen as the entry happens"
                      className="block w-full"
                    />
                  </div>
                  {live.log.length > 0 && (
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
                      {live.log.slice(-14).join(String.fromCharCode(10))}
                    </pre>
                  )}
                </div>
              )}
              {!live && job.erp_screenshot && (
                <div className="mt-2">
                  <div className="mb-1 flex items-center justify-between text-xs opacity-80">
                    <span>The screen after the entry</span>
                    {/* A full-page capture of a bill of entry is far taller than this panel.
                        Opening it in a tab shows it at its own size, which is the point of
                        capturing it at twice the pixel density in the first place. */}
                    <a
                      href={`data:image/png;base64,${job.erp_screenshot}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      Open full size ↗
                    </a>
                  </div>
                  {/* Scrolls rather than squashing: the whole page was captured, so the
                      operator can read down it instead of squinting at a shrunken copy. */}
                  <div className="max-h-[32rem] overflow-auto rounded border border-slate-300 dark:border-slate-700">
                    <img
                      src={`data:image/png;base64,${job.erp_screenshot}`}
                      alt="The ERP screen after the entry"
                      className="block w-full"
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      {/* ------- ERP Submission: the job once it has settled -------
          Completed, duplicate AND failed all land here — this panel reports the outcome
          whichever way it went, and narrowing it to "completed" would have silently hidden
          the failure screen, which is the one an operator most needs to see. */}
      {activeTab === "submission" &&
        (job.status === "completed" || job.status === "duplicate" || job.status === "failed") && (
        <div className="flex flex-col gap-4">
          {(() => {
            const failed = job.status === "failed" || job.erp_status === "error" || job.erp_status === "failed";
            return (
              <Card className={`flex flex-col items-center justify-center gap-3 px-6 py-10 text-center ${failed ? "border-rose-200 dark:border-rose-500/30" : ""}`}>
                <div className={`flex h-14 w-14 items-center justify-center rounded-2xl ${failed ? "bg-rose-50 text-rose-600 dark:bg-rose-500/10" : "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10"}`}>
                  {failed ? (
                    <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                  ) : (
                    <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>
                  )}
                </div>
                <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">
                  {failed ? "Entry failed" : "Job completed"}
                </h2>
                <p className="max-w-md text-sm text-slate-500 dark:text-slate-400">
                  {failed ? (
                    <>The ERP entry for <b>{job.reference}</b> did not go through. {job.erp_reason}</>
                  ) : (
                    <>The ERP entry for <b>{job.reference}</b> has been submitted. This job now shows as Completed on the dashboard.</>
                  )}
                </p>
                {failed && job.erp_diagnosis && (
                  <div className="mt-1 max-w-lg rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-left dark:border-amber-500/30 dark:bg-amber-500/10">
                    <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                      What the screen showed
                    </p>
                    <p className="text-sm text-amber-900 dark:text-amber-100">{job.erp_diagnosis}</p>
                  </div>
                )}
                {failed && job.erp_failed_field && (
                  <p className="text-xs text-rose-700 dark:text-rose-300">
                    Stopped at <b>{job.erp_failed_field}</b>
                    {job.erp_failed_value ? <> — value <span className="font-mono">{job.erp_failed_value}</span></> : null}
                  </p>
                )}
                <Link to="/jobs" className="text-sm text-indigo-600 hover:underline">← Back to all jobs</Link>
              </Card>
            );
          })()}

          {/* Everything the script PICKED out of the ERP. On a failed run this still shows what
              was read before it stopped — the reference may already have been generated. */}
          {job.erp_captured && Object.keys(job.erp_captured).length > 0 && (
            <Card className="p-5">
              <h3 className="mb-1 text-sm font-semibold text-slate-900 dark:text-slate-50">
                Picked from the ERP
              </h3>
              <p className="mb-4 text-xs text-slate-500">
                Values and documents the entry read back out of the ERP for this job.
              </p>
              <div className="flex flex-col gap-3">
                {Object.entries(job.erp_captured)
                  // "internal" picks exist only to be re-entered into a later ERP screen —
                  // showing them would clutter the operator's view with plumbing.
                  .filter(([, raw]) => typeof raw === "string" || (raw.usage ?? "both") !== "internal")
                  .map(([key, raw]) => {
                  // Older runs stored a bare string; newer ones store a record. Handle both so
                  // a job captured before this change still displays.
                  const item = typeof raw === "string"
                    ? { label: key, value: raw, kind: "value", description: "", file: null }
                    : raw;
                  return (
                    <div key={key} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-50">
                          {item.label || key}
                        </span>
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {item.kind || "value"}
                        </span>
                      </div>
                      {item.description && (
                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{item.description}</p>
                      )}
                      {item.kind === "image" && item.file ? (
                        /* The success screenshot — the whole reason for recording that step is
                           that somebody can LOOK at what the ERP ended up showing. A download
                           link is not looking at it. Click to open the full-size picture. */
                        <a
                          href={jobsApi.capturedFileUrl(job.id, item.file)}
                          target="_blank"
                          rel="noreferrer"
                          title="Open the full-size screenshot"
                          className="mt-2 block overflow-hidden rounded-lg border border-slate-200 hover:border-indigo-400 dark:border-slate-700"
                        >
                          <img
                            src={jobsApi.capturedFileUrl(job.id, item.file)}
                            alt={item.label || "The ERP screen at the end of the entry"}
                            className="w-full"
                          />
                        </a>
                      ) : item.kind === "document" && item.file ? (
                        <a
                          href={jobsApi.capturedFileUrl(job.id, item.file)}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/40 dark:text-indigo-300"
                        >
                          ⬇ {item.file}
                        </a>
                      ) : item.value ? (
                        <p className={`mt-2 rounded-lg bg-slate-50 px-3 py-2 font-mono text-sm text-slate-900 dark:bg-slate-800 dark:text-slate-100 ${item.kind === "text" ? "whitespace-pre-wrap" : ""}`}>
                          {item.value}
                        </p>
                      ) : (
                        <p className="mt-2 text-xs italic text-slate-400">nothing was read for this item</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ---------------- Data Extraction (Required Details Review) ----------------
          Dump Data and Manual Data Entry used to be their own separate stages, each with
          their own tab in the rail. Their content now renders here instead, below the
          extracted values, so everything to fill in before Cross Docs Verification is on
          one screen — nothing about what they show or how a value gets saved has changed. */}
      {activeTab === "extraction" && (
        <div className="flex flex-col gap-4">
          {!isAdditionalDetailsActive && (
            <ExtractionReview
              job={job}
              jobId={jobId}
              reload={load}
              readOnly={!canEditValues}
              activeFileId={activeFile?.id ?? null}
              setActiveFileId={setActiveFileId}
            />
          )}

          {/* Formerly the Dump Data tab — the customer's reference-sheet lookups (CTH/RITC). */}
          <DumpDataView job={job} reload={load} readOnly={!canEditValues} />

          {/* Formerly the Manual Data Entry tab — the fields the system asks a person for.
              Selected from the sidebar's own "Additional Details" row, below every document —
              that name is what the operator sees; "Manual Data Entry" stays an internal-only
              name for how the fields are asked and saved, same as it always was. */}
          {isAdditionalDetailsActive && (
            <>
              <Card className="p-5">
                <h2 className="font-semibold text-indigo-600 dark:text-indigo-400">
                  Additional Details
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  {manualFields.length === 0
                    ? "This template asks the operator for nothing — every field comes off a document or is worked out."
                    : manualPending.length === 0
                      ? `All ${manualFields.length} field(s) are answered.`
                      : `${manualPending.length} of ${manualFields.length} still to answer. The entry cannot be submitted until they are.`}
                </p>
              </Card>

              {commonComputedFields.length > 0 && (
                <Card className="p-5">
                  <h3 className="mb-1 text-sm font-semibold text-slate-900 dark:text-slate-50">
                    Worked out for this job
                  </h3>
                  <p className="mb-3 text-xs text-slate-400">
                    These aren't printed as-is on any single document — the system looked at
                    everything on this job and filled them in for you. Please check each one.
                  </p>
                  <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                    {commonComputedFields.map((fv) => (
                      <ExtractedField key={fv.id} fv={fv} readOnly={!canEditValues} reload={load} tone="computed" />
                    ))}
                  </div>
                </Card>
              )}

              {manualFields.filter((fv) => fv.row_index == null).length > 0 && (
                <Card className="p-5">
                  <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-50">
                    For the whole job
                  </h3>
                  <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                    {manualFields
                      .filter((fv) => fv.row_index == null)
                      .map((fv) => (
                        <div key={fv.id}>
                          <ExtractedField fv={fv} readOnly={!canEditValues} reload={load} />
                          {fv.ask_operator_hint && (
                            <p className="mt-1 text-[11px] text-slate-400">{fv.ask_operator_hint}</p>
                          )}
                        </div>
                      ))}
                  </div>
                </Card>
              )}

              {perRowAsks.length > 0 && (
                <Card className="p-5">
                  <h3 className="mb-1 text-sm font-semibold text-slate-900 dark:text-slate-50">
                    One value per product line
                  </h3>
                  <p className="mb-3 text-xs text-slate-400">
                    {perRowAsks.length} line(s). Each is named by its product, so a value goes
                    against the right one.
                  </p>
                  <div className="flex flex-col gap-3">
                    {perRowAsks.map(({ line, items, context }) => (
                      <div
                        key={line}
                        className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                      >
                        <p className="mb-2 flex items-baseline gap-2 text-xs">
                          <span className="font-semibold text-slate-500">Product {line}</span>
                          {context && (
                            <span className="min-w-0 truncate text-slate-400">{context}</span>
                          )}
                        </p>
                        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                          {items.map((fv) => (
                            <ExtractedField
                              key={fv.id}
                              fv={fv}
                              readOnly={!canEditValues}
                              reload={load}
                              small
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </>
          )}
        </div>
      )}

      {/* ---------------- IRN Documents Upload (GK1) / IRN Processing (GK2) ---------------- */}
      {activeTab === "irn" && (
        <div className="space-y-4">
          <Card className="p-6">
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">
              {displayLabelFor("irn")}
            </p>

            <div className="mt-4">{renderPrealert()}</div>

            {/* Supporting documents: any file, under a name the uploader chooses. Never OCR'd
                or extracted - purely stored against the job. */}
            <div className="mt-4 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                Supporting Documents
              </p>

              {supportingDocs.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {supportingDocs.map((doc) => (
                    <li
                      key={doc.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/60"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
                          {doc.label}
                        </p>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          {doc.files.map((f) =>
                            IMAGE_EXTENSIONS.some((ext) => f.original_name.toLowerCase().endsWith(ext)) ? (
                              <SupportingDocFileThumb
                                key={f.stored_as}
                                jobId={jobId}
                                docId={doc.id}
                                storedAs={f.stored_as}
                                name={f.original_name}
                              />
                            ) : (
                              <button
                                key={f.stored_as}
                                type="button"
                                onClick={() =>
                                  openSupportingDocumentFile(doc.id, f.stored_as, f.original_name)
                                }
                                className="text-xs text-indigo-600 hover:underline dark:text-indigo-400"
                              >
                                {f.original_name}
                              </button>
                            ),
                          )}
                        </div>
                        {renderIrnPlaceholder()}
                      </div>
                      <button
                        type="button"
                        onClick={() => handleIrnDelete(doc.id)}
                        className="text-xs text-rose-600 hover:underline dark:text-rose-400"
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <div className="mt-3 flex flex-wrap items-end gap-3">
                <div className="w-56">
                  <Input
                    label="Document name"
                    placeholder="e.g. Certificate of Origin"
                    value={irnLabel}
                    onChange={(e) => setIrnLabel(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    File(s)
                  </label>
                  <input
                    type="file"
                    multiple
                    onChange={(e) => setIrnFiles(Array.from(e.target.files ?? []))}
                    className="text-sm text-slate-600 dark:text-slate-300"
                  />
                </div>
                <Button
                  onClick={handleIrnUpload}
                  isLoading={irnUploading}
                  disabled={!irnLabel.trim() || irnFiles.length === 0}
                >
                  Upload
                </Button>
              </div>
              <p className="mt-2 text-xs text-slate-400">
                Optional — attach whatever is relevant under any name you choose, one or many
                files at a time. Nothing is required here.
              </p>
              {irnError && (
                <div className="mt-2">
                  <Alert>{irnError}</Alert>
                </div>
              )}
            </div>
          </Card>

          {/* Completion: GK1's own persisted tick (upload something above, or skip) vs GK2's
              unchanged dummy Approve & Proceed press. */}
          {user?.role === "gk2" ? (
            <Card className="p-8 text-center">
              <p className="mx-auto max-w-md text-sm text-slate-500">
                {irnApproved
                  ? "Marked complete — nothing real runs here yet, this is a stand-in."
                  : "Nothing real runs here yet — press below to mark it complete and move on."}
              </p>
              {irnApproved ? (
                <p className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600 dark:text-emerald-400">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs text-white">✓</span>
                  Approved
                </p>
              ) : (
                <Button className="mt-4" onClick={() => setIrnApproved(true)}>
                  Approve & Proceed
                </Button>
              )}
            </Card>
          ) : (
            <Card className="p-6 text-center">
              {job.irn_documents_done ? (
                <p className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600 dark:text-emerald-400">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs text-white">✓</span>
                  Marked complete
                </p>
              ) : (
                <>
                  <p className="mx-auto max-w-md text-sm text-slate-500">
                    Upload a document above if there's anything to attach, then choose one:
                  </p>
                  <div className="mt-3 flex flex-wrap items-center justify-center gap-3">
                    <Button onClick={handleIrnApprove} isLoading={irnApproving}>
                      Approval for IRN
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={handleIrnSkip}
                      isLoading={irnSkipping}
                    >
                      Skip — nothing to attach
                    </Button>
                  </div>
                </>
              )}
            </Card>
          )}
        </div>
      )}

      {/* The stages are chained: this is the only way forward, and it refuses while the
          current one is unfinished. Every screen stays readable either way — what is gated
          is advancing, not looking. Every stage now has its own explicit approve action
          inside its own content (IRN Processing's dummy button included), so this bar is
          just the plain "move on" prompt — it no longer needs a second, stage-specific
          confirm button of its own. */}
      {nextStage && (
        <div className="mt-6 flex items-center justify-between gap-4">
          <p className="text-xs text-slate-400">
            {blockedBecause(activeTab)
              ? "This stage is not finished yet."
              : `Ready to continue to ${displayLabelFor(nextStage.key)}.`}
          </p>
          <Button variant="secondary" onClick={goNext}>
            Next: {displayLabelFor(nextStage.key)} →
          </Button>
        </div>
      )}

      <Modal
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        title="You haven't changed anything — is that right?"
      >
        <p className="text-sm text-slate-600 dark:text-slate-300">
          {askPending.length} field{askPending.length === 1 ? "" : "s"} arrived already filled
          in and {askPending.length === 1 ? "was" : "were"} left as {askPending.length === 1 ? "it is" : "they are"}. Approving records
          {askPending.length === 1 ? " it" : " them"} as checked, and the entry can be submitted.
        </p>
        <div className="mt-3 max-h-52 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700">
          <table className="w-full text-left text-xs">
            <tbody>
              {askPending.slice(0, 40).map((fv) => (
                <tr key={fv.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                  <td className="px-3 py-1.5 text-slate-500">
                    {fv.label_name}
                    {fv.row_index != null && (
                      <span className="ml-1 text-slate-400">· line {fv.row_index}</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 font-medium text-slate-800 dark:text-slate-200">
                    {fv.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {askPending.length > 40 && (
          <p className="mt-1 text-[11px] text-slate-400">
            …and {askPending.length - 40} more.
          </p>
        )}
        {/* Named plainly, because approving a stale duty notification is the mistake this
            step exists to catch — these values change from one year to the next. */}
        <p className="mt-3 text-xs text-slate-400">
          Check the notification numbers are the current ones before approving.
        </p>
        <div className="mt-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setApproveOpen(false)}
            className="text-xs text-slate-500 hover:underline dark:text-slate-400"
          >
            Go back and change something
          </button>
          <Button onClick={approveUnchanged} isLoading={approving}>
            Approve and continue
          </Button>
        </div>
      </Modal>

      <Modal
        open={blocker !== null}
        onClose={() => setBlocker(null)}
        title="Finish the earlier stage first"
      >
        {/* Red, and it names the stage. The old wording described whatever screen you were
            standing on, which was rarely the thing actually blocking you — you can jump
            ahead in the rail, so the unfinished stage is usually one you have already
            walked past. */}
        <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 dark:border-rose-500/30 dark:bg-rose-500/10">
          <p className="text-sm font-semibold text-rose-800 dark:text-rose-300">
            {blocker ? displayLabelFor(blocker.key) : ""} is not finished
          </p>
          <p className="mt-1 text-sm text-rose-700 dark:text-rose-200">{blocker?.why}</p>
        </div>
        <p className="mt-3 text-xs text-slate-400">
          The stages run in order, so this one has to be settled before the job moves on. You
          can still open any stage to see what is there.
        </p>
        <div className="mt-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setBlocker(null)}
            className="text-xs text-slate-500 hover:underline dark:text-slate-400"
          >
            Stay here
          </button>
          <Button
            onClick={() => {
              const k = blocker?.key;
              setBlocker(null);
              if (k) setActiveTab(k);
            }}
          >
            Go to {blocker ? displayLabelFor(blocker.key) : ""} →
          </Button>
        </div>
      </Modal>

      {/* The job's own extracted data hashed the same as an already-settled job's - almost
          certainly the same shipment's paperwork arriving twice (a resend, a forward, the
          same PDF pulled from two mailboxes). Blocks nothing permanently: Hold just lets the
          operator look around before deciding, Approve treats it as genuinely separate,
          Delete removes it outright. */}
      <Modal
        open={job?.status === "possible_duplicate" && !dupDismissed}
        onClose={() => setDupDismissed(true)}
        title="This job may already exist"
      >
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-500/30 dark:bg-amber-500/10">
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
            Matches {job?.duplicate_of_reference ?? "an existing job"}
          </p>
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-200">
            The documents on this job extracted to the same data as{" "}
            <span className="font-semibold">{job?.duplicate_of_reference ?? "another job"}</span>
            . That usually means the same shipment's paperwork arrived twice — a resend, a
            forward, or the same file pulled from two mailboxes.
          </p>
        </div>
        {job?.duplicate_of_reference && (
          <Link
            to={`/jobs/${job.duplicate_of_job_id}`}
            className="mt-2 inline-block text-xs text-sky-600 hover:underline dark:text-sky-400"
          >
            Open {job.duplicate_of_reference} to compare →
          </Link>
        )}
        {dupError && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{dupError}</p>}
        <p className="mt-3 text-xs text-slate-400">
          Approve if this is genuinely a separate shipment that happens to match. Delete if it
          is a real repeat. Hold to decide later — this job stays flagged until you do.
        </p>
        <div className="mt-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setDupDismissed(true)}
            disabled={dupBusy}
            className="text-xs text-slate-500 hover:underline dark:text-slate-400 disabled:opacity-50"
          >
            Hold — decide later
          </button>
          <div className="flex gap-2">
            <Button variant="danger" onClick={deleteDuplicateNow} disabled={dupBusy}>
              Delete
            </Button>
            <Button onClick={approveDuplicateNow} disabled={dupBusy}>
              Approve — it's a different job
            </Button>
          </div>
        </div>
      </Modal>

      {/* ONE FIELD AT A TIME. Submit used to paint a banner naming what was missing and leave
          the operator to find each one on the page - on a fourteen-line invoice that is a hunt
          through eighty boxes. This asks for them in order, says which line each belongs to,
          and counts down. The last one submits the entry itself, so nothing has to be found
          again afterwards. */}
      <Modal
        open={fixOpen}
        onClose={() => setFixOpen(false)}
        title={
          fixQueue.length
            ? `Fill in ${fixQueue.length} field${fixQueue.length === 1 ? "" : "s"} to submit`
            : "Ready to submit"
        }
      >
        {fixQueue.length === 0 ? (
          <div>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Everything is filled in. Submitting the entry now.
            </p>
            <div className="mt-4 flex justify-end">
              <Button
                onClick={() => {
                  setFixOpen(false);
                  submitEntry();
                }}
              >
                Submit entry
              </Button>
            </div>
          </div>
        ) : (
          (() => {
            const at = Math.min(fixAt, fixQueue.length - 1);
            const { fv, where } = fixQueue[at];
            const save = async () => {
              const val = fixDraft.trim();
              if (!val) {
                setFixError("This field has to have a value before the entry can be submitted.");
                return;
              }
              setFixSaving(true);
              setFixError(null);
              try {
                await jobsApi.correctFieldValue(fv.id, val);
                await load();
                // The queue is rebuilt from the job, so the one just answered drops out of it
                // and the NEXT unanswered field becomes index 0. Staying put is what advances.
                const remaining = fixQueue.length - 1;
                if (remaining <= 0) {
                  setFixOpen(false);
                  submitEntry();
                } else {
                  setFixAt(0);
                  setFixDraft("");
                }
              } catch {
                setFixError("That value could not be saved. Try again.");
              } finally {
                setFixSaving(false);
              }
            };
            return (
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
                    {fixQueue.length} left
                  </span>
                  <div className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                    <div
                      className="h-full rounded-full bg-indigo-500 transition-[width] duration-300"
                      style={{
                        width: `${Math.max(4, 100 - (fixQueue.length * 100) / Math.max(fixQueue.length + at, 1))}%`,
                      }}
                    />
                  </div>
                </div>

                <p className="text-xs uppercase tracking-wide text-slate-400">{where}</p>
                <label
                  htmlFor="fix-value"
                  className="mt-1 block text-base font-semibold text-slate-900 dark:text-slate-50"
                >
                  {fv.label_name}
                </label>
                {fv.ask_operator_hint && (
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {fv.ask_operator_hint}
                  </p>
                )}
                {(fv.value ?? "").trim() && (
                  <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                    Read from the document:{" "}
                    <span className="font-mono text-slate-700 dark:text-slate-200">
                      {fv.value}
                    </span>
                  </p>
                )}
                <input
                  id="fix-value"
                  autoFocus
                  value={fixDraft}
                  onChange={(e) => setFixDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !fixSaving) save();
                  }}
                  placeholder="Type the value"
                  className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                />
                {fixError && (
                  <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{fixError}</p>
                )}

                <div className="mt-4 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => setFixOpen(false)}
                    className="text-xs text-slate-500 hover:underline dark:text-slate-400"
                  >
                    Close and fill them on the page instead
                  </button>
                  <Button onClick={save} isLoading={fixSaving}>
                    {fixQueue.length === 1 ? "Save and submit entry" : "Save and next →"}
                  </Button>
                </div>
              </div>
            );
          })()
        )}
      </Modal>
    </AppShell>
  );
}

/** Which extracted value is focused right now, and where its mark sits on ITS OWN document
 *  (normalized 0-1, straight off JobFieldValue.mark_x/y/width/height) — lets DocViewer jump
 *  to the right page, zoom in, and draw a box over the exact spot so a person can cross-check
 *  the reading against the source page without hunting for it. */
export type DocHighlight = {
  fieldId: string;
  page: number; // 1-based
  x: number;
  y: number;
  width: number;
  height: number;
};

/** A document viewer for the Data Extraction screen: the page shown at a readable size in
 *  the panel, with zoom and page controls.
 *
 *  It replaced a strip of thumbnails that opened a modal on click. Checking twenty extracted
 *  values against a page means looking from one to the other and back — a picture you have to
 *  open, read, and close again before you can type makes that impossible, and a thumbnail is
 *  far too small to read a printed invoice from.
 *
 *  Pages are fetched through the API (they are behind auth, so a plain <img src> will not do)
 *  and the object URLs are revoked on unmount.
 */
function DocViewer({
  jobId,
  docId,
  pages,
  highlight,
}: {
  jobId: string;
  docId: string;
  pages: number;
  /** The field currently focused on the right, if its mark belongs to THIS document. */
  highlight?: DocHighlight | null;
}) {
  const [urls, setUrls] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    let alive = true;
    const created: string[] = [];
    setLoading(true);
    setPage(0);
    (async () => {
      const out: string[] = [];
      for (let p = 1; p <= pages; p++) {
        try {
          const u = await jobsApi.jobDocPageUrl(jobId, docId, p);
          created.push(u);
          out.push(u);
        } catch {
          /* a page that will not render is skipped rather than breaking the viewer */
        }
      }
      if (alive) {
        setUrls(out);
        setLoading(false);
      } else {
        created.forEach((u) => URL.revokeObjectURL(u));
      }
    })();
    return () => {
      alive = false;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [jobId, docId, pages]);

  // Jump to the focused field's own page. Keyed on fieldId, not the whole object, so this
  // only fires when a DIFFERENT field is focused — not on every re-render.
  useEffect(() => {
    if (!highlight) return;
    const target = highlight.page - 1;
    if (target >= 0 && target < urls.length) setPage(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.fieldId, urls.length]);

  // Zoom in enough to actually read the marked value, unless the reader already zoomed in
  // further themselves — this never zooms back OUT while they're cross-checking.
  useEffect(() => {
    if (!highlight || highlight.width <= 0 || highlight.height <= 0) return;
    setZoom((z) => (z < 150 ? 175 : z));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.fieldId]);

  // Scroll the highlighted box to the centre of the panel, once the page image at the
  // current zoom has actually finished loading/laying out.
  useEffect(() => {
    if (!highlight || highlight.width <= 0 || highlight.height <= 0) return;
    if (highlight.page - 1 !== page) return;
    const container = containerRef.current;
    const img = imgRef.current;
    if (!container || !img) return;
    const h = highlight;
    const scrollToBox = () => {
      const cRect = container.getBoundingClientRect();
      const iRect = img.getBoundingClientRect();
      const boxCenterX = iRect.left - cRect.left + container.scrollLeft + h.x * iRect.width + (h.width * iRect.width) / 2;
      const boxCenterY = iRect.top - cRect.top + container.scrollTop + h.y * iRect.height + (h.height * iRect.height) / 2;
      container.scrollTo({
        left: Math.max(0, boxCenterX - container.clientWidth / 2),
        top: Math.max(0, boxCenterY - container.clientHeight / 2),
        behavior: "smooth",
      });
    };
    if (img.complete) {
      const id = requestAnimationFrame(scrollToBox);
      return () => cancelAnimationFrame(id);
    }
    img.addEventListener("load", scrollToBox, { once: true });
    return () => img.removeEventListener("load", scrollToBox);
  }, [highlight, page, zoom, urls]);

  if (loading) return <p className="text-sm text-slate-400">Loading the document…</p>;
  if (urls.length === 0) {
    return <p className="text-sm text-slate-400">This document has no pages to show.</p>;
  }

  const showBox = !!highlight && highlight.width > 0 && highlight.height > 0 && highlight.page - 1 === page;

  const btn =
    "flex h-7 w-7 items-center justify-center rounded text-slate-600 transition-colors hover:bg-slate-200 disabled:cursor-default disabled:opacity-30 dark:text-slate-300 dark:hover:bg-slate-700";

  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between gap-2 rounded-lg bg-slate-100 px-2 py-1.5 dark:bg-slate-800">
        <div className="flex items-center gap-1">
          <button type="button" className={btn} onClick={() => setZoom((z) => Math.max(50, z - 25))}
            disabled={zoom <= 50} aria-label="Zoom out">−</button>
          <span className="w-12 text-center text-xs tabular-nums text-slate-500">{zoom}%</span>
          <button type="button" className={btn} onClick={() => setZoom((z) => Math.min(400, z + 25))}
            disabled={zoom >= 400} aria-label="Zoom in">+</button>
          <button
            type="button"
            onClick={() => setZoom(100)}
            className="ml-1 rounded px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-700"
          >
            Fit
          </button>
        </div>
        {urls.length > 1 && (
          <div className="flex items-center gap-1">
            <button type="button" className={btn} onClick={() => setPage((n) => Math.max(0, n - 1))}
              disabled={page === 0} aria-label="Previous page">‹</button>
            <span className="text-xs tabular-nums text-slate-500">
              {page + 1} of {urls.length}
            </span>
            <button type="button" className={btn} onClick={() => setPage((n) => Math.min(urls.length - 1, n + 1))}
              disabled={page >= urls.length - 1} aria-label="Next page">›</button>
          </div>
        )}
        <a
          href={urls[page]}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded px-2 py-1 text-[11px] text-indigo-600 hover:underline dark:text-indigo-400"
        >
          Open full size
        </a>
      </div>
      {/* Both axes scroll: zoomed past the panel width, the page has to be pannable sideways
          or the right-hand column of an invoice becomes unreachable. */}
      <div
        ref={containerRef}
        className="h-[34rem] overflow-auto rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900"
      >
        <div className="relative inline-block">
          <img
            ref={imgRef}
            src={urls[page]}
            alt={`Page ${page + 1}`}
            style={{ width: `${zoom}%` }}
            className="block max-w-none"
          />
          {showBox && (
            <div
              className="pointer-events-none absolute rounded-sm ring-2 ring-amber-400 bg-amber-300/25 transition-all duration-300 dark:ring-amber-300 dark:bg-amber-400/20"
              style={{
                left: `${highlight!.x * 100}%`,
                top: `${highlight!.y * 100}%`,
                width: `${highlight!.width * 100}%`,
                height: `${highlight!.height * 100}%`,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/** Dump Data — the values this job took from the customer's own reference sheet.
 *
 * Only those. The workbook has 298 columns across eight sheets, and showing them was a wall
 * of dashes that answered no question anyone had: what is worth checking before an entry
 * goes is the handful of values nobody read off a document and nobody typed — the CTH and
 * RITC looked up from a part code.
 *
 * They are editable here, because a part number missing from the sheet is deliberately left
 * blank rather than guessed, and this is where you would notice.
 */
function DumpDataView({
  job,
  reload,
  readOnly,
}: {
  job: JobDetail;
  reload: () => Promise<void>;
  readOnly: boolean;
}) {
  // self_filled is the server's own flag for "answered from the reference sheet", the same
  // one the submit gate uses — so this screen cannot drift from what the system believes.
  //
  // A row that carries an actual document line item (a mark read straight off the invoice)
  // now shows its own reference-sheet lookup right there on that same card, under Required
  // Details Review - see ExtractionReview's lineRows/lookedUpByRow. Showing it again here, in
  // a second "Line 1" disconnected from the part code it was looked up for, would just be the
  // same value in two places. Only a row with NO document line item of its own - or the
  // job-level fields at row_index null - still needs a home, and this is it.
  const rowsWithDocumentLineItem = useMemo(() => {
    const set = new Set<number>();
    for (const fv of job.field_values) {
      if (fv.row_index != null && fv.mark_id && fv.origin === "document") set.add(fv.row_index);
    }
    return set;
  }, [job.field_values]);

  const lookedUp = useMemo(() => {
    const rows = new Map<number, JobFieldValue[]>();
    for (const fv of job.field_values) {
      if (fv.self_filled !== true) continue;
      if (fv.row_index != null && rowsWithDocumentLineItem.has(fv.row_index)) continue;
      const key = fv.row_index ?? 0;
      const arr = rows.get(key) ?? [];
      arr.push(fv);
      rows.set(key, arr);
    }
    return [...rows.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([line, items]) => ({
        line,
        items: items.sort((a, b) => a.label_name.localeCompare(b.label_name)),
        // What the line is, so a code is checked against a product rather than a number.
        context:
          job.field_values.find(
            (f) =>
              f.row_index === line &&
              f.self_filled !== true &&
              /descri/i.test(f.label_name) &&
              (f.value ?? "").trim(),
          )?.value ?? "",
      }));
  }, [job.field_values, rowsWithDocumentLineItem]);

  const all = lookedUp.flatMap((r) => r.items);
  const missing = all.filter((fv) => !(fv.value ?? "").trim()).length;
  const mergedElsewhere = job.field_values.filter(
    (fv) => fv.self_filled === true && fv.row_index != null && rowsWithDocumentLineItem.has(fv.row_index),
  ).length;

  if (all.length === 0) {
    return (
      <Card className="p-8 text-center">
        <p className="text-sm text-slate-500">
          {mergedElsewhere > 0
            ? `${mergedElsewhere} value(s) looked up from the reference sheet are shown on their own product line under Required Details Review.`
            : "Nothing on this job is looked up from a reference sheet."}
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-semibold text-indigo-600 dark:text-indigo-400">Dump Data</h2>
          {missing > 0 && (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
              {missing} not found
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {all.length} value(s) looked up in the customer's own export by part code. A part
          that is not on the sheet, or one the sheet gives two codes for, is left blank rather
          than guessed — type it here and it goes into the dump.
        </p>
        <div className="mt-3">
          <ColourKey />
        </div>
      </Card>

      {lookedUp.map(({ line, items, context }) => (
        <Card key={line} className="p-5">
          {line > 0 && (
            <p className="mb-3 flex items-baseline gap-2 text-xs">
              <span className="font-semibold text-slate-500">Product {line}</span>
              {context && <span className="min-w-0 truncate text-slate-400">{context}</span>}
            </p>
          )}
          <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
            {items.map((fv) => (
              <ExtractedField
                key={fv.id}
                fv={fv}
                readOnly={readOnly}
                reload={reload}
                tone="reference"
              />
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}


/** The colour language, defined ONCE and shown the same on every screen.
 *
 * Both screens had their own three-item legend listing only the colours that screen could
 * produce, so blue was explained on one and simply absent from the other — a reader could
 * not learn what the colours mean without visiting both. The key is the same everywhere;
 * what changes is which of them you happen to see.
 */
function ColourKey() {
  const items: { cls: string; label: string }[] = [
    {
      cls: "border-sky-200 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10",
      label: "read off a document",
    },
    {
      cls: "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10",
      label: "from the reference sheet",
    },
    {
      cls: "border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10",
      label: "worked out from a document",
    },
    {
      cls: "border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10",
      label: "you changed it",
    },
    {
      cls: "border-rose-300 bg-rose-50/60 dark:border-rose-500/40 dark:bg-rose-500/10",
      label: "blank — nothing found",
    },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1.5">
          <span className={`h-3 w-3 rounded-sm border ${i.cls}`} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** One extracted field, editable in place.
 *
 * Shows the CURRENT value — the correction if one was typed, otherwise what extraction read.
 * Saving on blur rather than behind a Save button: an operator checking twenty fields against
 * a page should not have to press twenty buttons, and a value left half-typed and navigated
 * away from is the commonest way to lose work.
 *
 * A field that has been corrected is marked, so it stays obvious which values came off the
 * page and which a person overrode.
 */
function ExtractedField({
  fv,
  readOnly,
  reload,
  small,
  tone,
  note,
  onFocusField,
}: {
  fv: JobFieldValue;
  readOnly: boolean;
  reload: () => Promise<void>;
  small?: boolean;
  /** Where the value came from, painted onto the box itself: green from the customer's
   *  reference sheet, blue read off a document. An empty box stays plain — nothing came
   *  from anywhere, so there is no source to show. */
  tone?: "reference" | "document" | "computed";
  /** A line under the box. Used to say when this copy of a field is a cross-check rather
   *  than the one the entry actually uses. */
  note?: string;
  /** Data Extraction only: tell the document preview which value is focused, so it can jump
   *  to and highlight this field's mark on the page. */
  onFocusField?: (fv: JobFieldValue) => void;
}) {
  const current = fv.corrected_value ?? fv.extracted_value ?? "";
  const [draft, setDraft] = useState(current);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  // The job reloads after every save, so props change under us; re-sync unless the operator
  // is mid-edit on this very box.
  useEffect(() => {
    setDraft(current);
  }, [current]);

  const dirty = draft !== current;

  const commit = async () => {
    if (!dirty || readOnly) return;
    setSaving(true);
    setFailed(false);
    try {
      await jobsApi.correctFieldValue(fv.id, draft);
      await reload();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const corrected = fv.corrected_value != null && fv.corrected_value !== "";
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center gap-1.5">
        <p
          className={`truncate font-semibold text-slate-900 dark:text-slate-100 ${
            small ? "text-[11px] font-medium text-slate-500 dark:text-slate-400" : "text-sm"
          }`}
        >
          {fv.label_name}
        </p>
        {corrected && (
          <span className="shrink-0 rounded bg-amber-100 px-1 text-[10px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
            edited
          </span>
        )}
        {saving && <span className="shrink-0 text-[10px] text-slate-400">saving…</span>}
        {failed && (
          <span className="shrink-0 text-[10px] text-rose-600">could not save</span>
        )}
      </div>
      <div className="relative">
      <input
        value={draft}
        readOnly={readOnly}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={() => onFocusField?.(fv)}
        // Blur still saves, as a safety net for anyone who types and tabs away. The button
        // below suppresses its own mousedown so it never takes focus from the input, so the
        // two cannot both fire — without that, blur would save and reload, unmounting the
        // button before its own click landed.
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        placeholder="not found"
        className={`w-full rounded-lg border px-3 py-2 text-sm transition-colors placeholder:italic placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none ${
          failed
            ? "border-rose-300 text-slate-700 dark:border-rose-500/40 dark:text-slate-200"
            : corrected
              ? // An operator's own correction outranks the source colour: what matters most
                // about this value is no longer where it came from, but that a person changed it.
                "border-amber-200 bg-amber-50/60 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
              : !draft.trim()
                ? // Extraction found nothing for this field — flagged red so a blank box reads
                  // as "missing" rather than as "nothing to check," which the plain box before
                  // this let slide past unnoticed.
                  "border-rose-300 bg-rose-50/60 text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
                : tone === "reference"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
                  : tone === "document"
                    ? "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200"
                    : tone === "computed"
                      ? "border-violet-200 bg-violet-50 text-violet-900 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-200"
                      : "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        } ${readOnly ? "cursor-default" : ""} ${!readOnly && (dirty || corrected) ? "pr-16" : ""}`}
      />
      {!readOnly && (dirty || saving) && (
        <button
          type="button"
          // Keeps focus in the input, so pressing Save does not blur it first.
          onMouseDown={(e) => e.preventDefault()}
          onClick={commit}
          disabled={!dirty || saving}
          className={`absolute right-1.5 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-[11px] font-semibold transition-colors ${
            dirty
              ? "bg-indigo-600 text-white hover:bg-indigo-500"
              : "cursor-default bg-transparent text-emerald-600 dark:text-emerald-400"
          }`}
        >
          {saving ? "…" : "Save"}
        </button>
      )}
      </div>
      {note && <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">{note}</p>}
    </div>
  );
}

/** Data Extraction — the document on the left, what was read out of it on the right.
 *
 * Shows `extracted_value`: what the reader took OFF the page, before any operator
 * correction. Not `value`, which prefers a typed correction — the point of this screen is to
 * check the machine against the paper, and showing the human's own answer back to them would
 * defeat it. Read-only for the same reason; corrections belong on the entry screen.
 *
 * One panel per uploaded FILE, not per slot, so three invoices are three panels and each
 * sits beside its own pages.
 */
function ExtractionReview({
  job,
  jobId,
  reload,
  readOnly,
  activeFileId,
  setActiveFileId,
}: {
  job: JobDetail;
  jobId: string;
  reload: () => Promise<void>;
  readOnly: boolean;
  // Which file is open — owned by JobRunPage now, not this component, so the rail's own
  // document checklist (under "Required Details Review") and this panel are always looking
  // at the same one instead of two separate ideas of "which file is open". Switching files
  // normally only happens from that checklist, but approving a document also advances here,
  // so the setter is passed down too.
  activeFileId: string | null;
  setActiveFileId: (id: string) => void;
}) {
  const files = useMemo(
    () =>
      // The order the API sends, which is the template's own — Bill of lading, Invoice,
      // Packing List, Freight Certificate. Sorting by name put "Fright Certificate" second,
      // alphabetically, which is not how anyone works through a shipment.
      (job.documents ?? []).filter((d) => d.is_uploaded),
    [job.documents],
  );
  const { user } = useAuth();
  const isGk2 = user?.role === "gk2";
  const proceedLabel = isGk2 ? "Approve & Proceed" : "Submit for Approval";
  // GK2 re-reviews every document independently, on its own column — never GK1's.
  const isApproved = (d: JobDocument) => (isGk2 ? d.gk2_approved : d.approved);
  const active = files.find((f) => f.id === activeFileId) ?? files[0];
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const approvedCount = files.filter(isApproved).length;

  // Delete/Reupload for the file on screen. Reupload is delete-then-upload into the SAME
  // slot rather than a raw upload over the existing file: the upload endpoint adds a new
  // file alongside whatever is already in a slot (a job can genuinely carry several files
  // per document type), so replacing the old one first is what makes this a true swap
  // rather than a second, duplicate file next to it.
  const [docActionBusy, setDocActionBusy] = useState(false);
  const [docActionError, setDocActionError] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const reuploadInputRef = useRef<HTMLInputElement>(null);

  const deleteActiveDocument = async () => {
    if (!active) return;
    setDocActionError(null);
    setDocActionBusy(true);
    try {
      await jobsApi.deleteJobDocumentFile(jobId, active.id);
      await reload();
    } catch (err) {
      if (axios.isAxiosError(err)) setDocActionError(err.response?.data?.detail ?? "Could not delete that document.");
    } finally {
      setDocActionBusy(false);
      setConfirmDeleteOpen(false);
    }
  };

  const reuploadActiveDocument = async (file: File) => {
    if (!active) return;
    setDocActionError(null);
    setDocActionBusy(true);
    try {
      await jobsApi.deleteJobDocumentFile(jobId, active.id);
      await jobsApi.uploadJobDocument(jobId, active.template_document_id, file);
      await reload();
    } catch (err) {
      if (axios.isAxiosError(err)) setDocActionError(err.response?.data?.detail ?? "Reupload failed.");
    } finally {
      setDocActionBusy(false);
    }
  };

  // Which extracted value is focused, so the document preview can jump to and highlight its
  // mark. Cleared on switching documents — a field focused on one file's tab has no business
  // being drawn over a completely different file's page.
  const [focusedFv, setFocusedFv] = useState<JobFieldValue | null>(null);
  useEffect(() => {
    setFocusedFv(null);
  }, [active?.id]);
  // found_* (where this value's own text actually sits on THIS document) drives the
  // highlight, never mark_* (the template's static, one-time-drawn box) — every real
  // document has its own layout, so a fixed template position is only right by coincidence.
  // No found_* means no confident match was made; show no highlight rather than guess.
  const highlight: DocHighlight | null = useMemo(() => {
    if (!focusedFv || focusedFv.found_page == null) return null;
    return {
      fieldId: focusedFv.id,
      page: focusedFv.found_page,
      x: focusedFv.found_x ?? 0,
      y: focusedFv.found_y ?? 0,
      width: focusedFv.found_width ?? 0,
      height: focusedFv.found_height ?? 0,
    };
  }, [focusedFv]);

  const approveActive = async () => {
    if (!active) return;
    const approving = !isApproved(active);
    setApprovingId(active.id);
    try {
      await jobsApi.approveDocument(jobId, active.id, approving);
      await reload();
      // Only advance on an actual approve, never on "Undo" — and only when there is
      // somewhere to go. Staying put on the last document is the point, not a bug.
      if (approving) {
        const currentIndex = files.findIndex((f) => f.id === active.id);
        const next = files[currentIndex + 1];
        if (next) setActiveFileId(next.id);
      }
    } finally {
      setApprovingId(null);
    }
  };

  // Values for the file on screen. Older jobs — extracted before values recorded which file
  // they came from — fall back to matching on the document name, so they still display.
  const fields = useMemo(() => {
    if (!active) return [];
    const mine = job.field_values.filter((fv) => fv.job_document_id === active.id);
    const rows = mine.length
      ? mine
      : job.field_values.filter(
          (fv) => !fv.job_document_id && fv.document_name === active.name,
        );
    // Only what was read off the page. A computed or looked-up field was never on the
    // document, so it has no business on the screen that checks the reading against it —
    // those belong to Dump Data.
    return rows.filter((fv) => fv.row_index == null && fv.mark_id);
  }, [job.field_values, active]);


  // Which document's copy of a field actually reaches the entry.
  //
  // A field marked on four documents is read from all four so they can be cross-checked, but
  // only ONE of those readings is used: the first non-empty one, in the template's order. The
  // rest exist to be compared against it. Without saying so, an operator correcting HBL No on
  // the Invoice tab would change a value that goes nowhere, and the entry would still carry
  // the Bill of Lading's — a correction that appears to have been made and has not.
  const usedFrom = useMemo(() => {
    const order = new Map(
      (job.documents ?? []).map((d, i) => [d.template_document_id, i] as const),
    );
    const winner = new Map<string, string>();
    const rows = job.field_values
      .filter((fv) => fv.row_index == null && fv.mark_id && (fv.value ?? "").trim())
      .sort(
        (a, b) =>
          (order.get(a.template_document_id ?? "") ?? 99) -
          (order.get(b.template_document_id ?? "") ?? 99),
      );
    for (const fv of rows) {
      if (!winner.has(fv.label_name)) winner.set(fv.label_name, fv.document_name);
    }
    return winner;
  }, [job.field_values, job.documents]);

  // Drop the cross-check copies that came back EMPTY.
  //
  // A field marked on four documents for cross-checking is often carried by only one or two
  // of them: the packing list has no HBL number and no gross weight. Those showed here as
  // empty boxes captioned "the entry uses Bill of lading's copy" — a warning about another
  // document attached to a value this one never had. There is nothing to check against the
  // page, so there is nothing to show. A copy that DID read something stays, note and all,
  // because that is a real reading worth checking; and a field empty on EVERY document also
  // stays, because "not found anywhere" is worth seeing.
  const visibleFields = useMemo(() => {
    return fields.filter((fv) => {
      const from = usedFrom.get(fv.label_name);
      const isCopy = !!from && from !== fv.document_name;
      return !(isCopy && !(fv.value ?? "").trim());
    });
  }, [fields, usedFrom]);

  const lineRows = useMemo(() => {
    if (!active) return [];
    const mine = job.field_values.filter(
      (fv) => fv.row_index != null && fv.job_document_id === active.id && fv.mark_id,
    );
    const byRow = new Map<number, JobFieldValue[]>();
    for (const fv of mine) {
      const arr = byRow.get(fv.row_index!) ?? [];
      arr.push(fv);
      byRow.set(fv.row_index!, arr);
    }
    return [...byRow.entries()].sort((a, b) => a[0] - b[0]);
  }, [job.field_values, active]);

  // The reference-sheet lookup for THIS SAME product line (the CTH/RITC code, keyed off the
  // part number sitting right there in the card above) - self_filled carries no mark and no
  // job_document_id of its own, only the row_index that ties it to one physical line, so it
  // has to be joined in here rather than by document. Shown alongside the line it was looked
  // up FOR instead of in a second, disconnected "Line 1" card further down the page under
  // Dump Data - a part code and the classification looked up for it belong on one card.
  const lookedUpByRow = useMemo(() => {
    const rows = new Map<number, JobFieldValue[]>();
    for (const fv of job.field_values) {
      if (fv.self_filled !== true || fv.row_index == null) continue;
      const arr = rows.get(fv.row_index) ?? [];
      arr.push(fv);
      rows.set(fv.row_index, arr);
    }
    return rows;
  }, [job.field_values]);

  if (!files.length) {
    return (
      <Card className="p-8 text-center">
        <p className="text-sm text-slate-500">
          Nothing has been uploaded yet. Add the documents on Document Capture, then run
          extraction.
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-xs dark:border-slate-700 dark:bg-slate-800/50">
        <span className="text-slate-500">
          {approvedCount} of {files.length} document{files.length === 1 ? "" : "s"} approved —
          every one needs "Approved and Proceed" before this can move to Cross Docs Verification.
        </span>
      </div>

      {/* The per-file switcher used to be a row of tabs here — it's the rail's own checklist
          under "Required Details Review" now (see stageNav in JobRunPage), so picking a
          document and seeing which ones are approved happens in one place, not two. */}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        {/* Sticky: the fields column is longer than the page image, and checking a value
            against the document is impossible if the document scrolls away while you read
            the twentieth field. */}
        <Card className="p-5 lg:sticky lg:top-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="font-semibold text-indigo-600 dark:text-indigo-400">
              Document Preview
            </h2>
            {!readOnly && active && (
              <div className="flex items-center gap-2">
                <input
                  ref={reuploadInputRef}
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void reuploadActiveDocument(file);
                  }}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={docActionBusy || job.status === "extracting"}
                  isLoading={docActionBusy}
                  onClick={() => reuploadInputRef.current?.click()}
                >
                  Reupload
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  disabled={docActionBusy || job.status === "extracting"}
                  onClick={() => setConfirmDeleteOpen(true)}
                >
                  Delete Document
                </Button>
              </div>
            )}
          </div>
          {docActionError && (
            <p className="mb-2 text-xs text-rose-600 dark:text-rose-400">{docActionError}</p>
          )}
          {active && (
            <DocViewer jobId={jobId} docId={active.id} pages={active.page_count} highlight={highlight} />
          )}
        </Card>

        <Modal
          open={confirmDeleteOpen}
          onClose={() => setConfirmDeleteOpen(false)}
          title="Delete this document?"
        >
          <p className="text-sm text-slate-600 dark:text-slate-300">
            This removes {active?.name ?? "this document"}'s uploaded file and every value read
            off it. Once a replacement is uploaded, re-run extraction to fill those values back
            in.
          </p>
          <div className="mt-4 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setConfirmDeleteOpen(false)}
              className="text-xs text-slate-500 hover:underline dark:text-slate-400"
            >
              Cancel
            </button>
            <Button variant="danger" isLoading={docActionBusy} onClick={() => void deleteActiveDocument()}>
              Delete document
            </Button>
          </div>
        </Modal>

        <Card className="p-5">
          <h2 className="mb-1 font-semibold text-indigo-600 dark:text-indigo-400">
            Extracted Data
          </h2>
          <p className="mb-3 text-xs text-slate-400">
            The fields read out of this document. Check them against the page on the left and
            correct anything wrong — a line item's own reference-sheet lookup sits right on its
            card below; anything looked up for the whole job is further down, under Dump Data.
          </p>
          <div className="mb-4">
            <ColourKey />
          </div>
          {visibleFields.length === 0 && lineRows.length === 0 ? (
            <p className="text-sm text-slate-400">
              Nothing was read from this document yet.
            </p>
          ) : (
            <>
              <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                {visibleFields.map((fv) => {
                  const from = usedFrom.get(fv.label_name);
                  return (
                    <ExtractedField
                      key={fv.id}
                      fv={fv}
                      readOnly={readOnly}
                      reload={reload}
                      tone="document"
                      onFocusField={setFocusedFv}
                      note={
                        from && from !== fv.document_name
                          ? `Cross-check only — the entry uses ${from}'s copy.`
                          : undefined
                      }
                    />
                  );
                })}
              </div>

              {lineRows.length > 0 && (
                <div className="mt-6">
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                    Product Detail · {lineRows.length}
                  </p>
                  <div className="flex flex-col gap-2">
                    {lineRows.map(([n, cells]) => {
                      const lookedUp = lookedUpByRow.get(n) ?? [];
                      return (
                        <div
                          key={n}
                          className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                        >
                          <p className="mb-2 text-xs font-semibold text-slate-500">Product {n}</p>
                          <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                            {cells.map((fv) => (
                              <ExtractedField
                                key={fv.id}
                                fv={fv}
                                readOnly={readOnly}
                                reload={reload}
                                small
                                tone="document"
                                onFocusField={setFocusedFv}
                              />
                            ))}
                            {/* Looked up in the customer's own reference sheet by the part
                                code on this same line - left blank rather than guessed when
                                the sheet does not carry it, same as everywhere else this
                                colour appears. */}
                            {lookedUp.map((fv) => (
                              <ExtractedField
                                key={fv.id}
                                fv={fv}
                                readOnly={readOnly}
                                reload={reload}
                                small
                                tone="reference"
                              />
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      {!readOnly && active && (
        <div className="flex items-center justify-end gap-3 rounded-lg border border-slate-200 px-4 py-3 dark:border-slate-700">
          {isApproved(active) ? (
            <>
              <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                ✓ Approved — {active.name}
              </span>
              <Button variant="secondary" onClick={approveActive} isLoading={approvingId === active.id}>
                Undo
              </Button>
            </>
          ) : (
            <Button onClick={approveActive} isLoading={approvingId === active.id}>
              {proceedLabel} — {active.name}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function JobDocPreview({ jobId, docId, pages }: { jobId: string; docId: string; pages: number }) {
  const [urls, setUrls] = useState<string[]>([]);
  const [zoom, setZoom] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const created: string[] = [];
    (async () => {
      const out: string[] = [];
      for (let p = 1; p <= Math.min(pages, 6); p++) {
        try {
          const u = await jobsApi.jobDocPageUrl(jobId, docId, p);
          created.push(u);
          out.push(u);
        } catch {
          /* skip a missing page */
        }
      }
      if (alive) setUrls(out);
      else created.forEach((u) => URL.revokeObjectURL(u));
    })();
    return () => {
      alive = false;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [jobId, docId, pages]);

  if (urls.length === 0) return null;
  return (
    <>
      <div className="mt-2 flex flex-wrap gap-2">
        {urls.map((u, i) => (
          <button key={i} type="button" onClick={() => setZoom(u)} className="block" title={`Page ${i + 1} — click to enlarge`}>
            <img src={u} alt={`page ${i + 1}`} className="h-28 w-auto rounded border border-slate-300 object-cover hover:ring-2 hover:ring-indigo-400 dark:border-slate-700" />
          </button>
        ))}
      </div>
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-6" onClick={() => setZoom(null)}>
          <img src={zoom} alt="document page" className="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl" />
        </div>
      )}
    </>
  );
}

const IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"];

/** A Supporting Document's own file, previewed the same way Prealert now previews Document
 *  Capture's images - only for actual image files, since a PDF (the common case here) has no
 *  simple single-image thumbnail the way an OCR'd page does. Every other file type stays the
 *  plain click-to-open link this replaces, via isImageFile below. */
function SupportingDocFileThumb({
  jobId, docId, storedAs, name,
}: { jobId: string; docId: string; storedAs: string; name: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    (async () => {
      try {
        const u = await jobsApi.supportingDocumentFileUrl(jobId, docId, storedAs);
        created = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      } catch {
        /* falls back to nothing - the plain link elsewhere still works */
      }
    })();
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [jobId, docId, storedAs]);

  if (!url) return <span className="text-xs text-slate-400">{name}…</span>;
  return (
    <>
      <button type="button" onClick={() => setZoom(true)} className="block" title={`${name} — click to enlarge`}>
        <img src={url} alt={name} className="h-20 w-auto rounded border border-slate-300 object-cover hover:ring-2 hover:ring-indigo-400 dark:border-slate-700" />
      </button>
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-6" onClick={() => setZoom(false)}>
          <img src={url} alt={name} className="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl" />
        </div>
      )}
    </>
  );
}
