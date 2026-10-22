import React, { createContext, useContext, useMemo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cleanMessageContent } from "@/lib/raphael-utils";

const PreContext = createContext(false);

export interface MarkdownContentProps {
  content: string;
  className?: string;
  sanitizeReasoning?: boolean;
}

export function MarkdownContent({
  content,
  className = "",
  sanitizeReasoning = true,
}: MarkdownContentProps) {
  const processedContent = useMemo(() => {
    if (!content) return "";
    return sanitizeReasoning ? cleanMessageContent(content) : content;
  }, [content, sanitizeReasoning]);

  return (
    <div className={`markdown-content break-words leading-relaxed text-sm ${className}`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children, ...props }) => (
            <h1
              {...props}
              className="mt-3.5 mb-2 font-display text-base font-bold uppercase tracking-wide text-foreground border-b border-grid/40 pb-1 first:mt-0"
            >
              {children}
            </h1>
          ),
          h2: ({ children, ...props }) => (
            <h2
              {...props}
              className="mt-3 mb-1.5 font-display text-sm font-bold uppercase tracking-wide text-foreground first:mt-0"
            >
              {children}
            </h2>
          ),
          h3: ({ children, ...props }) => (
            <h3
              {...props}
              className="mt-2.5 mb-1 font-display text-xs font-bold uppercase tracking-wider text-foreground first:mt-0"
            >
              {children}
            </h3>
          ),
          h4: ({ children, ...props }) => (
            <h4
              {...props}
              className="mt-2 mb-1 font-mono text-xs font-bold uppercase tracking-wider text-foreground first:mt-0"
            >
              {children}
            </h4>
          ),
          h5: ({ children, ...props }) => (
            <h5
              {...props}
              className="mt-1.5 mb-0.5 font-mono text-[11px] font-bold uppercase tracking-wider text-foreground first:mt-0"
            >
              {children}
            </h5>
          ),
          h6: ({ children, ...props }) => (
            <h6
              {...props}
              className="mt-1 mb-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground first:mt-0"
            >
              {children}
            </h6>
          ),
          p: ({ children, ...props }) => (
            <p {...props} className="my-1.5 leading-relaxed first:mt-0 last:mb-0">
              {children}
            </p>
          ),
          strong: ({ children, ...props }) => (
            <strong {...props} className="font-bold text-foreground">
              {children}
            </strong>
          ),
          em: ({ children, ...props }) => (
            <em {...props} className="italic text-foreground/90">
              {children}
            </em>
          ),
          ul: ({ children, ...props }) => (
            <ul {...props} className="my-2 list-disc list-outside pl-5 space-y-1 text-foreground/90">
              {children}
            </ul>
          ),
          ol: ({ children, ...props }) => (
            <ol {...props} className="my-2 list-decimal list-outside pl-5 space-y-1 text-foreground/90">
              {children}
            </ol>
          ),
          li: ({ children, ...props }) => (
            <li {...props} className="leading-relaxed">
              {children}
            </li>
          ),
          blockquote: ({ children, ...props }) => (
            <blockquote
              {...props}
              className="my-2.5 border-l-4 border-block-blue bg-panel/60 py-1.5 pl-3.5 pr-2 font-mono text-xs italic text-muted-foreground shadow-sm"
            >
              {children}
            </blockquote>
          ),
          hr: ({ ...props }) => <hr {...props} className="my-3 border-t-2 border-grid/40" />,
          pre: ({ children, ...props }) => (
            <pre
              {...props}
              className="my-2.5 max-w-full overflow-x-auto border-2 border-grid/60 bg-canvas p-3 font-mono text-xs text-foreground shadow-inner"
            >
              <PreContext.Provider value={true}>{children}</PreContext.Provider>
            </pre>
          ),
          code: ({ children, className: codeClass, ...props }) => {
            const inPre = useContext(PreContext);
            if (inPre) {
              return (
                <code {...props} className={codeClass || "font-mono text-xs text-foreground"}>
                  {children}
                </code>
              );
            }
            return (
              <code
                {...props}
                className="rounded-none border border-grid/40 bg-muted/70 px-1 py-0.5 font-mono text-[12px] font-semibold text-foreground shadow-xs"
              >
                {children}
              </code>
            );
          },
          table: ({ children, ...props }) => (
            <div className="my-3 max-w-full overflow-x-auto border-2 border-grid">
              <table {...props} className="w-full border-collapse text-xs">
                {children}
              </table>
            </div>
          ),
          thead: ({ children, ...props }) => (
            <thead {...props} className="border-b-2 border-grid bg-panel font-mono font-bold uppercase">
              {children}
            </thead>
          ),
          tbody: ({ children, ...props }) => (
            <tbody {...props} className="divide-y divide-grid/30">
              {children}
            </tbody>
          ),
          tr: ({ children, ...props }) => (
            <tr {...props} className="even:bg-panel/30">
              {children}
            </tr>
          ),
          th: ({ children, ...props }) => (
            <th {...props} className="border-r border-grid p-2 text-left font-mono text-[11px] font-bold uppercase tracking-wider text-foreground last:border-r-0">
              {children}
            </th>
          ),
          td: ({ children, ...props }) => (
            <td {...props} className="border-r border-grid/40 p-2 align-top text-foreground/90 last:border-r-0">
              {children}
            </td>
          ),
          a: ({ href, children, ...props }) => {
            const isSafe = href && /^(https?:\/\/|mailto:)/i.test(href);
            if (!isSafe) {
              return <span className="underline text-muted-foreground">{children}</span>;
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-block-blue underline underline-offset-2 hover:opacity-80 transition-opacity font-medium"
                {...props}
              >
                {children}
              </a>
            );
          },
        }}
      >
        {processedContent}
      </Markdown>
    </div>
  );
}
