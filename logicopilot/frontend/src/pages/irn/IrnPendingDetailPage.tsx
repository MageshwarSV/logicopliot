import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import * as publicIrnApi from "../../api/publicIrn";
import type { IrnDocumentGroup } from "../../api/publicIrn";
import type { JobDetail } from "../../types/jobs";
import { PublicIrnShell } from "./PublicIrnShell";

const IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"];

function isImageFile(name: string): boolean {
  const lower = name.toLowerCase();
  return IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

// doc_ref for a Supporting Document is always "supporting-<its own id>" (see
// _document_groups in app/api/v1/public_irn.py) - the raw id is what the existing
// supporting-document file endpoint needs.
function supportingDocId(docRef: string): string {
  return docRef.replace(/^supporting-/, "");
}

/** A document page's rendered preview, fetched through the public/key-gated endpoint instead
 *  of the authenticated one JobDocPreview (in JobRunPage.tsx) uses — same rendering, same
 *  click-to-enlarge, kept as its own small copy since this page must stay standalone. */
function IrnDocPageThumb({ jobId, docId, pages, jobKey }: { jobId: string; docId: string; pages: number; jobKey: string }) {
  const [urls, setUrls] = useState<string[]>([]);
  const [zoom, setZoom] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const created: string[] = [];
    (async () => {
      const out: string[] = [];
      for (let p = 1; p <= Math.min(pages, 6); p++) {
        try {
          const u = await publicIrnApi.irnPendingDocPageUrl(jobId, docId, p, jobKey);
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
  }, [jobId, docId, pages, jobKey]);

  if (urls.length === 0) return null;
  return (
    <>
      <div className="mt-2 flex flex-wrap gap-2">
        {urls.map((u, i) => (
          <button key={i} type="button" onClick={() => setZoom(u)} className="block" title={`Page ${i + 1} — click to enlarge`}>
            <img src={u} alt={`page ${i + 1}`} className="h-24 w-auto rounded border border-slate-300 object-cover hover:ring-2 hover:ring-indigo-400 dark:border-slate-700" />
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

/** A Supporting Document's own file, previewed only when it's an image (a PDF has no single-
 *  image thumbnail the way an OCR'd page does) — the public-endpoint counterpart of
 *  SupportingDocFileThumb in JobRunPage.tsx. Every other file type falls back to a plain name. */
function IrnSupportingDocThumb({
  jobId, docId, storedAs, name, jobKey,
}: { jobId: string; docId: string; storedAs: string; name: string; jobKey: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    (async () => {
      try {
        const u = await publicIrnApi.irnPendingSupportingDocumentFileUrl(jobId, docId, storedAs, jobKey);
        created = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      } catch {
        /* falls back to nothing */
      }
    })();
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [jobId, docId, storedAs, jobKey]);

  if (!url) return <span className="text-xs text-slate-400">{name}…</span>;
  return (
    <>
      <button type="button" onClick={() => setZoom(true)} className="block" title={`${name} — click to enlarge`}>
        <img src={url} alt={name} className="h-24 w-auto rounded border border-slate-300 object-cover hover:ring-2 hover:ring-indigo-400 dark:border-slate-700" />
      </button>
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-6" onClick={() => setZoom(false)}>
          <img src={url} alt={name} className="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl" />
        </div>
      )}
    </>
  );
}

function UnsignedPreview({ jobId, jobKey, group }: { jobId: string; jobKey: string; group: IrnDocumentGroup }) {
  if (group.kind === "job_document") {
    const files = group.unsigned_files as { job_document_id: string; page_count: number }[];
    return (
      <>
        {files.map((f) => (
          <IrnDocPageThumb key={f.job_document_id} jobId={jobId} docId={f.job_document_id} pages={f.page_count} jobKey={jobKey} />
        ))}
      </>
    );
  }
  const files = group.unsigned_files as { stored_as: string; original_name: string }[];
  const docId = supportingDocId(group.doc_ref);
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {files.map((f) =>
        isImageFile(f.original_name) ? (
          <IrnSupportingDocThumb key={f.stored_as} jobId={jobId} docId={docId} storedAs={f.stored_as} name={f.original_name} jobKey={jobKey} />
        ) : (
          <span key={f.stored_as} className="text-xs text-slate-500 dark:text-slate-400">{f.original_name}</span>
        ),
      )}
    </div>
  );
}

/** The signed file, once uploaded - almost always a PDF (a DSC-signed copy), so this opens it
 *  in a new tab rather than trying to thumbnail it, fetched lazily only when the row actually
 *  has a signed file to show. */
function SignedFileLink({ jobId, jobKey, docRef, name }: { jobId: string; jobKey: string; docRef: string; name: string }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    publicIrnApi.irnPendingSignedFileUrl(jobId, docRef, jobKey).then((u) => {
      created = u;
      if (alive) setUrl(u);
      else URL.revokeObjectURL(u);
    }).catch(() => {});
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [jobId, docRef, jobKey]);

  if (!url) return <span className="text-xs text-slate-400">{name}…</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400">
      View {name}
    </a>
  );
}

/** The right-hand column for a not-yet-signed document: pick the DSC-signed file, type its
 *  IRN number, and press the one button that submits both together - there is no separate
 *  "status" control anywhere here, since the backend decides the job's next status itself
 *  (see sign_irn_pending_document) the moment every header on the job has gone through this
 *  once. */
function SignUploadForm({
  jobId, jobKey, docRef, onSigned,
}: { jobId: string; jobKey: string; docRef: string; onSigned: (result: publicIrnApi.SignIrnDocumentResult) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [irnNumber, setIrnNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!file || !irnNumber.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await publicIrnApi.signIrnPendingDocument(jobId, docRef, irnNumber.trim(), file, jobKey);
      onSigned(result);
    } catch {
      setError("Could not upload the signed document. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-2">
      <input
        type="file"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        className="block w-full text-xs text-slate-500 file:mr-2 file:rounded-lg file:border-0 file:bg-slate-100 file:px-2 file:py-1 file:text-xs file:font-medium file:text-slate-700 dark:text-slate-400 dark:file:bg-slate-800 dark:file:text-slate-200"
      />
      <input
        type="text"
        placeholder="IRN number"
        value={irnNumber}
        onChange={(e) => setIrnNumber(e.target.value)}
        className="w-full rounded-lg border border-slate-200 px-2 py-1 text-xs outline-none focus:border-indigo-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      />
      <button
        type="button"
        onClick={submit}
        disabled={busy || !file || !irnNumber.trim()}
        className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300"
      >
        {busy ? "Uploading…" : "Get DSC + IRN Number"}
      </button>
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}

/** One job's IRN Document Process screen and nothing else - reachable only via its own direct
 *  link (with a `key` query param, see IrnPendingListPage), requiring NO login, and never
 *  part of the regular tabbed job page. Every document header shows its unsigned file(s) on
 *  the left and, on the right, either the signed file + IRN number once uploaded, or the
 *  upload form itself. Once every header has been signed the job moves on by itself - there is
 *  nothing to press here to make that happen, it just happens on the last upload. */
export function IrnPendingDetailPage() {
  const { jobId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const key = searchParams.get("key") ?? "";
  const [job, setJob] = useState<JobDetail | null>(null);
  const [docs, setDocs] = useState<IrnDocumentGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [movedOn, setMovedOn] = useState(false);

  useEffect(() => {
    if (!key) {
      setError("This link is missing its access key.");
      return;
    }
    publicIrnApi.getIrnPendingJob(jobId, key).then(setJob).catch(() => setError("This link is invalid, or this job could not be found."));
    publicIrnApi.listIrnPendingDocuments(jobId, key).then(setDocs).catch(() => setDocs([]));
  }, [jobId, key]);

  function handleSigned(result: publicIrnApi.SignIrnDocumentResult) {
    setDocs(result.documents);
    if (result.all_signed) {
      setMovedOn(true);
      publicIrnApi.getIrnPendingJob(jobId, key).then(setJob).catch(() => {});
    }
  }

  if (error) {
    return (
      <PublicIrnShell title="IRN Document Process">
        <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
      </PublicIrnShell>
    );
  }
  if (!job || !docs) {
    return (
      <PublicIrnShell title="IRN Document Process">
        <p className="text-sm text-slate-400">Loading…</p>
      </PublicIrnShell>
    );
  }

  return (
    <PublicIrnShell
      title={`IRN Document Process — ${job.reference}`}
      subtitle={
        <Link to={`/irn-pending?key=${encodeURIComponent(key)}`} className="text-indigo-600 hover:underline dark:text-indigo-400">
          ← All jobs in IRN Document Process
        </Link>
      }
    >
      {movedOn && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
          Every document is signed — this job has moved on to {job.outer_status ?? "the next stage"}.
        </div>
      )}
      <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
        {docs.length === 0 && <p className="text-sm text-slate-400">No documents on this job yet.</p>}
        <div className="space-y-5">
          {docs.map((g) => (
            <div key={g.doc_ref} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
              <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{g.label}</p>
              <div className="mt-2 grid gap-4 sm:grid-cols-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Unsigned</p>
                  <UnsignedPreview jobId={jobId} jobKey={key} group={g} />
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Signed</p>
                  {g.signed ? (
                    <div className="mt-2 space-y-1">
                      <SignedFileLink jobId={jobId} jobKey={key} docRef={g.doc_ref} name={g.signed.original_name} />
                      <p className="text-xs text-slate-500 dark:text-slate-400">IRN: {g.signed.irn_number}</p>
                    </div>
                  ) : (
                    <SignUploadForm jobId={jobId} jobKey={key} docRef={g.doc_ref} onSigned={handleSigned} />
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </PublicIrnShell>
  );
}
