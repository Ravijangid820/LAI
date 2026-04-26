import { useState } from "react";
import {
  Copy,
  Check,
  RotateCcw,
  ThumbsUp,
  ThumbsDown,
  Download,
  User,
} from "lucide-react";
import { Button } from "@/react-app/components/ui/button";
import { cn } from "@/react-app/lib/utils";
import { MarkdownRenderer } from "@/react-app/components/chat/MarkdownRenderer";
import { Logo } from "@/react-app/components/Logo";
import {
  ManuscriptIcon, // replaces FileText (lucide) — legal document/file
} from "@/react-app/components/icons";

export interface ChatAttachment {
  id: string;
  name: string;
  size: number;
  type: string;
  file?: File;
}

export interface ChatMessageData {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: ChatAttachment[];
  timestamp: Date;
}

interface ChatMessageProps {
  message: ChatMessageData;
  onRegenerate?: () => void;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export function ChatMessage({ message, onRegenerate }: ChatMessageProps) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  const handleCopy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className={cn("group flex gap-4 py-6", isUser ? "flex-row-reverse" : "")}
    >
      {/* ── Avatar ── */}
      {isUser ? (
        // User avatar — kept with gradient box + User lucide (no custom person-in-box equivalent)
        <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 bg-gradient-to-br from-primary to-indigo-600">
          <User className="w-5 h-5 text-white" />
        </div>
      ) : (
        // LAI Assistant avatar — Logo replaces Bot lucide (Image 1 fix)
        <div className="w-9 h-9 flex items-center justify-center flex-shrink-0">
          <Logo size="sm" showText={false} />
        </div>
      )}

      {/* ── Message Content ── */}
      <div
        className={cn("flex-1 space-y-3 min-w-0", isUser ? "text-right" : "")}
      >
        <div
          className={cn("flex items-center gap-2", isUser ? "justify-end" : "")}
        >
          <span className="text-sm font-medium">
            {isUser ? "You" : "LAI Assistant"}
          </span>
          <span className="text-xs text-muted-foreground">
            {message.timestamp.toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </div>

        {/* ── Attachments ── */}
        {message.attachments && message.attachments.length > 0 && (
          <div
            className={cn("flex flex-wrap gap-2", isUser ? "justify-end" : "")}
          >
            {message.attachments.map((file) => (
              <div
                key={file.id}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-muted/50 border border-border/50 max-w-xs"
              >
                {/* ManuscriptIcon replaces FileText (lucide) — legal manuscript for doc files */}
                <ManuscriptIcon className="w-4 h-4 text-primary flex-shrink-0" />
                <div className="flex-1 min-w-0 text-left">
                  <p className="text-sm font-medium truncate">{file.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatFileSize(file.size)}
                  </p>
                </div>
                {/* Download — no custom equivalent, kept from lucide */}
                <Button variant="ghost" size="icon" className="h-7 w-7">
                  <Download className="w-3.5 h-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* ── Message bubble ── */}
        <div
          className={cn(
            "prose prose-sm dark:prose-invert max-w-none",
            isUser ? "text-right" : "",
          )}
        >
          <div
            className={cn(
              "inline-block px-4 py-3 rounded-2xl",
              isUser
                ? "bg-primary text-primary-foreground rounded-tr-sm"
                : "bg-muted/50 rounded-tl-sm",
            )}
          >
            {isUser ? (
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {message.content}
              </p>
            ) : (
              <MarkdownRenderer content={message.content} />
            )}
          </div>
        </div>

        {/* ── Actions — assistant only ── */}
        {/* Copy, RotateCcw, ThumbsUp, ThumbsDown — no custom equivalents, kept from lucide */}
        {!isUser && (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={handleCopy}
            >
              {copied ? (
                <Check className="w-4 h-4 text-green-500" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={onRegenerate}
            >
              <RotateCcw className="w-4 h-4" />
            </Button>
            <div className="w-px h-4 bg-border mx-1" />
            <Button variant="ghost" size="icon" className="h-8 w-8">
              <ThumbsUp className="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="icon" className="h-8 w-8">
              <ThumbsDown className="w-4 h-4" />
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
