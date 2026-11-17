import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useRef } from "react";
import { Select } from "./Select";
import { useDropdownPosition } from "./dropdown";

const options = [{ value: "first", label: "First" }, { value: "second", label: "Second" }, { value: "third", label: "Third" }];
const rect = (left: number, top: number, width = 100, height = 30): DOMRect => ({ left, top, right: left + width, bottom: top + height, width, height, x: left, y: top, toJSON: () => ({}) });
let trigger: DOMRect;
beforeEach(() => {
  trigger = rect(100, 100);
  vi.stubGlobal("innerWidth", 800); vi.stubGlobal("innerHeight", 600);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 0; });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) { return this.getAttribute("role") === "combobox" ? trigger : rect(0, 0, 176, 112); });
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(110);
  HTMLElement.prototype.showPopover = vi.fn(function (this: HTMLElement) { this.setAttribute("data-open", ""); this.style.display = "block"; });
  HTMLElement.prototype.hidePopover = vi.fn(function (this: HTMLElement) { this.removeAttribute("data-open"); this.style.display = "none"; });
  const matches = HTMLElement.prototype.matches;
  vi.spyOn(HTMLElement.prototype, "matches").mockImplementation(function (this: HTMLElement, selector: string) { return selector === ":popover-open" ? this.hasAttribute("data-open") : matches.call(this, selector); });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function open() {
  render(<Select label="Sort" value="first" onChange={vi.fn()} options={options} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Sort" }));
  return screen.getByRole("listbox", { name: "Sort" });
}

test.each([{ top: 100, placement: "bottom", menuTop: 140 }, { top: 550, placement: "top", menuTop: 428 }])("points at its trigger when opening $placement", ({ top, placement, menuTop }) => {
  trigger = rect(100, top);
  const menu = open();
  expect(menu.dataset.placement).toBe(placement);
  expect(menu.style.top).toBe(`${menuTop}px`);
  expect(Number.parseFloat(menu.style.left) + Number.parseFloat(menu.style.getPropertyValue("--dropdown-arrow-x"))).toBe(150);
  expect(menu.querySelector(".dropdown-content")?.contains(within(menu).getByRole("option", { name: "First" }))).toBe(true);
});

test.each([0, 750])("keeps the menu onscreen and the pointer on the trigger at x=%s", left => {
  trigger = rect(left, 100, 50);
  const menu = open();
  const menuLeft = Number.parseFloat(menu.style.left);
  expect(menuLeft).toBeGreaterThanOrEqual(8);
  expect(menuLeft + Number.parseFloat(menu.style.width)).toBeLessThanOrEqual(792);
  expect(menuLeft + Number.parseFloat(menu.style.getPropertyValue("--dropdown-arrow-x"))).toBe(left + 25);
});

test("follows its trigger on container scroll and viewport resize", () => {
  const menu = open();
  trigger = rect(600, 550);
  fireEvent.scroll(document);
  expect(menu.dataset.placement).toBe("top");
  expect(menu.style.left).toBe("600px");
  vi.stubGlobal("innerWidth", 680); fireEvent.resize(window);
  expect(menu.style.left).toBe("496px");
  expect(Number.parseFloat(menu.style.left) + Number.parseFloat(menu.style.getPropertyValue("--dropdown-arrow-x"))).toBe(650);
});

test("limits scrolling to the content in a short viewport", () => {
  trigger = rect(200, 140); vi.stubGlobal("innerHeight", 260);
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(800);
  const menu = open();
  expect(menu.dataset.placement).toBe("top");
  expect(menu.style.top).toBe("8px");
  expect(menu.style.getPropertyValue("--dropdown-max-height")).toBe("122px");
  expect(menu.querySelector(".dropdown-content")).toBeTruthy();
});

test("preserves keyboard selection, Escape, and native popover dismissal", () => {
  const change = vi.fn();
  render(<Select label="Sort" value="first" onChange={change} options={options} />);
  const button = screen.getByRole("combobox", { name: "Sort" });
  const menu = document.getElementById(button.getAttribute("aria-controls")!)!;
  fireEvent.keyDown(button, { key: "ArrowDown" });
  expect(document.activeElement).toBe(screen.getByRole("option", { name: "Second" }));
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(document.activeElement).toBe(screen.getByRole("option", { name: "Third" }));
  fireEvent.click(document.activeElement!);
  expect(change).toHaveBeenCalledWith("third");
  expect(button.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(button);
  fireEvent.click(button);
  fireEvent.keyDown(screen.getByRole("option", { name: "First" }), { key: "Escape" });
  expect(button.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(button);
  menu.removeAttribute("data-open"); fireEvent(menu, new Event("toggle"));
  expect(button.getAttribute("aria-expanded")).toBe("false");
});


test("moves an open menu and rebinds scroll tracking when its anchor changes", () => {
  let second = rect(450, 300);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) { return this.dataset.anchor === "second" ? second : trigger; });
  function Menu({ anchor }: { anchor: string }) {
    const button = useRef<HTMLElement | null>(null), menu = useRef<HTMLDivElement>(null);
    useDropdownPosition(button, menu, true, { anchorKey: anchor, minWidth: 180 });
    return <><button data-anchor="first" ref={node => { if (anchor === "first") button.current = node; }}>First tab</button><button data-anchor="second" ref={node => { if (anchor === "second") button.current = node; }}>Second tab</button><div ref={menu} role="menu"><div className="dropdown-content">Actions</div></div></>;
  }
  const view = render(<Menu anchor="first" />);
  const menu = screen.getByRole("menu");
  expect(menu.style.left).toBe("100px");
  view.rerender(<Menu anchor="second" />);
  expect(menu.style.left).toBe("450px");
  expect(Number.parseFloat(menu.style.left) + Number.parseFloat(menu.style.getPropertyValue("--dropdown-arrow-x"))).toBe(500);
  second = rect(500, 300); fireEvent.scroll(document);
  expect(menu.style.left).toBe("500px");
});
