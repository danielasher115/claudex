import { FileText, Paperclip, X } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { api, type Artifact } from "../api";
import { Loading } from "./shared";
const FilePreview = lazy(() => import("./FilePreview").then(module => ({ default: module.FilePreview })));

export function useAttachments(key?: string) {
  const [files, setFiles] = useState<Artifact[]>(() => { try { return key ? JSON.parse(localStorage.getItem(key) ?? "[]") : []; } catch { return []; } });
  const [uploading, setUploading] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => { if (key) localStorage.setItem(key, JSON.stringify(files)); }, [files, key]);
  const upload = async (chosen: FileList | File[]) => {
    setError("");
    const batch = Array.from(chosen);
    setUploading(value => value + batch.length);
    await Promise.all(batch.map(async file => {
      try {
        if (file.size > 25 * 1024 * 1024) throw new Error("Files must be no larger than 25 MB");
        const data = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("File could not be read")); reader.readAsDataURL(file); });
        const prepared = await api<Artifact>("/api/artifacts", { name: file.name, data, kind: /\.(vtt|srt)$/i.test(file.name) ? "transcript" : "document" });
        setFiles(current => [...current.filter(a => a.id !== prepared.id), prepared]);
      } catch (e) { setError(`${file.name}: ${(e as Error).message}`); }
      finally { setUploading(value => value - 1); }
    }));
  };
  return { files, setFiles, uploading, error, upload };
}

export function AttachmentPicker({ attachments }: { attachments: ReturnType<typeof useAttachments> }) {
  const input = useRef<HTMLInputElement>(null);
  return <><input type="file" multiple ref={input} className="hidden" aria-label="Choose attachments" onChange={e => { if (e.target.files) void attachments.upload(e.target.files); e.target.value = ""; }} /><button type="button" className="icon-button" aria-label="Attach files" title="Attach documents, code, images or transcript summaries" disabled={attachments.uploading > 0} onClick={() => input.current?.click()}><Paperclip className="size-4" /></button></>;
}

export function AttachmentList({ files, remove, gallery = false }: { files: Artifact[]; remove?: (id: string) => void; gallery?: boolean }) {
  const [preview, setPreview] = useState<Artifact | null>(null);
  if (!files.length) return null;
  return <><div className="my-2 flex flex-wrap gap-2">{files.map(file => <div key={file.id} className={`flex max-w-full items-center gap-2 rounded-lg border border-line bg-code p-2 text-xs ${gallery ? "w-full sm:w-auto" : ""}`}>
    <button type="button" aria-label={`Preview ${file.name}`} onClick={() => setPreview(file)} className={`flex min-w-0 gap-2 rounded text-left hover:text-ink ${gallery ? "w-full flex-col items-start" : "items-center"}`}>
      {file.kind === "image" ? <img src={file.content_url} alt={file.name} loading="lazy" className={gallery ? "max-h-64 max-w-full rounded object-contain" : "size-10 shrink-0 rounded object-cover"} /> : <FileText className="size-5 shrink-0 text-faint" />}
      <span className="min-w-0 max-w-52"><span className="block truncate font-medium">{file.name}</span><span className="mt-1 block text-[10px] text-faint">{file.kind === "image" ? "Image ready" : file.pages ? `${file.pages} pages prepared` : `${file.characters.toLocaleString()} characters prepared`}</span></span>
    </button>
    {remove && <button type="button" aria-label={`Remove ${file.name}`} className="icon-button" onClick={() => remove(file.id)}><X className="size-3" /></button>}
  </div>)}</div>{preview && <Suspense fallback={<Loading label="Opening preview" />}><FilePreview key={preview.id} file={preview} close={() => setPreview(null)} /></Suspense>}</>;
}

export function AttachmentFeedback({ attachments }: { attachments: ReturnType<typeof useAttachments> }) {
  return <>{attachments.uploading > 0 && <div className="my-2"><Loading label={`Preparing ${attachments.uploading} file${attachments.uploading === 1 ? "" : "s"}`} /></div>}{attachments.error && <p role="alert" className="my-2 text-xs text-removed-ink">{attachments.error}</p>}</>;
}
