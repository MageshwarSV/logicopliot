import type { ReactNode } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Tailwind max-width class for the panel (default max-w-md). */
  maxWidth?: string;
}

export function Modal({ open, onClose, title, children, maxWidth = "max-w-md" }: ModalProps) {
  if (!open) return null;

  // Rendered through a portal to <body> so it always centers on the viewport and is
  // never clipped by a parent with transform/filter/backdrop-filter (e.g. the header).
  //
  // The panel is capped at the viewport height and scrolls its own body. A tall popup — the
  // "Pick data" form has four groups of controls — otherwise grew past the bottom of the
  // screen and took its Cancel/Add buttons with it, leaving no way to finish the action.
  // The title stays pinned so you never lose track of what the popup is for.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 sm:items-center">
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div
        className={`relative flex max-h-[calc(100vh-2rem)] w-full ${maxWidth} flex-col rounded-2xl bg-white shadow-xl dark:bg-slate-900`}
      >
        {/* Fixed header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-4 dark:border-slate-800">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        {/* Scrolling body — min-h-0 is what actually lets a flex child scroll */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
