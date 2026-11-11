import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AttachmentList } from "./Attachments";
import type { Artifact } from "../api";

const image: Artifact = { id: "image", name: "image.png", mime: "image/png", kind: "image", size: 32, characters: 0, pages: null, image_count: 1, status: "prepared", content_url: "/api/artifacts/image/content" };
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("clicking an image opens an in-app preview with download and close controls", async () => {
  render(<AttachmentList files={[image]} />);
  fireEvent.click(screen.getByRole("button", { name: "Preview image.png" }));
  expect(await screen.findByRole("dialog", { name: "Preview of image.png" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Download" }).getAttribute("download")).toBe("image.png");
  expect(document.querySelector('a[target="_blank"]')).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("document previews show prepared text instead of navigating to a new tab", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ name: "notes.txt", mime: "text/plain", text: "The complete document", summary: "", images: [] }), { headers: { "content-type": "application/json" } })));
  render(<AttachmentList files={[{ ...image, id: "document", name: "notes.txt", kind: "document", mime: "text/plain", characters: 21, image_count: 0 }]} />);
  fireEvent.click(screen.getByRole("button", { name: "Preview notes.txt" }));
  expect(await screen.findByText("The complete document")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy file content" })).toBeTruthy();
});
