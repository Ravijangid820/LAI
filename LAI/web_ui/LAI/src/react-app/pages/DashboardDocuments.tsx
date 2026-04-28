"use client";

import { useState, useEffect, useRef } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";
import { Button } from "@/react-app/components/ui/button";
import { Input } from "@/react-app/components/ui/input";
import { Progress } from "@/react-app/components/ui/progress";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/react-app/components/ui/dropdown-menu";
import {
  ManuscriptIcon,
  UploadIcon,
  DownloadIcon,
  TrashIcon,
  SearchIcon,
  FilterIcon,
  PlusIcon,
  DotsVerticalIcon,
  CalendarIcon,
  StorageIcon,
  LensIcon,
  ArchiveIcon,
  SandglassIcon,
} from "@/react-app/components/icons";
import { fetchDocuments, uploadDDiQDocument } from "@/react-app/lib/ddiqApi";
import type { DocumentItem } from "@/react-app/lib/ddiqDemoData";

export default function DashboardDocumentsPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── State ──
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadFileName, setUploadFileName] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);

  // ── Load documents from backend ──
  const loadDocuments = async () => {
    try {
      const res = await fetchDocuments();
      setDocuments(res.documents);
    } catch (err) {
      console.error("Failed to load documents:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDocuments();
  }, []);

  // ── Filtering ──
  const filteredDocuments = documents.filter((doc) => {
    const matchesSearch =
      doc.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      doc.category.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = !filterStatus || doc.status === filterStatus;
    return matchesSearch && matchesStatus;
  });

  // ── Drag & Drop ──
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === "dragenter" || e.type === "dragover");
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.length) processFiles(e.dataTransfer.files);
  };

  // ── File Upload via API ──
  const processFiles = async (files: FileList) => {
    for (const file of Array.from(files)) {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setUploadError(
          `"${file.name}" skipped — only PDF files are supported for analysis`,
        );
        continue;
      }

      setUploading(true);
      setUploadFileName(file.name);
      setUploadError(null);

      try {
        // Determine category from filename
        const nameLower = file.name.toLowerCase();
        let category = "Uncategorized";
        if (
          nameLower.includes("vertrag") ||
          nameLower.includes("contract") ||
          nameLower.includes("lease")
        )
          category = "Legal";
        else if (
          nameLower.includes("permit") ||
          nameLower.includes("genehmigung") ||
          nameLower.includes("bimsch")
        )
          category = "Permits";
        else if (
          nameLower.includes("umwelt") ||
          nameLower.includes("environment") ||
          nameLower.includes("uva") ||
          nameLower.includes("eia")
        )
          category = "Environmental";
        else if (nameLower.includes("techni") || nameLower.includes("spec"))
          category = "Technical";
        else if (nameLower.includes("grid") || nameLower.includes("netz"))
          category = "Grid";
        else if (
          nameLower.includes("financ") ||
          nameLower.includes("finanz") ||
          nameLower.includes("bank")
        )
          category = "Financial";

        await uploadDDiQDocument(file, category);

        // Refresh document list after upload
        await loadDocuments();
      } catch (err) {
        setUploadError(
          err instanceof Error ? err.message : `Upload failed for ${file.name}`,
        );
      } finally {
        setUploading(false);
        setUploadFileName("");
      }
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.currentTarget.files?.length) processFiles(e.currentTarget.files);
    e.currentTarget.value = "";
  };

  const triggerFileInput = () => fileInputRef.current?.click();

  // ── Actions (local for now — can add backend endpoints later) ──
  const deleteDocument = (id: string) => {
    // TODO: call DELETE /ddiq/documents/{id} when endpoint exists
    setDocuments(documents.filter((d) => d.id !== id));
  };

  const archiveDocument = (id: string) => {
    // TODO: call PATCH /ddiq/documents/{id}/archive when endpoint exists
    setDocuments(
      documents.map((d) =>
        d.id === id ? { ...d, status: "archived" as const } : d,
      ),
    );
  };

  const downloadDocument = (name: string) => {
    console.log(`Download: ${name}`);
    // TODO: call GET /ddiq/documents/{id}/download when endpoint exists
  };

  // ── UI Config ──
  const statusColor = {
    analyzed:
      "text-emerald-600 bg-emerald-500/10 dark:text-emerald-500 dark:bg-emerald-500/20",
    pending:
      "text-amber-600 bg-amber-500/10 dark:text-amber-500 dark:bg-amber-500/20",
    archived:
      "text-slate-500 bg-slate-500/10 dark:text-slate-400 dark:bg-slate-500/20",
  };

  const totalSize = documents.reduce((sum, doc) => sum + doc.size, 0);

  // ── Loading State ──
  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Documents</h1>
          <p className="text-muted-foreground">
            Manage and organize your project documents
          </p>
        </div>
        <div className="flex items-center justify-center py-16">
          <SandglassIcon className="w-6 h-6 text-muted-foreground animate-pulse mr-3" />
          <span className="text-muted-foreground">Loading documents...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Documents</h1>
          <p className="text-muted-foreground">
            Manage and organize your project documents
          </p>
        </div>
        <Button
          className="shadow-sm"
          onClick={triggerFileInput}
          disabled={uploading}
        >
          <PlusIcon className="w-4 h-4 mr-2" />
          Upload Document
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Total Documents</p>
                <p className="text-2xl font-bold mt-2">{documents.length}</p>
              </div>
              <div className="p-2.5 rounded-md bg-slate-100 dark:bg-slate-800">
                <ManuscriptIcon className="w-5 h-5 text-slate-600 dark:text-slate-400" />
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Total Size</p>
                <p className="text-2xl font-bold mt-2">
                  {totalSize.toFixed(1)} MB
                </p>
              </div>
              <div className="p-2.5 rounded-md bg-slate-100 dark:bg-slate-800">
                <StorageIcon className="w-5 h-5 text-slate-600 dark:text-slate-400" />
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardContent className="p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Analyzed</p>
                <p className="text-2xl font-bold mt-2">
                  {documents.filter((d) => d.status === "analyzed").length}
                </p>
              </div>
              <div className="p-2.5 rounded-md bg-slate-100 dark:bg-slate-800">
                <LensIcon className="w-5 h-5 text-slate-600 dark:text-slate-400" />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Upload Zone */}
      <Card
        className={`bg-card/50 backdrop-blur border-border/50 border-2 border-dashed transition-colors cursor-pointer hover:border-slate-400 dark:hover:border-slate-600 ${dragActive ? "border-slate-500 bg-slate-500/5" : ""} ${uploading ? "pointer-events-none opacity-60" : ""}`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={uploading ? undefined : triggerFileInput}
      >
        <CardContent className="p-8">
          <div className="flex flex-col items-center justify-center gap-4">
            {uploading ? (
              <>
                <div className="p-4 rounded-md bg-primary/10">
                  <SandglassIcon className="w-8 h-8 text-primary animate-pulse" />
                </div>
                <div className="text-center">
                  <p className="font-semibold text-foreground">
                    Uploading & analyzing...
                  </p>
                  <p className="text-sm text-muted-foreground mt-1">
                    {uploadFileName}
                  </p>
                </div>
                <Progress value={65} className="h-2 w-48" />
              </>
            ) : (
              <>
                <div className="p-4 rounded-md bg-slate-100 dark:bg-slate-800">
                  <UploadIcon className="w-8 h-8 text-slate-600 dark:text-slate-400" />
                </div>
                <div className="text-center">
                  <p className="font-semibold text-foreground">
                    Drag and drop PDF files here
                  </p>
                  <p className="text-sm text-muted-foreground mt-1">
                    or click to select documents — PDFs will be extracted,
                    chunked, and embedded for analysis
                  </p>
                </div>
                <Button
                  variant="outline"
                  className="mt-2 text-sm shadow-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    triggerFileInput();
                  }}
                >
                  Select Files
                </Button>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Upload Error */}
      {uploadError && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 flex items-center justify-between">
          <p className="text-sm text-rose-600 dark:text-rose-400">
            {uploadError}
          </p>
          <Button
            variant="ghost"
            size="sm"
            className="text-xs"
            onClick={() => setUploadError(null)}
          >
            Dismiss
          </Button>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf"
        onChange={handleFileInputChange}
        style={{ display: "none" }}
      />

      {/* Search & Filter */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <SearchIcon className="absolute left-3 top-3 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search documents..."
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
              All Statuses
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFilterStatus("analyzed")}>
              Analyzed
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFilterStatus("pending")}>
              Pending
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setFilterStatus("archived")}>
              Archived
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Documents List */}
      <Card className="bg-card/50 backdrop-blur border-border/50">
        <CardHeader className="pb-4">
          <CardTitle className="text-lg font-semibold">
            {filteredDocuments.length} Document
            {filteredDocuments.length !== 1 ? "s" : ""}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {filteredDocuments.length === 0 ? (
            <div className="text-center py-8">
              <ManuscriptIcon className="w-12 h-12 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground">
                {documents.length === 0
                  ? "No documents uploaded yet"
                  : "No documents match your search"}
              </p>
              {documents.length === 0 && (
                <p className="text-xs text-muted-foreground mt-1">
                  Upload a PDF above to get started
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              {filteredDocuments.map((doc) => (
                <div
                  key={doc.id}
                  className="flex items-center justify-between p-4 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors group"
                >
                  <div className="flex items-center gap-4 flex-1">
                    <div className="p-2 rounded-lg bg-slate-100 dark:bg-slate-800 flex-shrink-0 border border-slate-200 dark:border-slate-700">
                      <ManuscriptIcon className="w-5 h-5 text-slate-600 dark:text-slate-400" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{doc.name}</p>
                      <div className="flex items-center gap-3 text-sm text-muted-foreground">
                        <span>{doc.size.toFixed(1)} MB</span>
                        <span className="flex items-center gap-1">
                          <CalendarIcon className="w-3.5 h-3.5" />
                          {doc.uploadDate}
                        </span>
                        <span className="text-xs px-2 py-1 rounded bg-primary/10 text-primary">
                          {doc.category}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs font-medium px-2.5 py-1 rounded-md ${statusColor[doc.status]}`}
                    >
                      {doc.status.charAt(0).toUpperCase() + doc.status.slice(1)}
                    </span>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          <DotsVerticalIcon className="w-4 h-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => downloadDocument(doc.name)}
                        >
                          <DownloadIcon className="w-4 h-4 mr-2" />
                          Download
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => archiveDocument(doc.id)}
                        >
                          <ArchiveIcon className="w-4 h-4 mr-2" />
                          Archive
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => deleteDocument(doc.id)}
                          className="text-red-500"
                        >
                          <TrashIcon className="w-4 h-4 mr-2" />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
