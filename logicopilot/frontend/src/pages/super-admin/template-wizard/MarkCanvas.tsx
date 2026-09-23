import { useEffect, useRef, useState } from "react";
import { fetchPageObjectUrl } from "../../../api/onboarding";
import { MARK_COLORS, type Mark } from "../../../types/onboarding";

export interface DraftBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A page image with existing marks overlaid; drag to draw a new normalized box. */
export function MarkCanvas({
  documentId,
  page,
  marks,
  labelForMark,
  onDraw,
  disabled,
}: {
  documentId: string;
  page: number;
  marks: Mark[];
  labelForMark: (m: Mark) => string;
  onDraw: (box: DraftBox) => void;
  disabled?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [imgError, setImgError] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  // Overlays are outlines, not fills, so the document stays readable underneath — and
  // this hides them entirely when you need a clean look at the page.
  const [showMarks, setShowMarks] = useState(true);

  // A zero-area mark was never cropped on THIS document — it came from "present in another
  // document?", so it is located by meaning at extraction time and has no region to draw.
  const drawn = marks.filter((m) => m.width > 0 && m.height > 0);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    setImgUrl(null);
    setImgError(false);
    fetchPageObjectUrl(documentId, page)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        revoked = url;
        setImgUrl(url);
      })
      .catch(() => !cancelled && setImgError(true));
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [documentId, page]);

  function rel(e: React.MouseEvent) {
    const rect = wrapRef.current!.getBoundingClientRect();
    return {
      x: Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1),
      y: Math.min(Math.max((e.clientY - rect.top) / rect.height, 0), 1),
    };
  }

  function finishDraw() {
    if (!draft) return;
    const w = Math.abs(draft.x1 - draft.x0);
    const h = Math.abs(draft.y1 - draft.y0);
    setDrawing(false);
    if (w < 0.005 || h < 0.005) {
      setDraft(null);
      return;
    }
    onDraw({ x: Math.min(draft.x0, draft.x1), y: Math.min(draft.y0, draft.y1), width: w, height: h });
    setDraft(null);
  }

  if (imgError) {
    return (
      <div className="flex h-96 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900">
        Could not load the page preview.
      </div>
    );
  }
  if (!imgUrl) {
    return (
      <div className="flex h-96 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-400 dark:border-slate-800 dark:bg-slate-900">
        Loading page…
      </div>
    );
  }

  return (
    <div
      ref={wrapRef}
      onMouseDown={(e) => {
        if (disabled) return;
        e.preventDefault();
        const p = rel(e);
        setDrawing(true);
        setDraft({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
      }}
      onMouseMove={(e) => {
        if (!drawing) return;
        const p = rel(e);
        setDraft((d) => (d ? { ...d, x1: p.x, y1: p.y } : d));
      }}
      onMouseUp={finishDraw}
      onMouseLeave={() => drawing && finishDraw()}
      className={`relative select-none overflow-hidden rounded-xl border border-slate-300 bg-slate-100 dark:border-slate-700 ${
        disabled ? "" : "cursor-crosshair"
      }`}
    >
      <img src={imgUrl} alt={`Page ${page}`} className="block w-full" draggable={false} />

      {/* Show/hide overlays — lets you read the untouched page. */}
      {drawn.length > 0 && (
        <button
          type="button"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={() => setShowMarks((v) => !v)}
          title={showMarks ? "Hide the field boxes" : "Show the field boxes"}
          className="absolute right-2 top-2 z-20 rounded-md bg-white/90 px-2 py-1 text-[11px] font-medium text-slate-700 shadow ring-1 ring-slate-300 backdrop-blur-sm hover:bg-white dark:bg-slate-900/90 dark:text-slate-200 dark:ring-slate-600"
        >
          {showMarks ? `Hide fields (${drawn.length})` : `Show fields (${drawn.length})`}
        </button>
      )}

      {/* pointer-events-none so overlays never swallow a drag when drawing a new box. */}
      {showMarks && (
        <div className="pointer-events-none absolute inset-0">
          {drawn.map((m) => {
            const color = MARK_COLORS[m.color] ?? "#6366f1";
            // Put the caption inside the box when it sits near the top of the page,
            // otherwise it would cover the line of text above it.
            const insideTop = m.y < 0.04;
            return (
              <div
                key={m.id}
                className="group absolute rounded-[2px] border"
                style={{
                  left: `${m.x * 100}%`,
                  top: `${m.y * 100}%`,
                  width: `${m.width * 100}%`,
                  height: `${m.height * 100}%`,
                  borderColor: color,
                  // No fill: an outline keeps the value underneath fully legible.
                  backgroundColor: "transparent",
                  boxShadow: `0 0 0 1px ${color}33`,
                }}
              >
                <span
                  className={`absolute left-0 whitespace-nowrap rounded-sm px-1 text-[9px] font-semibold leading-[1.35] text-white opacity-70 ${
                    insideTop ? "top-0" : "-top-[13px]"
                  }`}
                  style={{ backgroundColor: color }}
                >
                  {labelForMark(m)}
                </span>
              </div>
            );
          })}
        </div>
      )}
      {draft && (
        <div
          className="absolute rounded-sm border-2 border-dashed border-indigo-600 bg-indigo-500/10"
          style={{
            left: `${Math.min(draft.x0, draft.x1) * 100}%`,
            top: `${Math.min(draft.y0, draft.y1) * 100}%`,
            width: `${Math.abs(draft.x1 - draft.x0) * 100}%`,
            height: `${Math.abs(draft.y1 - draft.y0) * 100}%`,
          }}
        />
      )}
    </div>
  );
}
