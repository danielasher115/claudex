import { Download } from "lucide-react";
import { useEffect, useState, type MouseEvent } from "react";
import { api, type Artifact } from "../api";
import { CopyButton, Dialog, Loading } from "./shared";
import { Markdown, fenced } from "./Markdown";
import { FileReferenceScope, useFileReference } from "./FileLinks";

const LANGUAGES: Record<string, string> = { py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", jsonl: "json", md: "markdown", sh: "bash", yml: "yaml", yaml: "yaml", toml: "ini", html: "xml", xml: "xml", css: "css", sql: "sql", rs: "rust", go: "go", cs: "csharp", diff: "diff", patch: "diff" };

function followSection(event: MouseEvent<HTMLDivElement>) {
  const anchor = event.target instanceof Element ? event.target.closest("a") : null;
  const href = anchor?.getAttribute("href");
  if (!href?.startsWith("#")) return;
  event.preventDefault();
  let target: string;
  try { target = decodeURIComponent(href.slice(1)); } catch { return; }
  const counts = new Map<string, number>();
  for (const heading of event.currentTarget.querySelectorAll<HTMLElement>("h1,h2,h3,h4,h5,h6")) {
    const base = (heading.textContent ?? "").toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s+/g, "-");
    const count = counts.get(base) ?? 0; counts.set(base, count + 1);
    if (target === (heading.id || (count ? `${base}-${count}` : base))) { heading.scrollIntoView({ block: "start" }); heading.tabIndex = -1; heading.focus({ preventScroll: true }); break; }
  }
}

export function FilePreview({ file, close }: { file: Artifact; close: () => void }) {
  const [preview, setPreview] = useState<{ text: string; summary: string; images: string[] } | null>(null);
  const [error, setError] = useState("");
  const [source, setSource] = useState(false);
  const reference = useFileReference();
  useEffect(() => {
    if (file.kind === "image") return;
    const controller = new AbortController();
    api<{ text: string; summary: string; images: string[] }>(`/api/artifacts/${file.id}/preview`, undefined, controller.signal)
      .then(data => { if (!controller.signal.aborted) setPreview(data); })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [file.id, file.kind]);
  const text = preview ? (preview.summary ? `Transcript summary\n${preview.summary}\n\n` : "") + preview.text : "";
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  const markdown = ["md", "markdown", "mdown"].includes(extension) || file.mime === "text/markdown";
  return <Dialog title={`Preview of ${file.name}`} subtitle={file.source_path} close={close} className="file-preview" actions={<>
    {text && <><span className="hidden shrink-0 text-xs text-faint sm:inline">{text.replace(/\n$/, "").split("\n").length.toLocaleString()} lines</span><CopyButton text={text} label="Copy file content" /></>}
    <a href={file.content_url} download={file.name} className="secondary-button !px-2 !py-1"><Download className="size-3.5" />Download</a>
  </>}>{markdown && preview && <div className="shrink-0 border-b border-line px-4 py-2"><div role="group" aria-label="Markdown view" className="segmented !w-fit !p-0.5"><button type="button" className={source ? "" : "selected"} aria-pressed={!source} onClick={() => setSource(false)}>Preview</button><button type="button" className={source ? "selected" : ""} aria-pressed={source} onClick={() => setSource(true)}>Source</button></div></div>}<div className="min-h-0 overflow-auto">
    {file.kind === "image" ? <img src={file.content_url} alt={file.name} className="mx-auto max-h-[80vh] max-w-full object-contain p-4" /> : <>
      {!preview && !error && <div className="p-6"><Loading label="Loading preview" /></div>}
      {error && <p role="alert" className="p-6 text-sm text-removed-ink">Could not load this preview: {error}</p>}
      {text && (markdown && !source ? <div onClickCapture={followSection} className="file-preview-markdown px-6 py-4"><FileReferenceScope sessionId={reference?.sessionId} itemId={reference?.itemId ?? ""} documentId={file.id} documentPath={file.source_path}><Markdown text={text} /></FileReferenceScope></div> : <div className="px-4 py-1 [&_pre]:max-h-none"><Markdown text={fenced(text, LANGUAGES[extension])} /></div>)}
      {preview?.images.map((url, index) => <figure key={url} className="border-t border-line p-4"><figcaption className="mb-2 text-xs text-faint">{file.name} · {file.mime === "application/pdf" ? "Page" : "Figure"} {index + 1}</figcaption><img src={url} alt={`${file.name} ${file.mime === "application/pdf" ? "page" : "figure"} ${index + 1}`} className="mx-auto max-w-full object-contain" /></figure>)}
      {preview && !text && !preview.images.length && <p className="p-6 text-sm text-faint">No preview is available for this file. Download it to open the original.</p>}
    </>}
  </div></Dialog>;
}
