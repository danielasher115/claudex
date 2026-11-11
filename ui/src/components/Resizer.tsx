import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

/** The sidebar's width: what the viewer last dragged it to, kept between visits, within its limits at every window size. */
export const SIDEBAR = { initial: 320, min: 240, max: 560, key: "sidebar-width" };

/** ``wanted`` within the limits: at least ``min``, at most ``max`` and at most half of the window, so the page keeps its room. */
export function clampWidth(wanted: number, viewport: number): number {
  const most = Math.max(SIDEBAR.min, Math.min(SIDEBAR.max, Math.floor(viewport / 2)));
  return Math.round(Math.min(Math.max(wanted, SIDEBAR.min), most));
}

/** The width now, and a setter that keeps it once ``keep`` says so (at the end of a drag, not at every step of it). */
export function useSidebarWidth(): [number, (width: number, keep?: boolean) => void] {
  const [wanted, setWanted] = useState(() => Number(localStorage.getItem(SIDEBAR.key)) || SIDEBAR.initial);
  const [viewport, setViewport] = useState(() => window.innerWidth);
  useEffect(() => {
    const resized = () => setViewport(window.innerWidth);
    window.addEventListener("resize", resized);
    return () => window.removeEventListener("resize", resized);
  }, []);
  const set = (width: number, keep = true) => {
    const clamped = clampWidth(width, window.innerWidth);
    setWanted(clamped);
    if (keep) localStorage.setItem(SIDEBAR.key, String(clamped));
  };
  return [clampWidth(wanted, viewport), set];
}

/** The handle on a panel's right edge that drags its width. It captures the pointer, so the drag follows the mouse wherever it
 * goes until the button is let go, and the panel's edge stays where the handle was taken (the width is the pointer's place less
 * that offset, not a sum of moves). Escape puts the width back; a double click resets it; the arrow keys move it by 16px. */
export function Resizer({ width, setWidth, label }: { width: number; setWidth: (width: number, keep?: boolean) => void; label: string }) {
  const drag = useRef<{ start: number; offset: number; left: number; pointer: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  const end = (target: HTMLElement, restore: boolean) => {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    setDragging(false);
    document.body.classList.remove("resizing");
    if (target.hasPointerCapture?.(current.pointer)) target.releasePointerCapture(current.pointer);
    if (restore) setWidth(current.start);
    else setWidth(width);
  };

  useEffect(() => {
    if (!dragging) return;
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      const handle = document.querySelector<HTMLElement>("[data-resizer]");
      if (handle) end(handle, true);
    };
    window.addEventListener("keydown", escape, true);
    return () => window.removeEventListener("keydown", escape, true);
  });

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const panel = event.currentTarget.parentElement!.getBoundingClientRect();
    drag.current = { start: width, offset: event.clientX - panel.right, left: panel.left, pointer: event.pointerId };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    document.body.classList.add("resizing");
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || event.pointerId !== current.pointer) return;
    setWidth(event.clientX - current.offset - current.left, false);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 64 : 16;
    const next = { ArrowLeft: width - step, ArrowRight: width + step, Home: SIDEBAR.min, End: SIDEBAR.max }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setWidth(next);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={SIDEBAR.min}
      aria-valuemax={clampWidth(SIDEBAR.max, window.innerWidth)}
      aria-valuenow={width}
      tabIndex={0}
      title="drag to resize; double-click to reset"
      data-resizer
      className="group absolute inset-y-0 -right-1.5 z-20 w-3 cursor-col-resize touch-none select-none outline-none"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={(event) => end(event.currentTarget, false)}
      onPointerCancel={(event) => end(event.currentTarget, true)}
      onLostPointerCapture={(event) => end(event.currentTarget, false)}
      onDoubleClick={() => setWidth(SIDEBAR.initial)}
      onKeyDown={onKeyDown}
    >
      <span className={`absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 transition-colors group-hover:bg-accent/60 group-focus-visible:bg-accent ${dragging ? "bg-accent" : ""}`} />
    </div>
  );
}
