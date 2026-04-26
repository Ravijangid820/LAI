import { useState } from "react";
import { 
  MessageSquare, 
  Plus, 
  Search, 
  MoreHorizontal,
  Trash2,
  Edit2,
  Star
} from "lucide-react";
import { Button } from "@/react-app/components/ui/button";
import { Input } from "@/react-app/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/react-app/components/ui/dropdown-menu";
import { cn } from "@/react-app/lib/utils";

export interface Conversation {
  id: string;
  title: string;
  preview: string;
  timestamp: Date;
  starred?: boolean;
}

interface ConversationListProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}

function formatDate(date: Date): string {
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return date.toLocaleDateString();
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onRename,
}: ConversationListProps) {
  const [searchQuery, setSearchQuery] = useState("");

  const filteredConversations = conversations.filter((conv) =>
    conv.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    conv.preview.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const groupedConversations = filteredConversations.reduce((acc, conv) => {
    const date = formatDate(conv.timestamp);
    if (!acc[date]) acc[date] = [];
    acc[date].push(conv);
    return acc;
  }, {} as Record<string, Conversation[]>);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <Button onClick={onNew} className="w-full glow-sm">
          <Plus className="w-4 h-4 mr-2" />
          New Chat
        </Button>
      </div>

      {/* Search */}
      <div className="p-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search conversations..."
            className="pl-9 h-9 bg-muted/50 border-transparent"
          />
        </div>
      </div>

      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto px-3 pb-4">
        {Object.entries(groupedConversations).map(([date, convs]) => (
          <div key={date} className="mb-4">
            <p className="text-xs font-medium text-muted-foreground mb-2 px-2">{date}</p>
            <div className="space-y-1">
              {convs.map((conv) => (
                <div
                  key={conv.id}
                  className={cn(
                    "group flex items-start gap-2 p-2.5 rounded-xl cursor-pointer transition-colors",
                    activeId === conv.id
                      ? "bg-primary/10 text-foreground"
                      : "hover:bg-muted/50"
                  )}
                  onClick={() => onSelect(conv.id)}
                >
                  <MessageSquare className={cn(
                    "w-4 h-4 mt-0.5 flex-shrink-0",
                    activeId === conv.id ? "text-primary" : "text-muted-foreground"
                  )} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{conv.title}</p>
                    <p className="text-xs text-muted-foreground truncate">{conv.preview}</p>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 opacity-0 group-hover:opacity-100 flex-shrink-0"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <MoreHorizontal className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => onRename(conv.id, conv.title)}>
                        <Edit2 className="w-4 h-4 mr-2" />
                        Rename
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Star className="w-4 h-4 mr-2" />
                        Star
                      </DropdownMenuItem>
                      <DropdownMenuItem 
                        className="text-destructive"
                        onClick={() => onDelete(conv.id)}
                      >
                        <Trash2 className="w-4 h-4 mr-2" />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              ))}
            </div>
          </div>
        ))}

        {filteredConversations.length === 0 && (
          <div className="text-center py-8 text-muted-foreground">
            <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No conversations found</p>
          </div>
        )}
      </div>
    </div>
  );
}
