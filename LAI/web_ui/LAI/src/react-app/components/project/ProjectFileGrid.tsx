import { useRef } from "react";
import { Plus, FileText, X } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";
import { ProjectFile } from "./types";

interface ProjectFileGridProps {
  files: ProjectFile[];
  projectId: string;
  onAddFiles: (projectId: string, files: FileList) => void;
  onDeleteFile: (projectId: string, fileId: string) => void;
}

function getTypeBadgeClass(type: string): string {
  switch (type.toUpperCase()) {
    case "PDF":
      return "bg-red-500/20 text-red-400 border border-red-500/30";
    case "DOCX":
      return "bg-blue-500/20 text-blue-400 border border-blue-500/30";
    case "XLSX":
      return "bg-green-500/20 text-green-400 border border-green-500/30";
    case "PY":
      return "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30";
    case "TS":
    case "TSX":
      return "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30";
    case "JS":
    case "JSX":
      return "bg-orange-500/20 text-orange-400 border border-orange-500/30";
    default:
      return "bg-muted text-muted-foreground border border-border/50";
  }
}

function getCapacityUsed(files: ProjectFile[]): number {
  const totalMB = files.reduce((acc, f) => acc + f.size, 0);
  return Math.min(Math.round((totalMB / 200) * 100), 100);
}

export function ProjectFileGrid({
  files,
  projectId,
  onAddFiles,
  onDeleteFile,
}: ProjectFileGridProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const capacity = getCapacityUsed(files);

  return (
    <Card className="bg-card/50 backdrop-blur border-border/50">
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm font-semibold">Files</CardTitle>
        <button
          onClick={() => fileInputRef.current?.click()}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Capacity bar */}
        <div>
          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-500"
              style={{ width: `${capacity}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {capacity}% of project capacity used
          </p>
        </div>

        {/* File grid */}
        {files.length === 0 ? (
          <button
            onClick={() => fileInputRef.current?.click()}
            className="w-full border-2 border-dashed border-border/50 rounded-lg p-6 text-center hover:border-primary/50 transition-colors"
          >
            <FileText className="w-6 h-6 text-muted-foreground mx-auto mb-1" />
            <p className="text-xs text-muted-foreground">Click to add files</p>
          </button>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            {files.map((file) => {
              const baseName = file.name.includes(".")
                ? file.name.substring(0, file.name.lastIndexOf("."))
                : file.name;
              const ext = file.name.includes(".")
                ? file.name.substring(file.name.lastIndexOf(".") + 1)
                : "";

              return (
                <div
                  key={file.id}
                  className="group relative bg-background/50 rounded-xl border border-border/50 p-2.5 hover:border-border transition-all"
                >
                  {/* Delete on hover */}
                  <button
                    onClick={() => onDeleteFile(projectId, file.id)}
                    className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-destructive text-destructive-foreground rounded-full items-center justify-center hidden group-hover:flex"
                  >
                    <X className="w-2.5 h-2.5" />
                  </button>

                  <p className="text-xs font-medium text-foreground leading-tight truncate">
                    {baseName}
                    {ext && (
                      <span className="text-muted-foreground">.{ext}</span>
                    )}
                  </p>
                  {file.lines !== undefined && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {file.lines} lines
                    </p>
                  )}
                  <div className="mt-2">
                    <span
                      className={`text-xs font-semibold px-1.5 py-0.5 rounded ${getTypeBadgeClass(file.type)}`}
                    >
                      {file.type}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.currentTarget.files) {
              onAddFiles(projectId, e.currentTarget.files);
              e.currentTarget.value = "";
            }
          }}
        />
      </CardContent>
    </Card>
  );
}
