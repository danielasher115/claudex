import { useLayoutEffect, type RefObject } from "react";
import "../dropdowns.css";

export function useDropdownPosition(trigger: RefObject<HTMLElement | null>, menu: RefObject<HTMLElement | null>, open: boolean, { minWidth = 176, align = "start", anchorKey }: { minWidth?: number; align?: "start" | "end"; anchorKey?: string | number } = {}) {
  useLayoutEffect(() => {
    const button = trigger.current, element = menu.current;
    if (!open || !button || !element) return;
    const content = element.querySelector<HTMLElement>(".dropdown-content") ?? element;
    const position = () => {
      const rect = button.getBoundingClientRect();
      const width = Math.min(Math.max(rect.width, minWidth), Math.max(0, innerWidth - 16));
      const left = Math.max(8, Math.min(align === "end" ? rect.right - width : rect.left, innerWidth - width - 8));
      element.style.width = `${width}px`;
      const height = Math.min(288, content.scrollHeight + 2);
      const below = Math.max(0, innerHeight - rect.bottom - 18), above = Math.max(0, rect.top - 18);
      const placement = height <= below || below >= above ? "bottom" : "top";
      const maxHeight = Math.min(288, placement === "bottom" ? below : above);
      element.style.left = `${left}px`;
      element.style.top = `${placement === "bottom" ? Math.min(innerHeight - 8, rect.bottom + 10) : Math.max(8, rect.top - 10 - Math.min(height, maxHeight))}px`;
      element.style.setProperty("--dropdown-max-height", `${maxHeight}px`);
      element.style.setProperty("--dropdown-arrow-x", `${Math.max(12, Math.min(width - 12, rect.left + rect.width / 2 - left))}px`);
      element.dataset.placement = placement;
    };
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(position);
    observer?.observe(button); observer?.observe(content);
    return () => { window.removeEventListener("resize", position); window.removeEventListener("scroll", position, true); observer?.disconnect(); };
  }, [trigger, menu, open, minWidth, align, anchorKey]);
}
