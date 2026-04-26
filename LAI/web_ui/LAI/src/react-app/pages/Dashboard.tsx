import { Link } from "react-router";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";
import { Button } from "@/react-app/components/ui/button";
import { Progress } from "@/react-app/components/ui/progress";
import {
  FileText,
  FolderOpen,
  MessageSquare,
  ShieldCheck,
  TrendingUp,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ArrowUpRight,
  Zap,
  Plus,
  Upload,
} from "lucide-react";

const stats = [
  {
    title: "Documents Analyzed",
    value: "1,284",
    change: "+12%",
    icon: FileText,
    color: "text-blue-600 dark:text-blue-400",
    bgColor: "bg-blue-50 dark:bg-blue-950/50",
  },
  {
    title: "Active Projects",
    value: "8",
    change: "+2",
    icon: FolderOpen,
    color: "text-violet-600 dark:text-violet-400",
    bgColor: "bg-violet-50 dark:bg-violet-950/50",
  },
  {
    title: "AI Conversations",
    value: "342",
    change: "+28%",
    icon: MessageSquare,
    color: "text-emerald-600 dark:text-emerald-400",
    bgColor: "bg-emerald-50 dark:bg-emerald-950/50",
  },
  {
    title: "Risk Assessments",
    value: "56",
    change: "+5",
    icon: ShieldCheck,
    color: "text-amber-600 dark:text-amber-400",
    bgColor: "bg-amber-50 dark:bg-amber-950/50",
  },
];

const recentProjects = [
  {
    name: "Nordwind Park Due Diligence",
    status: "In Progress",
    progress: 65,
    documents: 124,
    risk: "medium",
  },
  {
    name: "Bavaria Solar-Wind Hybrid",
    status: "Review",
    progress: 90,
    documents: 89,
    risk: "low",
  },
  {
    name: "Baltic Offshore Expansion",
    status: "Started",
    progress: 25,
    documents: 45,
    risk: "high",
  },
];

const riskIndicator = {
  low: {
    color: "text-emerald-600 dark:text-emerald-500",
    bg: "bg-emerald-500/10",
    Icon: CheckCircle2,
    label: "Low Risk",
  },
  medium: {
    color: "text-amber-600 dark:text-amber-500",
    bg: "bg-amber-500/10",
    Icon: AlertTriangle,
    label: "Medium Risk",
  },
  high: {
    color: "text-rose-600 dark:text-rose-500",
    bg: "bg-rose-500/10",
    Icon: XCircle,
    label: "High Risk",
  },
};

const recentActivity = [
  {
    action: "Document uploaded",
    item: "permit_application_2024.pdf",
    time: "5 min ago",
    Icon: Upload,
  },
  {
    action: "AI analysis completed",
    item: "Environmental impact report",
    time: "23 min ago",
    Icon: Zap,
  },
  {
    action: "Risk flagged",
    item: "Land lease agreement clause 4.2",
    time: "1 hour ago",
    Icon: AlertTriangle,
  },
  {
    action: "Project created",
    item: "Baltic Offshore Expansion",
    time: "3 hours ago",
    Icon: FolderOpen,
  },
];

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground">
            Welcome back. Here's your legal AI overview.
          </p>
        </div>
        <Link to="/dashboard/chat">
          <Button className="shadow-sm">
            <Plus className="w-4 h-4 mr-2" />
            New Chat
          </Button>
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => (
          <Card
            key={stat.title}
            className="bg-card/50 backdrop-blur border-border/50"
          >
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <div className={`p-2.5 rounded-lg ${stat.bgColor}`}>
                  <stat.icon className={`w-5 h-5 ${stat.color}`} />
                </div>
                <div className="flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-500">
                  <TrendingUp className="w-3 h-3" />
                  {stat.change}
                </div>
              </div>
              <div className="mt-4">
                <p className="text-2xl font-bold">{stat.value}</p>
                <p className="text-sm text-muted-foreground">{stat.title}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Active Projects */}
        <div className="lg:col-span-2">
          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="flex flex-row items-center justify-between pb-4">
              <CardTitle className="text-lg font-semibold">
                Active Projects
              </CardTitle>
              <Link to="/dashboard/projects">
                <Button variant="ghost" size="sm" className="text-primary">
                  View all
                  <ArrowUpRight className="w-4 h-4 ml-1" />
                </Button>
              </Link>
            </CardHeader>
            <CardContent className="space-y-4">
              {recentProjects.map((project) => {
                const risk =
                  riskIndicator[project.risk as keyof typeof riskIndicator];
                return (
                  <div
                    key={project.name}
                    className="p-4 rounded-md bg-muted/30 hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <h4 className="font-medium">{project.name}</h4>
                        <p className="text-sm text-muted-foreground">
                          {project.documents} documents
                        </p>
                      </div>
                      <div
                        className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${risk.bg} ${risk.color}`}
                      >
                        <risk.Icon className="w-3 h-3" />
                        {risk.label}
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">
                          {project.status}
                        </span>
                        <span className="font-medium">{project.progress}%</span>
                      </div>
                      <Progress value={project.progress} className="h-2" />
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>

        {/* Recent Activity */}
        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardHeader className="pb-4">
            <CardTitle className="text-lg font-semibold">
              Recent Activity
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {recentActivity.map((activity, index) => (
                <div key={index} className="flex items-start gap-3">
                  <div className="p-2 rounded-lg bg-muted/50 flex-shrink-0">
                    <activity.Icon className="w-4 h-4 text-muted-foreground" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium">{activity.action}</p>
                    <p className="text-xs text-muted-foreground truncate">
                      {activity.item}
                    </p>
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {activity.time}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Quick Actions */}
      <Card className="bg-slate-900 border-slate-800 text-white shadow-sm">
        <CardContent className="p-6">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-md bg-slate-800 border border-slate-700">
                <Zap className="w-6 h-6 text-slate-300" />
              </div>
              <div>
                <h3 className="font-semibold">Quick Due Diligence</h3>
                <p className="text-sm text-slate-400">
                  Upload documents and get precision AI analysis in minutes
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Link to="/dashboard/documents">
                <Button variant="outline" className="border-slate-700 hover:bg-slate-800 bg-slate-900 text-slate-300">Upload Documents</Button>
              </Link>
              <Link to="/dashboard/chat">
                <Button className="bg-blue-600 hover:bg-blue-700 text-white shadow-sm border-0">Start AI Chat</Button>
              </Link>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
