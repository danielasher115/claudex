import { createContext, lazy, Suspense, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, type Artifact } from "../api";
import { Dialog, Loading } from "./shared";

interface FileReference { sessionId: string; itemId: string; documentId?: string; documentPath?: string }
const Reference = createContext<FileReference | null>(null);
const FilePreview = lazy(() => import("./FilePreview").then(module => ({ default: module.FilePreview })));

export function useFileReference() { return useContext(Reference); }

export function FileReferenceScope({ sessionId, itemId, documentId, documentPath, children }: { sessionId?: string; itemId: string; documentId?: string; documentPath?: string; children: ReactNode }) {
  const value = useMemo(() => sessionId ? { sessionId, itemId, documentId, documentPath } : null, [sessionId, itemId, documentId, documentPath]);
  return <Reference.Provider value={value}>{children}</Reference.Provider>;
}

export function isLocalFileLink(href: string, origin = window.location.origin): boolean {
  if (!href || href.startsWith("#") || href.startsWith("?") || href.startsWith("//")) return false;
  if (/^[a-z]:(?:[\\/]|%2f|%5c)/i.test(href)) return true;
  let path = href;
  if (/^file:/i.test(href)) {
    try { const url = new URL(href); return !url.hostname || url.hostname === "localhost"; } catch { return false; }
  }
  if (/^https?:/i.test(href)) {
    try { const url = new URL(href); if (url.origin !== origin) return false; path = url.pathname; } catch { return false; }
  } else if (/^[a-z][a-z\d+.-]*:/i.test(href)) return false;
  return path !== "/" && !/^\/(?:api|sessions|assets)(?:\/|$)/.test(path);
}

function sourcePath(href: string): string {
  let value = href.split(/[?#]/)[0];
  try { if (/^(?:https?|file):/i.test(href)) value = new URL(href).pathname; } catch { /* Keep the recorded location visible if parsing fails. */ }
  try { value = decodeURIComponent(value); } catch { /* An invalid escape remains readable as recorded. */ }
  return value.replace(/:(?:L)?\d+(?::\d+)?$/, "");
}

function filename(href: string): string {
  const path = sourcePath(href).replaceAll("\\", "/");
  const value = path.split("/").pop()?.replace(/:(?:L)?\d+(?::\d+)?$/, "") || "file";
  try { return decodeURIComponent(value); } catch { return value; }
}

export function FileLink({ href, title, children }: { href: string; title?: string; children: ReactNode }) {
  const reference = useContext(Reference);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<Artifact | null>(null);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const close = () => { request.current?.abort(); setOpen(false); };
  const preview = async () => {
    if (!reference) return;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setFile(null); setError(""); setOpen(true);
    try {
      const artifact = await api<Artifact>(`/api/sessions/${encodeURIComponent(reference.sessionId)}/files/preview`, { item_id: reference.itemId, href, document_id: reference.documentId, document_path: reference.documentPath }, controller.signal);
      if (!controller.signal.aborted) setFile({ ...artifact, source_path: artifact.source_path || sourcePath(href) });
    } catch (failure) { if (!controller.signal.aborted) setError((failure as Error).message); }
  };
  if (!reference || !isLocalFileLink(href)) return <a href={href} title={title} target="_blank" rel="noreferrer">{children}</a>;
  return <><button type="button" aria-haspopup="dialog" className="inline cursor-pointer rounded-sm text-left font-medium underline underline-offset-2" title={title || `Preview ${filename(href)}`} onClick={() => void preview()}>{children}</button>{open && createPortal(file ? <Suspense fallback={<Dialog title={`Preview of ${filename(href)}`} subtitle={href} close={close}><div className="p-6"><Loading label="Opening preview" /></div></Dialog>}><FilePreview file={file} close={close} /></Suspense> : <Dialog title={`Preview of ${filename(href)}`} subtitle={href} close={close} className="file-preview"><div className="p-6">{error ? <p role="alert" className="text-sm text-removed-ink">{error}</p> : <Loading label="Opening preview" />}</div></Dialog>, document.body)}</>;
}
