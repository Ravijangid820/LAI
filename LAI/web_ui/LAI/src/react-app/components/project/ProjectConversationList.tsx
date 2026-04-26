import { ProjectConversation } from "./types";

interface ProjectConversationListProps {
  conversations: ProjectConversation[];
  onSelectConversation: (id: string) => void;
}

export function ProjectConversationList({
  conversations,
  onSelectConversation,
}: ProjectConversationListProps) {
  return (
    <div className="flex-1 overflow-y-auto pb-4 space-y-0.5">
      {conversations.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
          No conversations yet. Start by typing above.
        </div>
      ) : (
        conversations.map((conv, idx) => (
          <div key={conv.id}>
            {/* Clicking navigates to full chat view */}
            <button
              className="w-full text-left px-3 py-3 rounded-lg transition-colors hover:bg-card/50 group"
              onClick={() => onSelectConversation(conv.id)}
            >
              <p className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">
                {conv.title}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Last message {conv.timestamp}
              </p>
            </button>

            {idx < conversations.length - 1 && (
              <div className="mx-3 border-b border-border/30" />
            )}
          </div>
        ))
      )}
    </div>
  );
}
