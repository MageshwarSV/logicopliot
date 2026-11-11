import { useLayoutEffect, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

interface FloatingPosition {
  top: number;
  left: number;
  width: number;
}

/**
 * Tracks an anchor element's on-screen position while a panel anchored to it is open, so that
 * panel can be rendered via a portal (see FloatingPanel below) and still land exactly under the
 * anchor. Recomputed the moment it opens, and on every scroll/resize while it stays open - the
 * anchor can move (a scrolled ancestor, a resized window) without this component re-rendering
 * on its own otherwise.
 */
export function useFloatingPosition(
  anchorRef: RefObject<HTMLElement | null>,
  open: boolean,
): FloatingPosition | null {
  const [position, setPosition] = useState<FloatingPosition | null>(null);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    const update = () => {
      const el = anchorRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      setPosition({ top: rect.bottom, left: rect.left, width: rect.width });
    };
    update();
    // capture: true - a scroll on ANY ancestor (not just window) moves the anchor, e.g. a
    // scrolled card body, not only the page itself.
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [open, anchorRef]);

  return position;
}

/**
 * A dropdown/search-results panel rendered into document.body via a portal, positioned with
 * position:fixed at its anchor's own on-screen rect (see useFloatingPosition above) - escapes
 * any ancestor's overflow:hidden (every Card uses one - see Card.tsx) instead of being silently
 * clipped by it the way a plain position:absolute panel nested inside that Card otherwise is.
 * Found live: ERP Quotation Name and ERP Quotation Branch's own search-result panels, and the
 * plain combo-dropdown panel (CustomsHouseCode, Erp Entry Name, ...) all three sat inside a
 * Card and rendered invisibly clipped at its bottom edge the moment results were tall enough to
 * reach it.
 *
 * Usage: give the wrapping <div> around the input a ref, pass that ref here as anchorRef, and
 * render this in the input's place instead of a plain `{open && <div className="absolute ...">}`.
 * className styles the panel itself (background, border, max-height, etc.) - position and width
 * are handled here, so an absolute/w-full in that className is neither needed nor wanted.
 */
export function FloatingPanel({
  anchorRef,
  open,
  children,
  className,
}: {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  children: ReactNode;
  className?: string;
}) {
  const position = useFloatingPosition(anchorRef, open);
  if (!open || !position) return null;
  return createPortal(
    <div
      // Lets a caller's own blur handler recognise focus landing in here as still "inside the
      // field" even though this is no longer a DOM descendant of it (see this component's own
      // docstring) - checked via e.relatedTarget?.closest("[data-floating-panel-root]").
      data-floating-panel-root=""
      className={className}
      style={{ position: "fixed", top: position.top, left: position.left, width: position.width }}
    >
      {children}
    </div>,
    document.body,
  );
}
