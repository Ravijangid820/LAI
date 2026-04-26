export interface ProjectFile {
  id: string;
  name: string;
  size: number;
  uploadDate: string;
  type: string;
  lines?: number;
}

export interface ChatAttachment {
  id: string;
  name: string;
  size: number;
  type: string;
}

export interface ChatMessage {
  id: string;
  message: string;
  sender: "user" | "assistant";
  timestamp: string;
  attachments?: ChatAttachment[];
}

export interface ProjectConversation {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  messages: ChatMessage[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  instructions: string;
  status: "active" | "completed" | "archived";
  owner: string;
  createdDate: string;
  files: ProjectFile[];
  teamMembers: number;
  conversations: ProjectConversation[];
}