import { useState, useRef, useEffect, useCallback } from "react";
import { useOutletContext } from "react-router";
import { Logo } from "@/react-app/components/Logo";
import { ChevronDown } from "lucide-react";
import {
  BellIcon,
  ManuscriptIcon,
  AlertIcon,
  SignalTowerIcon,
  CircuitBoltIcon,
} from "@/react-app/components/icons";
import {
  ChatMessage,
  ChatMessageData,
  ChatAttachment,
} from "@/react-app/components/chat/ChatMessage";
import { ChatInput } from "@/react-app/components/chat/ChatInput";
import { TypingIndicator } from "@/react-app/components/chat/TypingIndicator";
import { Button } from "@/react-app/components/ui/button";
import type { Conversation } from "@/react-app/components/DashboardLayout";
import { queryRAG, uploadDocument, analyzeContract } from "@/react-app/lib/ragApi";
import { randomId } from "@/react-app/utils/uuid";

const suggestedPrompts = [
  {
    Icon: ManuscriptIcon,
    text: "Analyze uploaded permits",
    desc: "Review BImSchG compliance",
  },
  {
    Icon: AlertIcon,
    text: "Check land lease risks",
    desc: "Identify contractual issues",
  },
  {
    Icon: SignalTowerIcon,
    text: "Environmental compliance",
    desc: "Verify BNatSchG requirements",
  },
  {
    Icon: CircuitBoltIcon,
    text: "Grid connection review",
    desc: "Analyze Einspeisezusage terms",
  },
];

interface OutletContextType {
  activeConversationId: string | null;
  setActiveConversationId: (id: string | null) => void;
  conversations: Conversation[];
  setConversations: (convs: Conversation[]) => void;
}

export default function DashboardChatPage() {
  const context = useOutletContext<OutletContextType>();
  const { activeConversationId, conversations } = context || {};

  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [isTyping, setIsTyping] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomAnchorRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const activeConversation = conversations?.find(
    (c) => c.id === activeConversationId,
  );

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

  useEffect(() => {
    const timer = setTimeout(() => forceScrollToBottom("smooth"), 0);
    return () => clearTimeout(timer);
  }, [messages.length, isTyping, forceScrollToBottom]);

  useEffect(() => {
    setMessages([]);
    setShowScrollBtn(false);
    setSessionId(null);
  }, [activeConversationId]);

  // ── Send message ──────────────────────────────────────────────────────────
  // CHANGED: replaced mock timeout + mockResponses with real queryRAG() call
  const handleSendMessage = async (
    content: string,
    attachments: ChatAttachment[],
  ) => {
    const userMessage: ChatMessageData = {
      id: randomId(),
      role: "user",
      content,
      attachments,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsTyping(true);
    setTimeout(() => forceScrollToBottom("smooth"), 0);

    try {
      let currentSessionId = sessionId;

      // Upload all document attachments — backend Docling accepts
      // PDF, DOCX/DOC, XLSX/XLS, TXT, CSV, MD. Match the same set the
      // ChatInput file picker offers; if Docling can't parse a specific
      // file the backend will return an error which is surfaced in chat.
      const SUPPORTED_DOC_EXTS = [".pdf", ".doc", ".docx", ".xlsx", ".xls", ".txt", ".csv", ".md"];
      const docAttachments = attachments.filter(
        (a) => a.file && SUPPORTED_DOC_EXTS.some(
          (ext) => a.file!.name.toLowerCase().endsWith(ext)
        )
      );

      if (docAttachments.length > 0) {
        setIsUploading(true);
        for (const attachment of docAttachments) {
          if (attachment.file) {
            const uploadResult = await uploadDocument(attachment.file, currentSessionId);
            currentSessionId = uploadResult.session_id;
            setSessionId(currentSessionId);

            // Show upload success message with an "Analyze contract" CTA.
            // The CTA is surfaced as a regular message so the user knows
            // the option exists; clicking it triggers /analyze-contract.
            const uploadMessage: ChatMessageData = {
              id: randomId(),
              role: "assistant",
              content:
                `📄 **Document uploaded:** ${uploadResult.filename}\n` +
                `- Pages: ${uploadResult.pages}\n` +
                `- Chunks: ${uploadResult.chunks}\n\n` +
                `You can ask questions about this document, or type **"analyze contract"** ` +
                `for a clause-by-clause review (issues, missing clauses, citations).`,
              timestamp: new Date(),
            };
            setMessages((prev) => [...prev, uploadMessage]);
          }
        }
        setIsUploading(false);
      }

      // Special command: "analyze contract" runs the clause-by-clause
      // analyzer on the currently-uploaded session document.
      const trimmed = content.trim().toLowerCase();
      const isAnalyzeCmd =
        currentSessionId &&
        (trimmed === "analyze contract" ||
         trimmed === "analyse vertrag" ||
         trimmed === "analysiere vertrag" ||
         trimmed.startsWith("/analyze"));

      if (isAnalyzeCmd) {
        const a = await analyzeContract(currentSessionId);
        const lines: string[] = [];
        lines.push(`**Contract analysis** — ${a.filename}`);
        lines.push(`Detected ${a.n_clauses} clauses (analysis took ${a.elapsed_s}s)`);
        lines.push("");
        if (a.missing_required_clauses.length > 0) {
          lines.push("### ❌ Missing required clauses");
          for (const m of a.missing_required_clauses) {
            lines.push(`- **[${m.severity.toUpperCase()}] ${m.type ?? ""}** — ${m.description}`);
          }
          lines.push("");
        }
        const flagged = a.clauses.filter((c) => c.issues && c.issues.length > 0);
        if (flagged.length > 0) {
          lines.push("### ⚠️ Flagged clauses");
          for (const c of flagged) {
            lines.push(`#### ${c.id} · ${c.type}`);
            if (c.summary) lines.push(`> ${c.summary}`);
            for (const i of c.issues) {
              lines.push(
                `- **[${i.severity.toUpperCase()}]** ${i.description}` +
                (i.recommendation ? `\n   _Empfehlung: ${i.recommendation}_` : "")
              );
            }
            if (c.citations.length > 0) {
              lines.push(`- 📎 ${c.citations.join(", ")}`);
            }
            lines.push("");
          }
        } else {
          lines.push("✅ No issues flagged in any clause.");
        }
        const aiMessage: ChatMessageData = {
          id: randomId(),
          role: "assistant",
          content: lines.join("\n"),
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, aiMessage]);
      } else {
        // Normal /query path
        const result = await queryRAG(content, currentSessionId);

        if (result.session_id) {
          setSessionId(result.session_id);
        }

        // Show mode badge inline so user can see whether retrieval ran.
        const modeBadge = result.mode && result.mode !== "chat"
          ? `\n\n<sub>_Mode: ${result.mode}_</sub>`
          : "";

        const aiMessage: ChatMessageData = {
          id: randomId(),
          role: "assistant",
          content: result.answer + modeBadge,
          timestamp: new Date(),
        };

        setMessages((prev) => [...prev, aiMessage]);
      }
    } catch (err: unknown) {
      setIsUploading(false);
      // Show the error as an assistant message so it's visible in chat
      const errorMessage: ChatMessageData = {
        id: randomId(),
        role: "assistant",
        content: `⚠️ **Error:** ${err instanceof Error ? err.message : "Could not reach the backend. Make sure the API server is running on the SSH server."}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsTyping(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const handleSuggestedPrompt = (prompt: string) =>
    handleSendMessage(prompt, []);

  const hasMessages = messages.length > 0;

  return (
    <div className="h-full w-full flex flex-col bg-background overflow-hidden">
      {/* ── Header ── */}
      <div className="flex-shrink-0 h-14 border-b border-border flex items-center justify-between px-6 bg-background/50 backdrop-blur">
        <div className="flex items-center gap-3">
          <Logo size="sm" />
          {activeConversation && (
            <>
              <span className="text-border/60 select-none">·</span>
              <span className="text-sm text-muted-foreground truncate max-w-xs">
                {activeConversation.title}
              </span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="relative h-9 w-9">
            <BellIcon className="w-5 h-5" />
            <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-primary rounded-full" />
          </Button>
        </div>
      </div>

      {/* ── Scrollable Messages ── */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 w-full min-h-0 overflow-y-auto flex flex-col relative"
      >
        {!hasMessages && !activeConversationId ? (
          <div className="flex-1 flex flex-col items-center justify-center py-16 px-4">
            <div className="flex items-center justify-center mb-6">
              <Logo size="lg" showText={false} />
            </div>
            <h2 className="text-2xl font-bold mb-2">Welcome to LAI</h2>
            <p className="text-muted-foreground text-center max-w-md mb-8">
              Your AI assistant for wind energy legal due diligence. Upload
              documents, ask questions, and get instant analysis.
            </p>
            <div className="grid grid-cols-2 gap-3 w-full max-w-lg">
              {suggestedPrompts.map((prompt, i) => (
                <button
                  key={i}
                  onClick={() => handleSuggestedPrompt(prompt.text)}
                  className="flex items-start gap-3 p-4 rounded-md bg-card border border-border/50 hover:border-slate-400 dark:hover:border-slate-600 transition-all text-left shadow-sm"
                >
                  <div className="flex-shrink-0 mt-0.5 text-slate-500 dark:text-slate-400">
                    <prompt.Icon className="w-5 h-5" />
                  </div>
                  <div>
                    <p className="text-sm font-medium">{prompt.text}</p>
                    <p className="text-xs text-muted-foreground">
                      {prompt.desc}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : !hasMessages && activeConversationId ? (
          <div className="flex-1 flex flex-col items-center justify-center py-16 px-4">
            <div className="flex items-center justify-center mb-4">
              <Logo size="lg" showText={false} />
            </div>
            <h3 className="text-lg font-semibold mb-2">New Conversation</h3>
            <p className="text-muted-foreground text-center max-w-md">
              Ask me anything about wind energy permits, contracts, or legal
              compliance. You can also upload documents for analysis.
            </p>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto w-full px-4 py-4">
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                message={message}
                onRegenerate={() => {}}
              />
            ))}
            {(isTyping || isUploading) && (
              <TypingIndicator message={isUploading ? "Uploading document..." : "LAI is thinking..."} />
            )}
            <div ref={bottomAnchorRef} style={{ height: 1 }} />
          </div>
        )}

        {showScrollBtn && hasMessages && (
          <button
            onClick={() => forceScrollToBottom("smooth")}
            className="sticky bottom-4 left-1/2 -translate-x-1/2 w-fit mx-auto flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-primary text-primary-foreground shadow-lg hover:bg-primary/90 transition-all text-xs font-medium z-10"
          >
            <ChevronDown className="w-3.5 h-3.5" />
            Latest
          </button>
        )}
      </div>

      {/* ── Input ── */}
      <div className="flex-shrink-0 border-t border-border">
        <div className="max-w-4xl mx-auto w-full px-4 py-4">
          <ChatInput
            onSend={handleSendMessage}
            disabled={isTyping || isUploading}
            placeholder={isUploading ? "Uploading document..." : "Ask LAI about permits, contracts, or upload documents..."}
            inputRef={inputRef}
          />
        </div>
      </div>
    </div>
  );
}
