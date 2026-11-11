import { Check, ChevronDown } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";

export function Select({ value, onChange, options, label, className = "", disabled = false }: { value: string; onChange: (value: string) => void; options: { value: string; label: string }[]; label: string; className?: string; disabled?: boolean }) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const typed = useRef({ text: "", time: 0 });
  const selected = Math.max(0, options.findIndex(option => option.value === value));
  const focus = (index: number) => requestAnimationFrame(() => menu.current?.querySelectorAll<HTMLButtonElement>("[role=option]")[(index + options.length) % options.length]?.focus());
  const show = (index = selected) => {
    const rect = button.current!.getBoundingClientRect();
    const height = Math.min(288, options.length * 34 + 8);
    const element = menu.current!;
    element.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - Math.max(rect.width, 176) - 8))}px`;
    element.style.top = `${innerHeight - rect.bottom > height + 8 ? rect.bottom + 5 : Math.max(8, rect.top - height - 5)}px`;
    element.style.width = `${Math.max(rect.width, 176)}px`;
    element.showPopover(); setOpen(true); focus(index);
  };
  const close = () => { menu.current?.hidePopover(); setOpen(false); };
  const choose = (next: string) => { onChange(next); close(); button.current?.focus(); };
  const key = (event: KeyboardEvent, index: number) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : index + (event.key === "ArrowDown" ? 1 : -1);
      if (!open) show(next); else focus(next);
    } else if (event.key === "Escape") { event.preventDefault(); close(); button.current?.focus(); }
    else if (event.key === "Tab") close();
    else if (event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey) {
      typed.current = { text: Date.now() - typed.current.time < 600 ? typed.current.text + event.key : event.key, time: Date.now() };
      const next = options.findIndex(option => option.label.toLowerCase().startsWith(typed.current.text.toLowerCase()));
      if (next >= 0) { event.preventDefault(); if (open) focus(next); else show(next); }
    }
  };
  return <><button ref={button} type="button" role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={id} disabled={disabled} className={`styled-select ${className}`} onClick={() => open ? close() : show()} onKeyDown={event => key(event, selected)}><span className="min-w-0 truncate">{options.find(option => option.value === value)?.label ?? options[0]?.label}</span><ChevronDown className="ml-auto size-3 shrink-0 text-faint" /></button>
    <div ref={menu} id={id} popover="auto" role="listbox" aria-label={label} className="select-menu" onToggle={() => setOpen(menu.current?.matches(":popover-open") ?? false)}>{options.map((option, index) => <button type="button" key={option.value} role="option" aria-selected={value === option.value} tabIndex={-1} className="select-option" onClick={() => choose(option.value)} onKeyDown={event => key(event, index)}><span className="min-w-0 flex-1 truncate">{option.label}</span>{value === option.value && <Check className="size-3.5 shrink-0" />}</button>)}</div></>;
}
