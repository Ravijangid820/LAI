import { useRef, useState, useEffect, useCallback } from "react";
import {
  ArrowLeft,
  Share2,
  ChevronDown,
  Mic,
  MicOff,
  Send,
  Paperclip,
} from "lucide-react";
import { ProjectConversation, ChatMessage, ChatAttachment } from "./types";
import { MarkdownRenderer } from "@/react-app/components/chat/MarkdownRenderer";
import { Logo } from "@/react-app/components/Logo";
import {
  ManuscriptIcon,
  CloseIcon,
  GearIcon,
} from "@/react-app/components/icons";
import { useSpeechRecognition } from "@/react-app/hooks/useSpeechRecognition";
import { cn } from "@/react-app/lib/utils";

// ── Typing indicator ──────────────────────────────────────────────────────────
function TypingIndicator() {
  return (
    <div className="flex items-start gap-3 py-2">
      <div className="w-7 h-7 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Logo size="sm" showText={false} />
      </div>
      <div className="bg-card/40 border border-border/30 rounded-2xl rounded-bl-sm px-4 py-3">
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:0ms]" />
          <span className="w-2 h-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:150ms]" />
          <span className="w-2 h-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}

interface ProjectChatViewProps {
  projectName: string;
  conversation: ProjectConversation;
  onBack: () => void;
  onSendMessage: (message: string, attachments: ChatAttachment[]) => void;
}

export function ProjectChatView({
  projectName,
  conversation,
  onBack,
  onSendMessage,
}: ProjectChatViewProps) {
  const [chatInput, setChatInput] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  // ── KEY FIX: isTyping is purely local, never tied to prop changes ─────────
  // Previously isTyping was set AFTER onSendMessage, so the prop update
  // (new message in conversation.messages) arrived before isTyping=true,
  // making the message flash before the typing indicator appeared.
  // Now: isTyping=true → render → THEN onSendMessage → THEN timer → isTyping=false
  const [isTyping, setIsTyping] = useState(false);

  // Track how many messages were in the conversation when we last saw it.
  // When new messages arrive from the parent, we turn off typing.
  const knownMsgCountRef = useRef(conversation.messages.length);

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomAnchorRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Speech recognition ────────────────────────────────────────────────────
  const handleTranscript = useCallback((fullText: string) => {
    setChatInput(fullText);
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
        textareaRef.current.style.height =
          Math.min(textareaRef.current.scrollHeight, 120) + "px";
      }
    });
  }, []);

  const { micState, errorMessage, isSupported, toggleListening } =
    useSpeechRecognition({ onTranscript: handleTranscript });

  const isListening = micState === "listening";

  // ── Scroll helpers ────────────────────────────────────────────────────────
  const forceScrollToBottom = useCallback(
    (behavior: ScrollBehavior = "smooth") => {
      bottomAnchorRef.current?.scrollIntoView({ behavior, block: "end" });
      const el = scrollContainerRef.current;
      if (el) el.scrollTo({ top: el.scrollHeight, behavior });
      setShowScrollBtn(false);
    },
    [],
  );

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBtn(dist > 120);
  }, []);

  // Initial scroll on mount
  useEffect(() => {
    forceScrollToBottom("instant");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Auto-scroll when messages or typing changes ───────────────────────────
  useEffect(() => {
    const timer = setTimeout(() => forceScrollToBottom("smooth"), 0);
    return () => clearTimeout(timer);
  }, [conversation.messages.length, isTyping, forceScrollToBottom]);

  // ── Detect new message arriving from parent → turn off typing ────────────
  // This is the clean way to handle the typing indicator lifecycle:
  // parent pushes new message → we detect count change → hide typing indicator
  useEffect(() => {
    const currentCount = conversation.messages.length;
    if (currentCount > knownMsgCountRef.current && isTyping) {
      knownMsgCountRef.current = currentCount;
      setIsTyping(false);
      // Auto-focus input after answer arrives
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [conversation.messages.length, isTyping]);

  // ── Send ──────────────────────────────────────────────────────────────────
  const handleSend = async () => {
    const text = chatInput.trim();
    if (!text && attachments.length === 0) return;
    if (isListening) toggleListening(chatInput);

    // Capture attachments before clearing
    const sentAttachments = [...attachments];

    // Clear input immediately
    setChatInput("");
    setAttachments([]);
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    // ── CORRECT ORDER ─────────────────────────────────────────────────────
    // Step 1: Show typing indicator FIRST (before any message appears)
    knownMsgCountRef.current = conversation.messages.length;
    setIsTyping(true);

    // Step 2: Force scroll to show typing indicator
    setTimeout(() => forceScrollToBottom("smooth"), 0);

    // Step 3: Small delay to let typing indicator render before message arrives
    await new Promise((r) => setTimeout(r, 30));

    // Step 4: Tell parent to add the user message (this will trigger re-render
    // with new conversation.messages, but isTyping=true so indicator stays)
    onSendMessage(text, sentAttachments);

    // Step 5: The useEffect watching conversation.messages.length will
    // call setIsTyping(false) and focus the input when the AI response arrives.
    // No need to manually setIsTyping(false) here — the effect handles it.
    // But as a safety fallback, cap at 15s:
    await new Promise((r) => setTimeout(r, 15000));
    if (isTyping) {
      setIsTyping(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setChatInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (!files) return;
    const next: ChatAttachment[] = Array.from(files).map((f) => ({
      id: Date.now().toString() + Math.random(),
      name: f.name,
      size: f.size,
      type: (f.name.split(".").pop() ?? "file").toUpperCase(),
    }));
    setAttachments((prev) => [...prev, ...next]);
    e.currentTarget.value = "";
  };

  const removeAttachment = (id: string) =>
    setAttachments((prev) => prev.filter((a) => a.id !== id));

  const canSend = chatInput.trim().length > 0 || attachments.length > 0;

  return (
    <div className="h-full flex flex-col bg-background overflow-hidden">
      {/* ── HEADER ── */}
      <div className="flex-shrink-0 h-10 border-b border-border/50 flex items-center justify-between px-5 bg-background/95 backdrop-blur">
        <div className="flex items-center gap-2 text-sm min-w-0">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors shrink-0"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>{projectName}</span>
          </button>
          <span className="text-border/70 shrink-0">/</span>
          <span className="text-foreground font-semibold truncate">
            {conversation.title}
          </span>
          <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 ml-0.5" />
        </div>
        <div className="flex items-center gap-1.5 shrink-0 ml-3">
          <button className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors">
            <ManuscriptIcon className="w-4 h-4" />
          </button>
          <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-sm font-medium text-foreground hover:bg-muted/30 transition-colors">
            <Share2 className="w-3.5 h-3.5" />
            Share
          </button>
        </div>
      </div>

      {/* ── MESSAGES ── */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 w-full min-h-0 overflow-y-auto flex flex-col relative"
      >
        {conversation.messages.length === 0 && !isTyping && (
          <div className="flex-1 flex flex-col items-center justify-center py-12 px-4">
            <div className="flex items-center justify-center mb-4">
              <Logo size="lg" showText={false} />
            </div>
            <h3 className="text-base font-semibold mb-2">New Conversation</h3>
            <p className="text-muted-foreground text-center max-w-sm text-sm">
              Ask me anything about wind energy permits, contracts, or legal
              compliance.
            </p>
          </div>
        )}

        {(conversation.messages.length > 0 || isTyping) && (
          <div className="max-w-4xl mx-auto w-full px-5 pt-5 pb-2 space-y-6">
            {conversation.messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
            {isTyping && <TypingIndicator />}
            {/* Anchor element — scroll target */}
            <div ref={bottomAnchorRef} style={{ height: 1 }} />
          </div>
        )}

        {showScrollBtn && (
          <button
            onClick={() => forceScrollToBottom("smooth")}
            className="sticky bottom-4 left-1/2 -translate-x-1/2 w-fit mx-auto flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-primary text-primary-foreground shadow-lg hover:bg-primary/90 transition-all text-xs font-medium z-10"
          >
            <ChevronDown className="w-3.5 h-3.5" />
            Latest
          </button>
        )}
      </div>

      {/* ── INPUT ── */}
      <div className="flex-shrink-0">
        <div className="max-w-4xl mx-auto w-full px-5 pt-3 pb-3">
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2">
              {attachments.map((att) => (
                <div
                  key={att.id}
                  className="flex items-center gap-1.5 bg-card border border-border/50 rounded-lg px-2.5 py-1.5 text-xs"
                >
                  <ManuscriptIcon className="w-3.5 h-3.5 text-primary" />
                  <span className="text-foreground max-w-[140px] truncate">
                    {att.name}
                  </span>
                  <button
                    onClick={() => removeAttachment(att.id)}
                    className="text-muted-foreground hover:text-destructive transition-colors ml-0.5"
                  >
                    <CloseIcon className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {isListening && (
            <div className="flex items-center gap-2 mb-2 px-1">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500" />
              </span>
              <span className="text-xs text-red-500 font-medium">
                Listening… speak now
              </span>
            </div>
          )}

          {micState === "error" && errorMessage && (
            <p className="text-xs text-destructive mb-2 px-1">{errorMessage}</p>
          )}
          {micState === "unsupported" && (
            <p className="text-xs text-muted-foreground mb-2 px-1">
              Voice input not supported in this browser. Try Chrome or Edge.
            </p>
          )}

          <div className="bg-card/60 backdrop-blur rounded-2xl border border-border/50 shadow-sm">
            <div className="flex items-center gap-2 px-3 pt-3 pb-2">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex-shrink-0 p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
                title="Attach document"
              >
                <Paperclip className="w-[18px] h-[18px]" />
              </button>

              <textarea
                ref={textareaRef}
                className={cn(
                  "flex-1 resize-none outline-none bg-transparent text-foreground text-sm leading-relaxed min-h-[24px] max-h-[120px] py-0.5",
                  isListening
                    ? "placeholder:text-red-400"
                    : "placeholder-muted-foreground",
                )}
                placeholder={
                  isListening
                    ? "Listening… speak now"
                    : "Ask LAI about permits, contracts, or upload documents..."
                }
                rows={1}
                value={chatInput}
                onChange={handleTextareaChange}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                disabled={isTyping}
              />

              <div className="flex items-center gap-1.5 flex-shrink-0">
                <button
                  onClick={() => toggleListening(chatInput)}
                  disabled={isTyping || !isSupported}
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
                  onClick={handleSend}
                  disabled={!canSend || isTyping}
                  className={cn(
                    "w-8 h-8 rounded-full flex items-center justify-center transition-all",
                    canSend && !isTyping
                      ? "bg-primary hover:bg-primary/90 text-primary-foreground shadow-sm"
                      : "bg-muted text-muted-foreground opacity-50 cursor-not-allowed",
                  )}
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between px-4 pb-2.5">
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground/60">
                <GearIcon className="w-3 h-3" />
                LAI analyzes legal documents for wind energy due diligence
              </span>
              <span className="text-xs text-muted-foreground/40">
                Press Enter to send, Shift+Enter for new line
              </span>
            </div>
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileSelect}
          accept=".pdf,.doc,.docx,.xlsx,.xls,.txt,.csv"
        />
      </div>
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────
function MessageBubble({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(true);
  const isUser = message.sender === "user";
  const isLong = message.message.length > 400;

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[72%] space-y-1.5">
          {message.attachments && message.attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 justify-end">
              {message.attachments.map((att) => (
                <div
                  key={att.id}
                  className="flex items-center gap-1.5 bg-card border border-border/50 rounded-lg px-2.5 py-1.5 text-xs"
                >
                  <ManuscriptIcon className="w-3.5 h-3.5 text-primary" />
                  <span className="text-foreground max-w-[120px] truncate">
                    {att.name}
                  </span>
                  <span className="text-muted-foreground/60 uppercase ml-0.5">
                    {att.type}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="bg-card border border-border/50 rounded-2xl rounded-br-sm px-4 py-3">
            <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">
              {message.message}
            </p>
          </div>
          <p className="text-xs text-muted-foreground/40 text-right px-1">
            {message.timestamp}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3">
      <div className="w-7 h-7 flex items-center justify-center flex-shrink-0 mt-0.5">
        <Logo size="sm" showText={false} />
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        <span className="text-xs text-muted-foreground font-medium">
          LAI Assistant
        </span>
        {isLong ? (
          <div className="bg-card/40 border border-border/30 rounded-xl rounded-tl-sm px-4 py-3 space-y-2">
            <div className={!expanded ? "line-clamp-6 overflow-hidden" : ""}>
              <MarkdownRenderer content={message.message} />
            </div>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-xs text-primary hover:text-primary/80 transition-colors font-medium"
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          </div>
        ) : (
          <div className="bg-card/40 border border-border/30 rounded-xl rounded-tl-sm px-4 py-3">
            <MarkdownRenderer content={message.message} />
          </div>
        )}
        <p className="text-xs text-muted-foreground/40">{message.timestamp}</p>
      </div>
    </div>
  );
}
