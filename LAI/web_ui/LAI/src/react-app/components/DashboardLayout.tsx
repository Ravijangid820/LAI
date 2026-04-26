import { useState } from "react";
import { Link, useLocation, Outlet, useNavigate } from "react-router";
import { Logo } from "@/react-app/components/Logo";
import { useAuth } from "@/react-app/contexts/AuthContext";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/react-app/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/react-app/components/ui/avatar";
import {
  PanelCollapseIcon,
  PanelExpandIcon,
} from "@/react-app/components/icons";
import { ThemeToggle } from "@/react-app/components/ThemeToggle";

const navigation = [
  { name: "Dashboard", href: "/dashboard", symbol: "⌂" },
  { name: "Chat", href: "/dashboard/chat", symbol: "✦" },
  { name: "Documents", href: "/dashboard/documents", symbol: "≡" },
  { name: "Projects", href: "/dashboard/projects", symbol: "◈" },
  { name: "Risk Assessment", href: "/dashboard/risk", symbol: "⚖" },
];

const secondaryNav = [
  { name: "Settings", href: "/dashboard/settings", symbol: "⊙" },
];

export interface Conversation {
  id: string;
  title: string;
  preview: string;
  timestamp: Date;
}

const demoConversations: Conversation[] = [
  {
    id: "1",
    title: "Nordwind Park permit analysis",
    preview: "Reviewing BImSchG permits and environmental...",
    timestamp: new Date(),
  },
  {
    id: "2",
    title: "Land lease agreement review",
    preview: "Analyzing clause 4.2 regarding termination...",
    timestamp: new Date(Date.now() - 86400000),
  },
  {
    id: "3",
    title: "Grid connection contracts",
    preview: "What are the key risks in the Einspeisezusage...",
    timestamp: new Date(Date.now() - 86400000 * 3),
  },
];

// ─────────────────────────────────────────────────────────────────────────────

export default function DashboardLayout() {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [conversations, setConversations] =
    useState<Conversation[]>(demoConversations);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const location = useLocation();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  const isActive = (href: string) => {
    if (href === "/dashboard") return location.pathname === "/dashboard";
    return (
      location.pathname === href || location.pathname.startsWith(href + "/")
    );
  };

  const isOnChatPage = location.pathname === "/dashboard/chat";

  const userInitials =
    user?.email
      ?.split("@")[0]
      .split("")
      .slice(0, 2)
      .map((c) => c.toUpperCase())
      .join("") || "JD";

  // ── FIX 3: New Chat — creates a real conversation and sets it active ──────
  const handleNewChat = () => {
    const newConversation: Conversation = {
      id: crypto.randomUUID(),
      title: "New Chat",
      preview: "Start a new conversation...",
      timestamp: new Date(),
    };
    // Prepend to list so it appears at the top
    setConversations((prev) => [newConversation, ...prev]);
    setActiveConversationId(newConversation.id);
  };

  return (
    <div
      className="h-screen bg-background flex overflow-hidden"
      style={
        {
          "--sidebar-width": `${collapsed ? 64 : 256}px`,
        } as React.CSSProperties
      }
    >
      {/* ── Sidebar ── */}
      <aside
        className={`flex-shrink-0 h-full bg-sidebar border-r border-sidebar-border flex flex-col transition-all duration-300 z-40 ${collapsed ? "w-16" : "w-64"
          }`}
      >
        {/* ── Logo + Collapse ── */}
        <div className="flex flex-col border-b border-sidebar-border">
          <div
            className={`h-16 flex items-center ${collapsed ? "justify-center px-3" : "justify-between px-4"}`}
          >
            {collapsed ? (
              <div
                className="w-8 h-8 flex-shrink-0 overflow-hidden"
                style={{ minWidth: "2rem" }}
              >
                <Logo size="sm" />
              </div>
            ) : (
              <>
                <Logo size="sm" />
                <ThemeToggle />
              </>
            )}
          </div>

          {/* Collapse toggle */}
          <div className="px-3 pb-2">
            <button
              onClick={() => setCollapsed(!collapsed)}
              className={`flex items-center gap-2 rounded-md text-sm font-medium text-muted-foreground hover:bg-sidebar-accent transition-colors ${collapsed
                ? "w-10 h-10 justify-center mx-auto"
                : "w-full px-3 py-2.5"
                }`}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? (
                <PanelExpandIcon className="w-5 h-5 flex-shrink-0" />
              ) : (
                <>
                  <PanelCollapseIcon className="w-5 h-5 flex-shrink-0" />
                  <span>Collapse</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* ── Navigation ── */}
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {!collapsed && (
            <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3 px-3">
              Main
            </div>
          )}

          {navigation.map((item) => (
            <Link
              key={item.name}
              to={item.href}
              title={item.name}
              className={`flex items-center rounded-md text-sm font-medium transition-all ${isActive(item.href)
                ? "bg-primary text-primary-foreground shadow-md"
                : "text-sidebar-foreground hover:bg-sidebar-accent"
                } ${collapsed
                  ? "justify-center w-10 h-10 mx-auto"
                  : "gap-3 px-3 py-2.5"
                }`}
            >
              {collapsed ? (
                <span
                  className={`text-xl leading-none select-none ${isActive(item.href)
                    ? "text-primary-foreground"
                    : "text-muted-foreground"
                    }`}
                >
                  {item.symbol}
                </span>
              ) : (
                <>
                  <span
                    className={`text-lg leading-none select-none w-6 text-center flex-shrink-0 ${isActive(item.href)
                      ? "text-primary-foreground"
                      : "text-muted-foreground"
                      }`}
                  >
                    {item.symbol}
                  </span>
                  <span>{item.name}</span>
                </>
              )}
            </Link>
          ))}

          {/* Divider */}
          {collapsed ? (
            <div className="my-3 border-t border-sidebar-border/50 mx-2" />
          ) : (
            <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mt-6 mb-3 px-3">
              Support
            </div>
          )}

          {secondaryNav.map((item) => (
            <Link
              key={item.name}
              to={item.href}
              title={item.name}
              className={`flex items-center rounded-md text-sm font-medium transition-all ${isActive(item.href)
                ? "bg-primary text-primary-foreground shadow-md"
                : "text-sidebar-foreground hover:bg-sidebar-accent"
                } ${collapsed
                  ? "justify-center w-10 h-10 mx-auto"
                  : "gap-3 px-3 py-2.5"
                }`}
            >
              {collapsed ? (
                <span
                  className={`text-xl leading-none select-none ${isActive(item.href)
                    ? "text-primary-foreground"
                    : "text-muted-foreground"
                    }`}
                >
                  {item.symbol}
                </span>
              ) : (
                <>
                  <span
                    className={`text-lg leading-none select-none w-6 text-center flex-shrink-0 ${isActive(item.href)
                      ? "text-primary-foreground"
                      : "text-muted-foreground"
                      }`}
                  >
                    {item.symbol}
                  </span>
                  <span>{item.name}</span>
                </>
              )}
            </Link>
          ))}

          {/* ── Conversations — chat page + expanded only ── */}
          {isOnChatPage && !collapsed && (
            <>
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mt-6 mb-3 px-3">
                Conversations
              </div>
              <div className="space-y-1">
                {/* FIX 3: calls handleNewChat which creates a real conversation */}
                <button
                  onClick={handleNewChat}
                  className="w-full flex items-center gap-2 px-3 py-2.5 rounded-md text-sm font-medium text-sidebar-foreground hover:bg-sidebar-accent transition-all"
                >
                  <span className="text-primary font-bold text-base leading-none">
                    +
                  </span>
                  New Chat
                </button>

                {conversations.map((conv) => (
                  <div
                    key={conv.id}
                    className={`group flex items-start gap-2 px-3 py-2.5 rounded-md text-xs transition-all cursor-pointer ${activeConversationId === conv.id
                      ? "bg-primary/10 hover:bg-primary/20"
                      : "hover:bg-sidebar-accent"
                      }`}
                    onClick={() => setActiveConversationId(conv.id)}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sidebar-foreground truncate">
                        {conv.title}
                      </p>
                      <p className="text-muted-foreground truncate text-xs">
                        {conv.preview}
                      </p>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          onClick={(e) => e.stopPropagation()}
                          className="opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 text-muted-foreground hover:text-foreground text-sm px-1"
                        >
                          ···
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => {
                            setConversations((prev) =>
                              prev.filter((c) => c.id !== conv.id),
                            );
                            if (activeConversationId === conv.id)
                              setActiveConversationId(null);
                          }}
                          className="text-destructive"
                        >
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))}
              </div>
            </>
          )}
        </nav>

        {/* ── User Section ── */}
        <div
          className={`p-3 border-t border-sidebar-border ${collapsed ? "flex justify-center" : ""}`}
        >
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className={`flex items-center gap-3 w-full p-2 rounded-md hover:bg-sidebar-accent transition-colors ${collapsed ? "justify-center" : ""
                  }`}
              >
                <Avatar className="w-9 h-9 border-2 border-slate-200 dark:border-slate-700">
                  <AvatarFallback className="bg-slate-800 text-slate-100 text-sm font-semibold dark:bg-slate-700 dark:text-slate-200">
                    {userInitials}
                  </AvatarFallback>
                </Avatar>
                {!collapsed && (
                  <div className="flex-1 text-left min-w-0">
                    <p className="text-sm font-medium truncate">
                      {user?.email || "User"}
                    </p>
                    <p className="text-xs text-muted-foreground truncate">
                      Logged in
                    </p>
                  </div>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem
                className="text-destructive"
                onClick={handleLogout}
              >
                Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </aside>

      {/* ── Main Content ── */}
      <main
        className={`flex-1 min-w-0 h-full ${isOnChatPage ? "overflow-hidden" : "overflow-auto p-6"
          }`}
      >
        <Outlet
          context={{
            activeConversationId,
            setActiveConversationId,
            conversations,
            setConversations,
          }}
        />
      </main>
    </div>
  );
}
