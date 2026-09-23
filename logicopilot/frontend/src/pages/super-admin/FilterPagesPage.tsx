import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import * as filterPagesApi from "../../api/customFilterPages";
import type { CustomFilterPage } from "../../types/customFilterPage";

export function FilterPagesPage() {
  const [pages, setPages] = useState<CustomFilterPage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [preview, setPreview] = useState<CustomFilterPage | null>(null);
  const [previewPageNum, setPreviewPageNum] = useState(1);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  async function refresh() {
    try {
      setPages(await filterPagesApi.listCustomFilterPages());
    } catch {
      setError("Failed to load filter pages.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function upload(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      await filterPagesApi.uploadCustomFilterPage(name, file);
      setModalOpen(false);
      setName("");
      setFile(null);
      refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not upload this page.");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleActive(p: CustomFilterPage) {
    await filterPagesApi.setCustomFilterPageActive(p.id, !p.is_active);
    refresh();
  }

  async function remove(p: CustomFilterPage) {
    await filterPagesApi.deleteCustomFilterPage(p.id);
    refresh();
  }

  function openPreview(p: CustomFilterPage) {
    setPreview(p);
    setPreviewPageNum(1);
  }

  function closePreview() {
    setPreview(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
  }

  // Loads (and reloads on page change) the rendered image for whichever filter page is
  // open for preview. Each fetched blob URL is revoked before the next is set, so switching
  // pages or closing the modal never leaks a stale object URL.
  useEffect(() => {
    if (!preview) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setPreviewLoading(true);
    filterPagesApi.customFilterPageUrl(preview.id, previewPageNum).then((url) => {
      if (cancelled) {
        URL.revokeObjectURL(url);
        return;
      }
      objectUrl = url;
      setPreviewUrl(url);
    }).catch(() => {
      if (!cancelled) setError("Could not load the page preview.");
    }).finally(() => {
      if (!cancelled) setPreviewLoading(false);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [preview, previewPageNum]);

  return (
    <AppShell
      title="Page Filter"
      subtitle="Upload a page (e.g. a recurring cover sheet or letterhead page). Any email-pulled document containing a near-identical page has it filtered out automatically — matched by content comparison only, never AI."
      actions={<Button size="sm" onClick={() => setModalOpen(true)}>+ Add Filter Page</Button>}
    >
      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      <Card>
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Filter Pages</h2>
        </div>
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {pages.length === 0 && (
            <p className="px-5 py-4 text-sm text-slate-400">No filter pages yet — add one above.</p>
          )}
          {pages.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-3 px-5 py-3">
              <button
                type="button"
                onClick={() => openPreview(p)}
                className="min-w-0 flex-1 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
                title="Click to preview this page"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-900 underline decoration-slate-300 decoration-dotted underline-offset-2 dark:text-slate-100">
                    {p.name}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      p.is_active
                        ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
                        : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300"
                    }`}
                  >
                    {p.is_active ? "active" : "inactive"}
                  </span>
                </div>
                <p className="truncate text-xs text-slate-400">
                  {p.original_filename ?? "—"} · {p.page_count} page{p.page_count === 1 ? "" : "s"}
                </p>
              </button>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" variant="secondary" onClick={() => toggleActive(p)}>
                  {p.is_active ? "Deactivate" : "Activate"}
                </Button>
                <Button size="sm" variant="danger" onClick={() => remove(p)}>Delete</Button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Add Filter Page">
        <form className="flex flex-col gap-4" onSubmit={upload}>
          <Input
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Standard cover sheet"
            required
          />
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="filter-page-file">
              Page (PDF or image)
            </label>
            <input
              id="filter-page-file"
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              required
              className="text-sm text-slate-700 dark:text-slate-300"
            />
            <p className="text-xs text-slate-400">
              The page is read once (OCR) at upload time, and compared against future documents by plain
              text similarity — no AI is involved in the matching itself.
            </p>
          </div>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" isLoading={submitting} disabled={!file || !name}>Upload</Button>
        </form>
      </Modal>

      <Modal open={preview !== null} onClose={closePreview} title={preview?.name ?? "Preview"} maxWidth="max-w-2xl">
        {preview && (
          <div className="flex flex-col items-center gap-4">
            {preview.page_count > 1 && (
              <div className="flex items-center gap-3">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={previewPageNum <= 1}
                  onClick={() => setPreviewPageNum((n) => n - 1)}
                >
                  ‹ Prev
                </Button>
                <span className="text-sm text-slate-500 dark:text-slate-400">
                  Page {previewPageNum} of {preview.page_count}
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={previewPageNum >= preview.page_count}
                  onClick={() => setPreviewPageNum((n) => n + 1)}
                >
                  Next ›
                </Button>
              </div>
            )}
            <div className="flex min-h-[300px] w-full items-center justify-center rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950">
              {previewLoading && <p className="py-16 text-sm text-slate-400">Loading preview…</p>}
              {!previewLoading && previewUrl && (
                <img src={previewUrl} alt={`${preview.name} — page ${previewPageNum}`} className="max-h-[70vh] w-full object-contain" />
              )}
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
