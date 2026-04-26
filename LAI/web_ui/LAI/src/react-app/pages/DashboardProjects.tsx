import { useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";
import { Button } from "@/react-app/components/ui/button";
import { Input } from "@/react-app/components/ui/input";
import { Label } from "@/react-app/components/ui/label";
import { Textarea } from "@/react-app/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/react-app/components/ui/dropdown-menu";
import {
  PlusIcon,
  CaseFolderIcon,
  ManuscriptIcon,
  DotsVerticalIcon,
  TrashIcon,
  CloseIcon,
  NewFolderIcon,
  CalendarIcon,
  TeamIcon,
  AlertRingIcon,
  CheckRingIcon,
  SearchIcon,
  FilterIcon,
  ConsultIcon,
  ArchiveIcon,
} from "@/react-app/components/icons";
import { ProjectDetailView } from "@/react-app/components/project/ProjectDetailView";
import { INITIAL_PROJECTS } from "@/react-app/components/project/data";
import {
  Project,
  ProjectConversation,
  ProjectFile,
  ChatAttachment,
} from "@/react-app/components/project/types";

function getStatusColor(status: string) {
  switch (status) {
    case "active":
      return "bg-amber-500/10 text-amber-600 border-amber-500/20 dark:text-amber-500 dark:border-amber-500/30";
    case "completed":
      return "bg-emerald-500/10 text-emerald-600 border-emerald-500/20 dark:text-emerald-500 dark:border-emerald-500/30";
    case "archived":
      return "bg-slate-500/10 text-slate-600 border-slate-500/20 dark:text-slate-400 dark:border-slate-500/30";
    default:
      return "bg-slate-500/10 text-slate-600 border-slate-500/20 dark:text-slate-400 dark:border-slate-500/30";
  }
}

export default function DashboardProjectsPage() {
  const [projects, setProjects] = useState<Project[]>(INITIAL_PROJECTS);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [newProject, setNewProject] = useState({ name: "", description: "" });

  const selectedProject =
    projects.find((p) => p.id === selectedProjectId) ?? null;
  const filteredProjects = projects.filter((p) => {
    const matchSearch =
      p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      p.description.toLowerCase().includes(searchTerm.toLowerCase());
    const matchStatus = !filterStatus || p.status === filterStatus;
    return matchSearch && matchStatus;
  });

  const handleCreateProject = () => {
    if (!newProject.name.trim()) return;
    setProjects((prev) => [
      {
        id: Date.now().toString(),
        name: newProject.name,
        description: newProject.description,
        instructions: "",
        status: "active",
        owner: "You",
        createdDate: new Date().toISOString().split("T")[0],
        files: [],
        teamMembers: 1,
        conversations: [],
      },
      ...prev,
    ]);
    setNewProject({ name: "", description: "" });
    setShowCreateModal(false);
  };

  const handleDeleteProject = (id: string) => {
    setProjects((p) => p.filter((x) => x.id !== id));
    if (selectedProjectId === id) setSelectedProjectId(null);
  };
  const handleCompleteProject = (id: string) =>
    setProjects((p) =>
      p.map((x) => (x.id === id ? { ...x, status: "completed" } : x)),
    );
  const handleArchiveProject = (id: string) =>
    setProjects((p) =>
      p.map((x) => (x.id === id ? { ...x, status: "archived" } : x)),
    );
  const handleSaveInstructions = (projectId: string, text: string) =>
    setProjects((p) =>
      p.map((x) => (x.id === projectId ? { ...x, instructions: text } : x)),
    );

  const handleAddFiles = (projectId: string, files: FileList) => {
    const newFiles: ProjectFile[] = Array.from(files).map((f) => ({
      id: Date.now().toString() + Math.random(),
      name: f.name,
      size: f.size / (1024 * 1024),
      uploadDate: new Date().toISOString().split("T")[0],
      type: (f.name.split(".").pop() ?? "file").toUpperCase(),
      lines: Math.floor(Math.random() * 400) + 20,
    }));
    setProjects((p) =>
      p.map((x) =>
        x.id === projectId ? { ...x, files: [...newFiles, ...x.files] } : x,
      ),
    );
  };

  const handleDeleteFile = (projectId: string, fileId: string) =>
    setProjects((p) =>
      p.map((x) =>
        x.id === projectId
          ? { ...x, files: x.files.filter((f) => f.id !== fileId) }
          : x,
      ),
    );

  const handleAddConversation = (
    projectId: string,
    conv: ProjectConversation,
  ) =>
    setProjects((p) =>
      p.map((x) =>
        x.id === projectId
          ? { ...x, conversations: [conv, ...x.conversations] }
          : x,
      ),
    );

  const handleAddMessage = (
    projectId: string,
    conversationId: string,
    userMsg: string,
    attachments: ChatAttachment[] = [],
    aiResponse = "This is an automated response.",
  ) => {
    const timeStr = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    setProjects((p) =>
      p.map((x) =>
        x.id === projectId
          ? {
            ...x,
            conversations: x.conversations.map((c) =>
              c.id === conversationId
                ? {
                  ...c,
                  lastMessage: userMsg,
                  timestamp: "Just now",
                  messages: [
                    ...c.messages,
                    {
                      id: Date.now().toString(),
                      message: userMsg,
                      sender: "user" as const,
                      timestamp: timeStr,
                      attachments,
                    },
                    {
                      id: (Date.now() + 1).toString(),
                      message: aiResponse,
                      sender: "assistant" as const,
                      timestamp: timeStr,
                    },
                  ],
                }
                : c,
            ),
          }
          : x,
      ),
    );
  };

  if (selectedProjectId && selectedProject) {
    return (
      <div
        className="fixed inset-0 z-10"
        style={{ left: "var(--sidebar-width, 64px)" }}
      >
        <ProjectDetailView
          project={selectedProject}
          onBack={() => setSelectedProjectId(null)}
          onComplete={handleCompleteProject}
          onArchive={handleArchiveProject}
          onDelete={handleDeleteProject}
          onSaveInstructions={handleSaveInstructions}
          onAddFiles={handleAddFiles}
          onDeleteFile={handleDeleteFile}
          onAddConversation={handleAddConversation}
          onAddMessage={handleAddMessage}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Projects</h1>
          <p className="text-muted-foreground">
            Manage your wind energy due diligence projects
          </p>
        </div>
        <Button className="shadow-sm" onClick={() => setShowCreateModal(true)}>
          <PlusIcon className="w-4 h-4 mr-2" />
          New Project
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          {
            label: "Total Projects",
            value: projects.length,
            Icon: NewFolderIcon,
            iconClass: "text-slate-600 dark:text-slate-400",
            bgClass: "bg-slate-100 dark:bg-slate-800",
          },
          {
            label: "Active Projects",
            value: projects.filter((p) => p.status === "active").length,
            Icon: AlertRingIcon,
            iconClass: "text-amber-600 dark:text-amber-500",
            bgClass: "bg-amber-500/10",
          },
          {
            label: "Completed",
            value: projects.filter((p) => p.status === "completed").length,
            Icon: CheckRingIcon,
            iconClass: "text-emerald-600 dark:text-emerald-500",
            bgClass: "bg-emerald-500/10",
          },
        ].map(({ label, value, Icon, iconClass, bgClass }) => (
          <Card
            key={label}
            className="bg-card/50 backdrop-blur border-border/50"
          >
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">{label}</p>
                  <p className="text-2xl font-bold mt-2">{value}</p>
                </div>
                <div className={`p-2.5 rounded-md ${bgClass}`}>
                  <Icon className={`w-5 h-5 ${iconClass}`} />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <SearchIcon className="absolute left-3 top-3 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search projects..."
            className="pl-10"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              <FilterIcon className="w-4 h-4 mr-2" />
              Status
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setFilterStatus(null)}>
              All Status
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFilterStatus("active")}>
              Active
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFilterStatus("completed")}>
              Completed
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFilterStatus("archived")}>
              Archived
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredProjects.length === 0 ? (
          <div className="col-span-full text-center py-12">
            <CaseFolderIcon className="w-12 h-12 text-muted-foreground mx-auto mb-3 opacity-50" />
            <p className="text-muted-foreground">No projects found</p>
          </div>
        ) : (
          filteredProjects.map((project) => (
            <Card
              key={project.id}
              className="bg-card/50 backdrop-blur border-border/50 hover:border-primary/50 transition-colors cursor-pointer"
              onClick={() => setSelectedProjectId(project.id)}
            >
              <CardHeader className="pb-3 flex flex-row items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <CaseFolderIcon className="w-5 h-5 text-slate-500 dark:text-slate-400" />
                    <CardTitle className="text-lg">{project.name}</CardTitle>
                  </div>
                  <span
                    className={`inline-block text-xs font-medium px-2.5 py-1 rounded-md mt-2 border ${getStatusColor(project.status)}`}
                  >
                    {project.status}
                  </span>
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <DotsVerticalIcon className="w-4 h-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {project.status === "active" && (
                      <DropdownMenuItem
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCompleteProject(project.id);
                        }}
                      >
                        <CheckRingIcon className="w-4 h-4 mr-2" />
                        Mark Completed
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        handleArchiveProject(project.id);
                      }}
                    >
                      <ArchiveIcon className="w-4 h-4 mr-2" />
                      Archive
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteProject(project.id);
                      }}
                      className="text-destructive focus:text-destructive"
                    >
                      <TrashIcon className="w-4 h-4 mr-2" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {project.description || "No description"}
                </p>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <div className="flex items-center gap-1">
                    <CalendarIcon className="w-3.5 h-3.5" />
                    {project.createdDate}
                  </div>
                  <div className="flex items-center gap-1">
                    <TeamIcon className="w-3.5 h-3.5" />
                    {project.teamMembers}
                  </div>
                </div>
                <div className="border-t border-border/50 pt-3">
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <div className="flex items-center gap-1">
                      <ConsultIcon className="w-3.5 h-3.5" />
                      <span>{project.conversations.length} chats</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <ManuscriptIcon className="w-3.5 h-3.5" />
                      <span>{project.files.length} files</span>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <Card className="w-full max-w-md">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <CardTitle>Create New Project</CardTitle>
              <button
                onClick={() => setShowCreateModal(false)}
                className="text-muted-foreground hover:text-foreground"
              >
                <CloseIcon className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label htmlFor="name">Project Name</Label>
                <Input
                  id="name"
                  placeholder="e.g., Windtech Farm Phase 1"
                  value={newProject.name}
                  onChange={(e) =>
                    setNewProject({ ...newProject, name: e.target.value })
                  }
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="description">Description</Label>
                <Textarea
                  id="description"
                  placeholder="Project description..."
                  value={newProject.description}
                  onChange={(e) =>
                    setNewProject({
                      ...newProject,
                      description: e.target.value,
                    })
                  }
                  className="mt-1"
                  rows={3}
                />
              </div>
              <div className="flex gap-2 pt-2">
                <Button
                  variant="outline"
                  className="flex-1"
                  onClick={() => setShowCreateModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  className="flex-1"
                  onClick={handleCreateProject}
                  disabled={!newProject.name.trim()}
                >
                  Create Project
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
