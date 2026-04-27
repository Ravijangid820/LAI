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
import {
  queryRAG,
  uploadDocument,
  analyzeContract,
  getSession,
  getAnalyzeProgress,
  type AnalyzeProgress,
} from "@/react-app/lib/ragApi";
import { randomId } from "@/react-app/utils/uuid";

// localStorage key for the active session id. One per active conversation
// (we scope by activeConversationId so users with multiple chats keep
// their uploaded contract isolated per chat).
const SESSION_KEY_PREFIX = "lai.session.";
function sessionKey(convId: string | undefined | null): string {
  return SESSION_KEY_PREFIX + (convId || "default");
}

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
  refreshConversations: () => Promise<void>;
}

export default function DashboardChatPage() {
  const context = useOutletContext<OutletContextType>();
  const {
    activeConversationId,
    conversations,
    setActiveConversationId,
    refreshConversations,
  } = context || {};

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

  // ── Session rehydration ───────────────────────────────────────────────
  // On conversation change (or first mount), look up a previously-saved
  // session_id in localStorage. If one exists, fetch its persisted
  // messages from the backend and replay them into the UI so the user's
  // chat history survives page refreshes and serve_rag restarts.
  useEffect(() => {
    setMessages([]);
    setShowScrollBtn(false);
    setSessionId(null);

    const stored = (() => {
      try {
        return window.localStorage.getItem(sessionKey(activeConversationId));
      } catch {
        return null;
      }
    })();
    if (!stored) return;

    let cancelled = false;
    (async () => {
      const result = await getSession(stored);
      if (cancelled) return;
      if (!result.ok) {
        // Only treat a TRUE 404 as a stale id worth clearing. Network
        // errors (e.g. serve_rag mid-restart) keep the id so we can
        // rehydrate on a later mount.
        if (result.reason === "not-found") {
          try { window.localStorage.removeItem(sessionKey(activeConversationId)); } catch {}
        }
        return;
      }
      const detail = result.session;
      setSessionId(detail.session_id);
      const replayed: ChatMessageData[] = detail.messages.map((m) => ({
        id: randomId(),
        role: m.role,
        content: m.content,
        timestamp: new Date((m.created_at || 0) * 1000),
      }));
      setMessages(replayed);
    })();

    return () => { cancelled = true; };
  }, [activeConversationId]);

  // Persist sessionId to localStorage when we have one. Don't auto-clear
  // on null — the rehydration effect above is the only place that should
  // remove a stored id, and only on confirmed 404. Otherwise an initial
  // null state on mount would wipe the value before rehydration can read it.
  useEffect(() => {
    if (!sessionId) return;
    try {
      const k = sessionKey(activeConversationId);
      window.localStorage.setItem(k, sessionId);
    } catch {
      // localStorage unavailable (private mode etc.) — ignore, just lose persistence
    }
  }, [sessionId, activeConversationId]);

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
            // Sync sidebar — make this conversation the active entry
            // and refresh the list so it appears immediately.
            setActiveConversationId?.(currentSessionId);
            refreshConversations?.();

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
        // Live progress message — updated in place every few seconds
        // while the long /analyze-contract POST is open.
        const progressMsgId = randomId();
        const progressMsg: ChatMessageData = {
          id: progressMsgId,
          role: "assistant",
          content: "🔄 **Analyse läuft…** Vorbereitung",
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, progressMsg]);

        const renderProgress = (p: AnalyzeProgress): string => {
          if (p.status === "error") {
            return `❌ **Analyse fehlgeschlagen** — ${p.error ?? "unbekannter Fehler"}`;
          }
          const elapsed = Math.max(0, Math.round(p.elapsed_s ?? 0));
          const elapsedStr = elapsed >= 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`;
          const pct = Math.round((p.percent ?? 0) * 100);
          let label = "Vorbereitung";
          if (p.step === "starting") label = "Starte Analyse";
          else if (p.step === "classifying") label = "Vertragstyp wird erkannt";
          else if (p.step === "classify_done") label = "Vertragstyp erkannt";
          else if (p.step === "preparing_context" || p.step === "preparing_context_done")
            label = "Kontext wird vorbereitet";
          else if (p.step === "tables_reconciled") label = "Tabellen abgeglichen";
          else if (p.step === "extracting_parcels") label = "Flurstücke werden extrahiert";
          else if (p.step === "parcels_done")
            label = `Flurstücke extrahiert (${p.current ?? 0})`;
          else if (p.step === "analyzing_clause")
            label = `Klausel ${p.current}/${p.total} wird analysiert`;
          else if (p.step === "clauses_done") label = "Klausel-Analyse abgeschlossen";
          else if (p.step === "whole_contract") label = "Gesamtvertrag wird geprüft";
          else if (p.step === "done") label = "Fertig";
          // Rough ETA — simple linear extrapolation, only meaningful when
          // we have non-zero progress.
          let etaStr = "";
          if (pct > 5 && pct < 100 && elapsed > 0) {
            const totalEst = elapsed / Math.max(p.percent ?? 0.001, 0.01);
            const remaining = Math.max(0, Math.round(totalEst - elapsed));
            etaStr = remaining >= 60
              ? ` · ~${Math.ceil(remaining / 60)} min verbleibend`
              : ` · ~${remaining}s verbleibend`;
          }
          return `🔄 **${label}** — ${pct}% · ${elapsedStr} elapsed${etaStr}`;
        };

        // Start polling. Stops when analyzeContract resolves below.
        // currentSessionId is guaranteed non-null here (isAnalyzeCmd
        // guards on it), but the TS narrowing doesn't survive into a
        // separate boolean variable — assert non-null explicitly.
        const sidForAnalyze: string = currentSessionId!;
        let pollDone = false;
        const pollInterval = window.setInterval(async () => {
          if (pollDone) return;
          const p = await getAnalyzeProgress(sidForAnalyze);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === progressMsgId ? { ...m, content: renderProgress(p) } : m,
            ),
          );
        }, 3000);

        let a;
        try {
          a = await analyzeContract(sidForAnalyze);
        } finally {
          pollDone = true;
          window.clearInterval(pollInterval);
        }

        // Replace the progress placeholder with the final result.
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
        // Replace the progress placeholder we inserted earlier with the
        // final analysis text — keeping the same id so React updates in
        // place rather than appending a new bubble.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === progressMsgId
              ? { ...m, content: lines.join("\n"), timestamp: new Date() }
              : m,
          ),
        );
      } else if (content.trim().length === 0 && docAttachments.length > 0) {
        // Upload-only — user attached a file with no question. The
        // "Document uploaded" confirmation we showed above is the
        // entire response; firing /query with empty content would
        // just produce a spurious greeting. Skip.
      } else if (content.trim().length === 0) {
        // Empty submit, no attachment either — nothing to do.
      } else {
        // Normal /query path
        const result = await queryRAG(content, currentSessionId);

        if (result.session_id) {
          setSessionId(result.session_id);
          // First message of a chat creates a session row server-side;
          // sync the sidebar so the new conversation shows up immediately.
          if (!activeConversationId || activeConversationId !== result.session_id) {
            setActiveConversationId?.(result.session_id);
            refreshConversations?.();
          }
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
