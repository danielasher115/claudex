import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Children, isValidElement, type ReactNode } from "react";
import { CopyButton } from "./shared";
import { FileLink, isLocalFileLink } from "./FileLinks";

export function fenced(text: string, language = "plaintext") {
  const longest = Math.max(2, ...[...text.matchAll(/`+/g)].map(match => match[0].length));
  const fence = "`".repeat(longest + 1);
  return `${fence}${language}\n${text.replace(/\n$/, "")}\n${fence}`;
}

export function Markdown({ text }: { text: string }) {
  let displayed = text;
  if (text.trim().startsWith("{") || text.trim().startsWith("[")) {
    try {
      const value = JSON.parse(text);
      if (value && typeof value === "object") {
        displayed = fenced(JSON.stringify(value, null, 2), "json");
      }
    } catch { /* A partial streaming reply remains ordinary text until valid JSON arrives. */ }
  }
  return <div className="prose prose-stone dark:prose-invert max-w-none prose-pre:bg-code prose-pre:text-ink prose-pre:border prose-pre:border-line prose-code:before:content-none prose-code:after:content-none prose-p:my-2 prose-li:my-0.5 prose-headings:font-semibold break-words">
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]} urlTransform={url => isLocalFileLink(url) ? url : defaultUrlTransform(url)} components={{ pre: CodeBlock, a: ({ children, href, title }) => <FileLink href={href ?? ""} title={title}>{children}</FileLink> }}>{displayed}</ReactMarkdown>
  </div>;
}

function plainText(node: ReactNode): string {
  return Children.toArray(node).map(child => typeof child === "string" || typeof child === "number" ? String(child) : isValidElement<{ children?: ReactNode }>(child) ? plainText(child.props.children) : "").join("");
}

function CodeBlock({ children }: { children?: ReactNode }) {
  return <div className="group/code relative"><pre>{children}</pre><div className="absolute right-2 top-2 rounded-md bg-code opacity-0 group-hover/code:opacity-100 focus-within:opacity-100"><CopyButton text={plainText(children).replace(/\n$/, "")} label="Copy code" /></div></div>;
}
