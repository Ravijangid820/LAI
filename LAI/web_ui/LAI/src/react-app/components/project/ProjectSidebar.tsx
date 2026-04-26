import { ProjectFile } from "./types";
import { ProjectInstructions } from "./ProjectInstructions";
import { ProjectFileGrid } from "./ProjectFileGrid";

interface ProjectSidebarProps {
  instructions: string;
  files: ProjectFile[];
  projectId: string;
  onSaveInstructions: (text: string) => void;
  onAddFiles: (projectId: string, files: FileList) => void;
  onDeleteFile: (projectId: string, fileId: string) => void;
}

export function ProjectSidebar({
  instructions,
  files,
  projectId,
  onSaveInstructions,
  onAddFiles,
  onDeleteFile,
}: ProjectSidebarProps) {
  return (
    <aside className="w-80 flex-shrink-0 border-l border-border/50 overflow-y-auto p-4 space-y-4 bg-background/50">
      <ProjectInstructions
        instructions={instructions}
        onSave={onSaveInstructions}
      />
      <ProjectFileGrid
        files={files}
        projectId={projectId}
        onAddFiles={onAddFiles}
        onDeleteFile={onDeleteFile}
      />
    </aside>
  );
}
