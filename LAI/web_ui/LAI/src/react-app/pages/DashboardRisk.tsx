"use client";

import { useState, useMemo } from "react";
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
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/react-app/components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/react-app/components/ui/dropdown-menu";
import { cn } from "@/react-app/lib/utils";
import {
  SignalTowerIcon,
  CheckRingIcon,
  AlertIcon,
  DangerRingIcon,
  FilterIcon,
  SearchIcon,
  ArrowUpRightIcon,
  ArrowDownRightIcon,
  SandglassIcon,
  DownloadIcon,
} from "@/react-app/components/icons";
import ReportDownloadPanel from "@/react-app/components/ReportDownloadPanel";

// ─── Types ──────────────────────────────────────────────────────────────────

interface RiskArea {
  id: string;
  name: string;
  category: string;
  riskLevel: "low" | "medium" | "high";
  score: number;
  trend: "up" | "down" | "stable";
  description: string;
  flaggedItems: number;
  lastUpdated: string;
}

// Documents — same data as DashboardDocumentsPage
const DOCUMENTS = [
  {
    id: "1",
    name: "permit_application_2024.pdf",
    size: 2.4,
    uploadDate: "2024-02-18",
    type: "PDF",
    status: "analyzed" as const,
    category: "Permits",
  },
  {
    id: "2",
    name: "land_lease_agreement.docx",
    size: 1.1,
    uploadDate: "2024-02-15",
    type: "Word",
    status: "analyzed" as const,
    category: "Legal",
  },
  {
    id: "3",
    name: "environmental_impact_report.pdf",
    size: 5.8,
    uploadDate: "2024-02-14",
    type: "PDF",
    status: "analyzed" as const,
    category: "Environmental",
  },
  {
    id: "4",
    name: "technical_specifications.xlsx",
    size: 0.8,
    uploadDate: "2024-02-10",
    type: "Excel",
    status: "pending" as const,
    category: "Technical",
  },
  {
    id: "5",
    name: "grid_connection_procedure.pdf",
    size: 3.2,
    uploadDate: "2024-02-08",
    type: "PDF",
    status: "archived" as const,
    category: "Grid",
  },
];

const riskCfg = {
  low: {
    color: "text-emerald-600 dark:text-emerald-500",
    bg: "bg-emerald-500/10 dark:bg-emerald-500/20",
    border: "border-emerald-500/20",
    borderL: "border-l-emerald-500/50",
    Icon: CheckRingIcon,
    label: "Low Risk",
    bgI: "bg-emerald-500/20",
  },
  medium: {
    color: "text-amber-600 dark:text-amber-500",
    bg: "bg-amber-500/10 dark:bg-amber-500/20",
    border: "border-amber-500/20",
    borderL: "border-l-amber-500/50",
    Icon: AlertIcon,
    label: "Medium Risk",
    bgI: "bg-amber-500/20",
  },
  high: {
    color: "text-rose-600 dark:text-rose-500",
    bg: "bg-rose-500/10 dark:bg-rose-500/20",
    border: "border-rose-500/20",
    borderL: "border-l-rose-500/50",
    Icon: DangerRingIcon,
    label: "High Risk",
    bgI: "bg-rose-500/20",
  },
};

// ─── Demo Data ──────────────────────────────────────────────────────────────
// Same documents as DashboardDocumentsPage — later replace with shared state/context

const RISKS: RiskArea[] = [
  {
    id: "1",
    name: "Environmental Compliance",
    category: "Legal",
    riskLevel: "low",
    score: 85,
    trend: "up",
    description: "All environmental regulations compliant",
    flaggedItems: 0,
    lastUpdated: "2024-02-18",
  },
  {
    id: "2",
    name: "Land Lease Agreements",
    category: "Contracts",
    riskLevel: "medium",
    score: 65,
    trend: "down",
    description: "Potential liability in clause 4.2 regarding termination",
    flaggedItems: 2,
    lastUpdated: "2024-02-16",
  },
  {
    id: "3",
    name: "Grid Connection Rights",
    category: "Technical",
    riskLevel: "high",
    score: 35,
    trend: "down",
    description: "Restricted grid access periods during maintenance windows",
    flaggedItems: 5,
    lastUpdated: "2024-02-15",
  },
  {
    id: "4",
    name: "Financing & Loans",
    category: "Financial",
    riskLevel: "low",
    score: 92,
    trend: "stable",
    description: "Favorable loan terms secured",
    flaggedItems: 0,
    lastUpdated: "2024-02-18",
  },
  {
    id: "5",
    name: "Permits & Licenses",
    category: "Regulatory",
    riskLevel: "medium",
    score: 72,
    trend: "up",
    description: "Operating permit expires in 18 months",
    flaggedItems: 1,
    lastUpdated: "2024-02-14",
  },
  {
    id: "6",
    name: "Insurance Coverage",
    category: "Insurance",
    riskLevel: "low",
    score: 88,
    trend: "up",
    description: "Comprehensive coverage in place",
    flaggedItems: 0,
    lastUpdated: "2024-02-17",
  },
];

// ─── Component ──────────────────────────────────────────────────────────────

export default function DashboardRiskPage() {
  const [tab, setTab] = useState<"assessment" | "reports">("assessment");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<string | null>(null);

  const filtered = useMemo(
    () =>
      RISKS.filter((r) => {
        const ms =
          r.name.toLowerCase().includes(search.toLowerCase()) ||
          r.category.toLowerCase().includes(search.toLowerCase());
        return ms && (!filter || r.riskLevel === filter);
      }),
    [search, filter],
  );

  const stats = {
    low: RISKS.filter((r) => r.riskLevel === "low").length,
    medium: RISKS.filter((r) => r.riskLevel === "medium").length,
    high: RISKS.filter((r) => r.riskLevel === "high").length,
  };
  const avg = Math.round(RISKS.reduce((s, r) => s + r.score, 0) / RISKS.length);
  const analyzedCount = DOCUMENTS.filter((d) => d.status === "analyzed").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Risk Assessment</h1>
        <p className="text-muted-foreground">
          Traffic light risk visualization and DDiQ report generation
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList className="grid w-full max-w-md grid-cols-2">
          <TabsTrigger value="assessment" className="flex items-center gap-2">
            <SignalTowerIcon className="w-4 h-4" />
            Risk Assessment
          </TabsTrigger>
          <TabsTrigger value="reports" className="flex items-center gap-2">
            <DownloadIcon className="w-4 h-4" />
            DDiQ Reports
            {analyzedCount > 0 && (
              <span className="text-[10px] font-medium ml-1 px-1.5 py-0.5 rounded-full bg-primary/10 text-primary">
                {analyzedCount}
              </span>
            )}
          </TabsTrigger>
        </TabsList>

        {/* Risk Assessment Tab */}
        <TabsContent value="assessment" className="space-y-6 mt-6">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <Card className="bg-card/50 backdrop-blur border-border/50">
              <CardContent className="p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground">
                      Overall Score
                    </p>
                    <p className="text-2xl font-bold mt-2">{avg}%</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      Health Rating
                    </p>
                  </div>
                  <div className="p-2.5 rounded-md bg-slate-100 dark:bg-slate-800">
                    <SignalTowerIcon className="w-5 h-5 text-slate-600 dark:text-slate-400" />
                  </div>
                </div>
              </CardContent>
            </Card>
            {(["low", "medium", "high"] as const).map((lv) => {
              const c = riskCfg[lv];
              return (
                <Card
                  key={lv}
                  className={cn(
                    "bg-card/50 backdrop-blur border-border/50 border-l-4",
                    c.borderL,
                  )}
                >
                  <CardContent className="p-5">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="text-sm text-muted-foreground">
                          {c.label}
                        </p>
                        <p className={cn("text-2xl font-bold mt-2", c.color)}>
                          {stats[lv]}
                        </p>
                      </div>
                      <div className={cn("p-2.5 rounded-md", c.bg)}>
                        <c.Icon className={cn("w-5 h-5", c.color)} />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="pb-4">
              <CardTitle className="text-lg font-semibold">
                Risk Distribution
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {(["low", "medium", "high"] as const).map((lv) => {
                  const c = riskCfg[lv];
                  return (
                    <div key={lv} className="text-center">
                      <div className="flex justify-center mb-4">
                        <div
                          className={cn(
                            "w-24 h-24 rounded-md border-4 flex items-center justify-center",
                            c.bgI,
                            c.border,
                          )}
                        >
                          <c.Icon className={cn("w-12 h-12", c.color)} />
                        </div>
                      </div>
                      <h3 className={cn("font-semibold mb-2", c.color)}>
                        {c.label}
                      </h3>
                      <p className={cn("text-3xl font-bold mb-1", c.color)}>
                        {stats[lv]}
                      </p>
                      <p className="text-sm text-muted-foreground">Areas</p>
                      <p className={cn("text-xs mt-2", c.color)}>
                        {Math.round((stats[lv] / RISKS.length) * 100)}%
                      </p>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          <div className="flex items-center gap-3">
            <div className="relative flex-1">
              <SearchIcon className="absolute left-3 top-3 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search risk areas..."
                className="pl-10"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <FilterIcon className="w-4 h-4 mr-2" />
                  {filter
                    ? riskCfg[filter as keyof typeof riskCfg]?.label
                    : "All Levels"}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setFilter(null)}>
                  All Levels
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setFilter("low")}>
                  Low Risk
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setFilter("medium")}>
                  Medium Risk
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setFilter("high")}>
                  High Risk
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setTab("reports")}
            >
              <DownloadIcon className="w-4 h-4 mr-2" />
              DDiQ Report
            </Button>
          </div>

          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="pb-4">
              <CardTitle className="text-lg font-semibold">
                Risk Areas ({filtered.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {filtered.length === 0 ? (
                <div className="text-center py-8">
                  <SignalTowerIcon className="w-12 h-12 text-muted-foreground mx-auto mb-3 opacity-50" />
                  <p className="text-muted-foreground">No risk areas found</p>
                </div>
              ) : (
                filtered.map((r) => {
                  const c = riskCfg[r.riskLevel];
                  return (
                    <div
                      key={r.id}
                      className={cn(
                        "p-4 rounded-md border transition-colors hover:opacity-90",
                        c.border,
                        c.bgI,
                      )}
                    >
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-start gap-3 flex-1">
                          <div
                            className={cn(
                              "p-2.5 rounded-lg flex-shrink-0",
                              c.bg,
                            )}
                          >
                            <c.Icon className={cn("w-5 h-5", c.color)} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <h4 className="font-semibold">{r.name}</h4>
                              <span
                                className={cn(
                                  "text-xs font-medium px-2.5 py-1 rounded-md",
                                  c.bg,
                                  c.color,
                                )}
                              >
                                {c.label}
                              </span>
                            </div>
                            <p className="text-sm text-muted-foreground mt-1">
                              {r.description}
                            </p>
                            <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                              <span className="px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 font-medium">
                                {r.category}
                              </span>
                              {r.flaggedItems > 0 && (
                                <span className="flex items-center gap-1 text-rose-600 dark:text-rose-500">
                                  <AlertIcon className="w-3 h-3" />
                                  {r.flaggedItems} flagged
                                </span>
                              )}
                              <span className="flex items-center gap-1">
                                <SandglassIcon className="w-3 h-3" />
                                Updated {r.lastUpdated}
                              </span>
                            </div>
                          </div>
                        </div>
                        <div className="text-right flex-shrink-0 ml-4">
                          <p className="text-2xl font-bold">{r.score}%</p>
                          <div className="flex items-center gap-1 mt-1 justify-end">
                            {r.trend === "up" ? (
                              <ArrowUpRightIcon className="w-4 h-4 text-emerald-600" />
                            ) : r.trend === "down" ? (
                              <ArrowDownRightIcon className="w-4 h-4 text-rose-600" />
                            ) : (
                              <div className="w-4 h-0.5 bg-muted-foreground" />
                            )}
                            <span className="text-xs text-muted-foreground capitalize">
                              {r.trend}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="mt-3">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs text-muted-foreground">
                            Risk Score
                          </span>
                          <span className="text-xs font-medium">
                            {r.score}% Safe
                          </span>
                        </div>
                        <Progress value={r.score} className="h-2" />
                      </div>
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* DDiQ Reports Tab — passes documents as props */}
        <TabsContent value="reports" className="mt-6">
          <ReportDownloadPanel documents={DOCUMENTS} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
