import { useState, useRef, useCallback, KeyboardEvent, RefObject } from "react";
import { Send, X, Paperclip, Mic, MicOff } from "lucide-react";
import { Button } from "@/react-app/components/ui/button";
import { cn } from "@/react-app/lib/utils";
import type { ChatAttachment } from "./ChatMessage";
import { ManuscriptIcon } from "@/react-app/components/icons";
import { useSpeechRecognition } from "@/react-app/hooks/useSpeechRecognition";

interface ChatInputProps {
  onSend: (message: string, attachments: ChatAttachment[]) => void;
  disabled?: boolean;
  placeholder?: string;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export function ChatInput({
  onSend,
  disabled,
  placeholder,
  inputRef,
}: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const internalTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const textareaRef: RefObject<HTMLTextAreaElement | null> =
    inputRef ?? internalTextareaRef;

  // ── Speech recognition ──────────────────────────────────────────────
  const handleTranscript = useCallback(
    (fullText: string) => {
      setMessage(fullText);
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
          textareaRef.current.style.height =
            Math.min(textareaRef.current.scrollHeight, 200) + "px";
        }
      });
    },
    [textareaRef],
  );

  const { micState, errorMessage, isSupported, toggleListening } =
    useSpeechRecognition({ onTranscript: handleTranscript });

  const isListening = micState === "listening";

  // ── Send ─────────────────────────────────────────────────────────────
  const handleSend = () => {
    if (!message.trim() && attachments.length === 0) return;
    if (isListening) toggleListening(message);
    onSend(message, attachments);
    setMessage("");
    setAttachments([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── File handling ────────────────────────────────────────────────────
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (!files) return;
    const newAttachments: ChatAttachment[] = Array.from(files).map((file) => ({
      id: crypto.randomUUID(),
      name: file.name,
      size: file.size,
      type: file.type,
      file,
    }));
    setAttachments((prev) => [...prev, ...newAttachments]);
    e.currentTarget.value = "";
  };

  const removeAttachment = (id: string) =>
    setAttachments((prev) => prev.filter((a) => a.id !== id));

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const files = e.dataTransfer.files;
    if (!files) return;
    const newAttachments: ChatAttachment[] = Array.from(files).map((file) => ({
      id: crypto.randomUUID(),
      name: file.name,
      size: file.size,
      type: file.type,
      file,
    }));
    setAttachments((prev) => [...prev, ...newAttachments]);
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
  };

  const canSend = message.trim().length > 0 || attachments.length > 0;

  return (
    <div className="flex flex-col gap-2">
      {/* Listening indicator */}
      {isListening && (
        <div className="flex items-center gap-2 px-1">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500" />
          </span>
          <span className="text-xs text-red-500 font-medium">
            Listening… speak now
          </span>
        </div>
      )}

      {/* Mic error messages */}
      {micState === "error" && errorMessage && (
        <p className="text-xs text-destructive px-1">{errorMessage}</p>
      )}
      {micState === "unsupported" && (
        <p className="text-xs text-muted-foreground px-1">
          Voice input not supported in this browser. Try Chrome or Edge.
        </p>
      )}

      {/* Main input box */}
      <div
        className={cn(
          "relative rounded-2xl border bg-card/60 backdrop-blur shadow-sm transition-all",
          isDragging
            ? "border-primary border-dashed bg-primary/5"
            : "border-border/50",
          "focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/20",
        )}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {/* Attachments preview */}
        {attachments.length > 0 && (
          <div className="p-3 pb-0 flex flex-wrap gap-2">
            {attachments.map((file) => (
              <div
                key={file.id}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-muted/50 border border-border/50 group"
              >
                <ManuscriptIcon className="w-4 h-4 text-primary flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate max-w-[150px]">
                    {file.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatFileSize(file.size)}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 opacity-50 group-hover:opacity-100"
                  onClick={() => removeAttachment(file.id)}
                >
                  <X className="w-3.5 h-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* Input row */}
        <div className="flex items-center gap-2 px-3 pt-3 pb-2">
          {/* Paperclip */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            className="flex-shrink-0 p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            title="Attach document"
          >
            <Paperclip className="w-[18px] h-[18px]" />
          </button>

          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder={
              isListening ? "Listening… speak now" : placeholder || "Ask LAI..."
            }
            disabled={disabled}
            rows={1}
            className={cn(
              "flex-1 resize-none outline-none bg-transparent text-foreground text-sm leading-relaxed min-h-[24px] max-h-[200px] py-0.5",
              isListening
                ? "placeholder:text-red-400"
                : "placeholder:text-muted-foreground",
            )}
          />

          {/* Mic + Send */}
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <button
              type="button"
              onClick={() => toggleListening(message)}
              disabled={disabled || !isSupported}
              title={
                !isSupported
                  ? "Not supported in this browser. Try Chrome or Edge."
                  : isListening
                    ? "Stop recording"
                    : "Start voice input"
              }
              className={cn(
                "relative p-1.5 rounded-lg transition-all duration-200",
                isListening
                  ? "text-red-500 hover:text-red-600 hover:bg-red-500/10"
                  : !isSupported
                    ? "text-muted-foreground/40 cursor-not-allowed"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/30",
              )}
            >
              {isListening && (
                <span className="absolute inset-0 rounded-lg animate-ping bg-red-400/20" />
              )}
              {isListening ? (
                <MicOff className="w-[18px] h-[18px] relative z-10" />
              ) : (
                <Mic className="w-[18px] h-[18px] relative z-10" />
              )}
            </button>

            <button
              type="button"
              onClick={handleSend}
              disabled={!canSend || disabled}
              className={cn(
                "w-8 h-8 rounded-full flex items-center justify-center transition-all",
                canSend && !disabled
                  ? "bg-primary hover:bg-primary/90 text-primary-foreground shadow-sm"
                  : "bg-muted text-muted-foreground opacity-50 cursor-not-allowed",
              )}
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Footer hint */}
        <div className="flex items-center justify-end px-4 pb-2.5">
          <span className="text-xs text-muted-foreground/40">
            Press Enter to send · Shift+Enter for new line
          </span>
        </div>
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileChange}
        accept=".pdf,.doc,.docx,.xlsx,.xls,.txt,.csv"
      />
    </div>
  );
}
