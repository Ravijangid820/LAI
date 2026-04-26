// src/react-app/components/chat/MarkdownRenderer.tsx
import ReactMarkdown from "react-markdown";

interface Props {
  content: string;
}

export function MarkdownRenderer({ content }: Props) {
  return (
    <ReactMarkdown
      components={{
        h1: ({ children }) => (
          <h1 className="text-xl font-bold text-foreground mb-3 mt-4">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="text-lg font-semibold text-foreground mb-2 mt-3">
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="text-base font-semibold text-foreground mb-2 mt-3">
            {children}
          </h3>
        ),
        strong: ({ children }) => (
          <strong className="font-semibold text-foreground">{children}</strong>
        ),
        ul: ({ children }) => (
          <ul className="list-disc list-inside space-y-1 my-2 text-foreground">
            {children}
          </ul>
        ),
        ol: ({ children }) => (
          <ol className="list-decimal list-inside space-y-1 my-2 text-foreground">
            {children}
          </ol>
        ),
        li: ({ children }) => (
          <li className="text-sm leading-relaxed">{children}</li>
        ),
        p: ({ children }) => (
          <p className="text-sm leading-relaxed mb-2 text-foreground">
            {children}
          </p>
        ),
        code: ({ children }) => (
          <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono text-primary">
            {children}
          </code>
        ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-primary pl-3 my-2 text-muted-foreground italic">
            {children}
          </blockquote>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
