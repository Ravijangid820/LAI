import { useState } from "react";
import {
  ArrowLeft,
  Star,
  MoreVertical,
  Plus,
  Send,
  CheckCircle2,
  Archive,
  Trash2,
} from "lucide-react";
import { Button } from "@/react-app/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/react-app/components/ui/dropdown-menu";
import { Project, ProjectConversation, ChatAttachment } from "./types";
import { ProjectConversationList } from "./ProjectConversationList";
import { ProjectSidebar } from "./ProjectSidebar";
import { ProjectChatView } from "./ProjectChatView";

const mockResponses = [
  "I've analyzed the documents you uploaded. Here are the key findings for the wind energy due diligence:\n\n**Permit Status (BImSchG)**\n• The permit was issued on March 15, 2023 and is currently valid\n• Environmental impact assessment completed with minor conditions\n• Building permit aligned with local zoning requirements\n\n**Identified Risks**\n1. **Medium Risk**: Clause 4.2 of the land lease allows early termination with 12-month notice\n2. **Low Risk**: Grid connection agreement expires in 2035, requires renewal\n3. **High Risk**: Missing documentation for aviation lighting compliance\n\nWould you like me to elaborate on any of these findings?",
  "Based on my analysis of the environmental impact assessment:\n\n**Wildlife Protection Measures**\n• Bat activity monitoring required during peak seasons (April–October)\n• Bird collision prevention shutdowns implemented during migration periods\n• Compensatory measures for habitat displacement are compliant\n\n**Compliance Status**: The project meets current BNatSchG requirements, but I recommend reviewing the latest amendments to ensure continued compliance.",
  "I've reviewed the grid connection agreement (Einspeisezusage) with the following observations:\n\n**Key Terms**\n• Connection capacity: 45 MW at 110 kV level\n• Feed-in priority: Standard renewable energy priority applies\n• Duration: Valid until December 31, 2035\n\n**Risk Assessment**\n🟢 **Low Risk**: Current capacity allocation is sufficient\n🟡 **Medium Risk**: Renewal negotiations should begin by 2033\n🔴 **High Risk**: No backup connection agreement exists",
  "Here is a summary of the land lease agreement review:\n\n**Key Findings**\n• Total lease area: 12.4 hectares across 3 parcels\n• Lease term: 25 years with 2 optional 5-year extensions\n• Annual rent: €4,200/MW installed capacity\n\n**Critical Clauses**\n• Section 7.3: Force majeure clause is broadly drafted\n• Section 12.1: Change of control provision requires landowner consent\n• Section 15: Decommissioning obligations are well-defined",
];

interface ProjectDetailViewProps {
  project: Project;
  onBack: () => void;
  onComplete: (id: string) => void;
  onArchive: (id: string) => void;
  onDelete: (id: string) => void;
  onSaveInstructions: (projectId: string, text: string) => void;
  onAddFiles: (projectId: string, files: FileList) => void;
  onDeleteFile: (projectId: string, fileId: string) => void;
  onAddConversation: (
    projectId: string,
    conversation: ProjectConversation,
  ) => void;
  onAddMessage: (
    projectId: string,
    conversationId: string,
    userMsg: string,
    attachments: ChatAttachment[],
    aiResponse: string,
  ) => void;
}

export function ProjectDetailView({
  project,
  onBack,
  onComplete,
  onArchive,
  onDelete,
  onSaveInstructions,
  onAddFiles,
  onDeleteFile,
  onAddConversation,
  onAddMessage,
}: ProjectDetailViewProps) {
  const [openConversationId, setOpenConversationId] = useState<string | null>(
    null,
  );
  const [newChatInput, setNewChatInput] = useState("");
  const [responseIdx, setResponseIdx] = useState(0);

  const openConversation = project.conversations.find(
    (c) => c.id === openConversationId,
  );

  // ── Chat view ─────────────────────────────────────────────────────────────
  if (openConversationId && openConversation) {
    return (
      <div className="h-full flex flex-col bg-background overflow-hidden">
        {/* ✅ FIX: removed projectFiles prop — no longer in ProjectChatViewProps */}
        <ProjectChatView
          projectName={project.name}
          conversation={openConversation}
          onBack={() => setOpenConversationId(null)}
          onSendMessage={(msg, attachments) => {
            const aiResponse =
              mockResponses[responseIdx % mockResponses.length];
            setResponseIdx((i) => i + 1);
            onAddMessage(
              project.id,
              openConversationId,
              msg,
              attachments,
              aiResponse,
            );
          }}
        />
      </div>
    );
  }

  // ── Project detail: conversation list + sidebar ───────────────────────────
  const handleStartNewConversation = () => {
    if (!newChatInput.trim()) return;

    const timeStr = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const aiResponse = mockResponses[responseIdx % mockResponses.length];
    setResponseIdx((i) => i + 1);

    const newConv: ProjectConversation = {
      id: Date.now().toString(),
      title: newChatInput.slice(0, 60),
      lastMessage: newChatInput,
      timestamp: "Just now",
      messages: [
        {
          id: Date.now().toString(),
          message: newChatInput,
          sender: "user",
          timestamp: timeStr,
          attachments: [],
        },
        {
          id: (Date.now() + 1).toString(),
          message: aiResponse,
          sender: "assistant",
          timestamp: timeStr,
        },
      ],
    };

    onAddConversation(project.id, newConv);
    setNewChatInput("");
    setOpenConversationId(newConv.id);
  };

  return (
    <div className="h-full flex flex-col bg-background overflow-hidden">
      {/* Top nav bar */}
      <div className="flex-shrink-0 h-11 flex items-center px-4 border-b border-border/50 bg-background/95 backdrop-blur">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          All projects
        </button>
      </div>

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT PANEL */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <div className="flex items-center justify-between px-6 pt-5 pb-4 flex-shrink-0">
            <h1 className="text-2xl font-bold text-foreground tracking-tight">
              {project.name}
            </h1>
            <div className="flex items-center gap-1">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground"
                  >
                    <MoreVertical className="w-4 h-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {project.status === "active" && (
                    <DropdownMenuItem onClick={() => onComplete(project.id)}>
                      <CheckCircle2 className="w-4 h-4 mr-2" />
                      Mark as Completed
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => onArchive(project.id)}>
                    <Archive className="w-4 h-4 mr-2" />
                    Archive Project
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      onDelete(project.id);
                      onBack();
                    }}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:text-yellow-400"
              >
                <Star className="w-4 h-4" />
              </Button>
            </div>
          </div>

          {/* New conversation input */}
          <div className="px-6 pb-4 flex-shrink-0">
            <div className="bg-card/50 backdrop-blur rounded-2xl border border-border/50 overflow-hidden">
              <textarea
                className="w-full min-h-[52px] px-4 pt-3 pb-1 resize-none outline-none bg-transparent text-foreground placeholder-muted-foreground text-sm leading-relaxed"
                placeholder="Start a new conversation..."
                rows={2}
                value={newChatInput}
                onChange={(e) => setNewChatInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleStartNewConversation();
                  }
                }}
              />
              <div className="flex items-center justify-between px-3 py-2 border-t border-border/30">
                <button className="text-muted-foreground hover:text-foreground p-1 rounded-md hover:bg-muted/50 transition-colors">
                  <Plus className="w-4 h-4" />
                </button>
                <button
                  onClick={handleStartNewConversation}
                  disabled={!newChatInput.trim()}
                  className="disabled:opacity-40 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>

          {/* Conversations list */}
          <ProjectConversationList
            conversations={project.conversations}
            onSelectConversation={setOpenConversationId}
          />
        </div>

        {/* RIGHT SIDEBAR */}
        <ProjectSidebar
          instructions={project.instructions}
          files={project.files}
          projectId={project.id}
          onSaveInstructions={(text) => onSaveInstructions(project.id, text)}
          onAddFiles={onAddFiles}
          onDeleteFile={onDeleteFile}
        />
      </div>
    </div>
  );
}
