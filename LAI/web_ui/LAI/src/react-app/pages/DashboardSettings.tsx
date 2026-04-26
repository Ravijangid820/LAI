import { useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";
import { Button } from "@/react-app/components/ui/button";
import { Input } from "@/react-app/components/ui/input";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/react-app/components/ui/tabs";
import { Switch } from "@/react-app/components/ui/switch";
import { Label } from "@/react-app/components/ui/label";
import { Textarea } from "@/react-app/components/ui/textarea";
import { useTheme } from "@/react-app/contexts/ThemeContext";
import {
  GearIcon,
  PersonIcon,
  BellIcon,
  PadlockIcon,
  KeyIcon,
  ExitIcon,
  SaveIcon,
  LensIcon,
  LensOffIcon,
  MoonIcon,
  SunIcon,
  EnvelopeIcon,
  PhoneIcon,
  PinIcon,
  BuildingIcon,
  EditIcon,
  CheckIcon,
  CloseIcon,
  ShieldColumnIcon,
} from "@/react-app/components/icons";

// ─────────────────────────────────────────────────────────────────────────────
// Auto icon — a small sun+moon split symbol for the "System" option
// ─────────────────────────────────────────────────────────────────────────────


export default function DashboardSettingsPage() {
  // ── Real theme control — same hook used by ThemeToggle in the chat header ──
  const { theme, setTheme } = useTheme();

  const [showPassword, setShowPassword] = useState(false);
  const [editingProfile, setEditingProfile] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const [profile, setProfile] = useState({
    firstName: "John",
    lastName: "Anderson",
    email: "john.anderson@windlegal.de",
    phone: "+49 30 123456",
    company: "Nordwind Legal Consultants",
    jobTitle: "Senior Legal Analyst",
    bio: "Specialized in wind energy legal due diligence and regulatory compliance.",
  });

  const [notifications, setNotifications] = useState({
    emailNotifications: true,
    riskAlerts: true,
    documentUpdates: true,
    weeklyReport: true,
    projectUpdates: false,
    teamInvites: true,
  });

  const [preferences, setPreferences] = useState({
    language: "en",
    dateFormat: "DD/MM/YYYY",
    timezone: "Europe/Berlin",
  });

  const [apiKeys, setApiKeys] = useState([
    {
      id: "1",
      name: "Production API Key",
      created: "2024-01-15",
      lastUsed: "2024-02-18",
      active: true,
    },
    {
      id: "2",
      name: "Development API Key",
      created: "2024-02-01",
      lastUsed: "2024-02-17",
      active: true,
    },
  ]);

  const handleProfileSave = () => {
    setSaveSuccess(true);
    setEditingProfile(false);
    setTimeout(() => setSaveSuccess(false), 3000);
  };

  const handleNotificationChange = (key: keyof typeof notifications) =>
    setNotifications((prev) => ({ ...prev, [key]: !prev[key] }));

  const handleDeleteApiKey = (id: string) =>
    setApiKeys(apiKeys.filter((k) => k.id !== id));

  // Theme options — value matches what useTheme / ThemeProvider expects
  const themeOptions = [
    { value: "light" as const, label: "Light", Icon: SunIcon },
    { value: "dark" as const, label: "Dark", Icon: MoonIcon },

  ];

  const notificationItems = [
    {
      key: "emailNotifications" as const,
      label: "Email Notifications",
      description: "Receive notifications via email",
    },
    {
      key: "riskAlerts" as const,
      label: "Risk Alerts",
      description: "Get notified about new risk assessments",
    },
    {
      key: "documentUpdates" as const,
      label: "Document Updates",
      description: "Notifications when documents are processed",
    },
    {
      key: "weeklyReport" as const,
      label: "Weekly Report",
      description: "Receive weekly summary reports",
    },
    {
      key: "projectUpdates" as const,
      label: "Project Updates",
      description: "Updates on project progress and status",
    },
    {
      key: "teamInvites" as const,
      label: "Team Invites",
      description: "Notifications about team collaboration invites",
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">
          Manage your account settings, preferences, and security
        </p>
      </div>

      <Tabs defaultValue="profile" className="w-full">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="profile" className="flex items-center gap-2">
            <PersonIcon className="w-4 h-4" />
            Profile
          </TabsTrigger>
          <TabsTrigger
            value="notifications"
            className="flex items-center gap-2"
          >
            <BellIcon className="w-4 h-4" />
            Notifications
          </TabsTrigger>
          <TabsTrigger value="preferences" className="flex items-center gap-2">
            <GearIcon className="w-4 h-4" />
            Preferences
          </TabsTrigger>
          <TabsTrigger value="security" className="flex items-center gap-2">
            <PadlockIcon className="w-4 h-4" />
            Security
          </TabsTrigger>
        </TabsList>

        {/* ── Profile ── */}
        <TabsContent value="profile" className="space-y-6">
          {saveSuccess && (
            <div className="p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center gap-3">
              <CheckIcon className="w-5 h-5 text-emerald-600 dark:text-emerald-500" />
              <span className="text-emerald-700 dark:text-emerald-400">
                Profile updated successfully!
              </span>
            </div>
          )}
          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="flex flex-row items-center justify-between pb-4">
              <CardTitle>Profile Information</CardTitle>
              <Button
                variant={editingProfile ? "outline" : "default"}
                size="sm"
                onClick={() => setEditingProfile(!editingProfile)}
              >
                {editingProfile ? (
                  <>
                    <CloseIcon className="w-4 h-4 mr-2" />
                    Cancel
                  </>
                ) : (
                  <>
                    <EditIcon className="w-4 h-4 mr-2" />
                    Edit
                  </>
                )}
              </Button>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-4 mb-6">
                <div className="w-16 h-16 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center">
                  <PersonIcon className="w-8 h-8 text-slate-500 dark:text-slate-400" />
                </div>
                {editingProfile && (
                  <Button variant="outline" size="sm">
                    Change Avatar
                  </Button>
                )}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[
                  {
                    id: "firstName",
                    label: "First Name",
                    Icon: null,
                    value: profile.firstName,
                    field: "firstName",
                  },
                  {
                    id: "lastName",
                    label: "Last Name",
                    Icon: null,
                    value: profile.lastName,
                    field: "lastName",
                  },
                  {
                    id: "email",
                    label: "Email",
                    Icon: EnvelopeIcon,
                    value: profile.email,
                    field: "email",
                  },
                  {
                    id: "phone",
                    label: "Phone",
                    Icon: PhoneIcon,
                    value: profile.phone,
                    field: "phone",
                  },
                  {
                    id: "company",
                    label: "Company",
                    Icon: BuildingIcon,
                    value: profile.company,
                    field: "company",
                  },
                  {
                    id: "jobTitle",
                    label: "Job Title",
                    Icon: null,
                    value: profile.jobTitle,
                    field: "jobTitle",
                  },
                ].map(({ id, label, Icon, value, field }) => (
                  <div key={id}>
                    <Label className="text-muted-foreground mb-2 flex items-center gap-2">
                      {Icon && <Icon className="w-4 h-4" />}
                      {label}
                    </Label>
                    <Input
                      value={value}
                      disabled={!editingProfile}
                      onChange={(e) =>
                        setProfile((prev) => ({
                          ...prev,
                          [field]: e.target.value,
                        }))
                      }
                      className="bg-muted/50"
                    />
                  </div>
                ))}
              </div>
              <div>
                <Label className="text-muted-foreground mb-2 block">Bio</Label>
                <Textarea
                  value={profile.bio}
                  disabled={!editingProfile}
                  onChange={(e) =>
                    setProfile((prev) => ({ ...prev, bio: e.target.value }))
                  }
                  className="bg-muted/50"
                  rows={4}
                />
              </div>
              {editingProfile && (
                <Button onClick={handleProfileSave} className="glow-sm">
                  <SaveIcon className="w-4 h-4 mr-2" />
                  Save Changes
                </Button>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Notifications ── */}
        <TabsContent value="notifications" className="space-y-6">
          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="pb-4">
              <CardTitle>Email Notifications</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {notificationItems.map((item) => (
                <div
                  key={item.key}
                  className="flex items-center justify-between p-4 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors"
                >
                  <div>
                    <p className="font-medium">{item.label}</p>
                    <p className="text-sm text-muted-foreground">
                      {item.description}
                    </p>
                  </div>
                  <Switch
                    checked={notifications[item.key]}
                    onCheckedChange={() => handleNotificationChange(item.key)}
                  />
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Preferences ── */}
        <TabsContent value="preferences" className="space-y-6">
          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="pb-4">
              <CardTitle>Display & Localization</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* ── THEME — wired to useTheme, same as ThemeToggle ── */}
              <div>
                <Label className="text-muted-foreground mb-3 block text-sm font-medium">
                  Appearance
                </Label>
                <div className="grid grid-cols-3 gap-3">
                  {themeOptions.map((opt) => {
                    const isActive = theme === opt.value;
                    return (
                      <button
                        key={opt.value}
                        onClick={() => setTheme(opt.value)}
                        className={`p-4 rounded-md border-2 flex flex-col items-center gap-2.5 transition-all ${isActive
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border/50 hover:border-primary/40 text-muted-foreground hover:text-foreground"
                          }`}
                      >
                        <opt.Icon className="w-5 h-5" />
                        <span className="text-sm font-medium">{opt.label}</span>
                        {isActive && (
                          <span className="text-xs text-primary/80 font-medium">
                            Active
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
                <p className="text-xs text-muted-foreground mt-2">
                  "Auto" follows your system's dark/light mode setting.
                </p>
              </div>

              {/* Language */}
              <div>
                <Label className="text-muted-foreground mb-2 block">
                  Language
                </Label>
                <select
                  value={preferences.language}
                  onChange={(e) =>
                    setPreferences((p) => ({ ...p, language: e.target.value }))
                  }
                  className="w-full px-3 py-2 rounded-lg bg-muted/50 border border-border/50 text-foreground"
                >
                  <option value="en">English</option>
                  <option value="de">Deutsch</option>
                  <option value="fr">Français</option>
                  <option value="es">Español</option>
                </select>
              </div>

              {/* Date Format */}
              <div>
                <Label className="text-muted-foreground mb-2 block">
                  Date Format
                </Label>
                <select
                  value={preferences.dateFormat}
                  onChange={(e) =>
                    setPreferences((p) => ({
                      ...p,
                      dateFormat: e.target.value,
                    }))
                  }
                  className="w-full px-3 py-2 rounded-lg bg-muted/50 border border-border/50 text-foreground"
                >
                  <option value="DD/MM/YYYY">DD/MM/YYYY</option>
                  <option value="MM/DD/YYYY">MM/DD/YYYY</option>
                  <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                </select>
              </div>

              {/* Timezone */}
              <div>
                <Label className="text-muted-foreground mb-2 flex items-center gap-2">
                  <PinIcon className="w-4 h-4" />
                  Timezone
                </Label>
                <select
                  value={preferences.timezone}
                  onChange={(e) =>
                    setPreferences((p) => ({ ...p, timezone: e.target.value }))
                  }
                  className="w-full px-3 py-2 rounded-lg bg-muted/50 border border-border/50 text-foreground"
                >
                  <option value="Europe/Berlin">Europe/Berlin (CET)</option>
                  <option value="Europe/London">Europe/London (GMT)</option>
                  <option value="Europe/Paris">Europe/Paris (CET)</option>
                  <option value="UTC">UTC</option>
                </select>
              </div>

              <Button className="glow-sm">
                <SaveIcon className="w-4 h-4 mr-2" />
                Save Preferences
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Security ── */}
        <TabsContent value="security" className="space-y-6">
          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="pb-4">
              <CardTitle>Password & Authentication</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label className="text-muted-foreground mb-2 block">
                  Current Password
                </Label>
                <div className="relative">
                  <Input
                    type={showPassword ? "text" : "password"}
                    placeholder="Enter your current password"
                    className="bg-muted/50 pr-10"
                  />
                  <button
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-3 text-muted-foreground hover:text-foreground"
                  >
                    {showPassword ? (
                      <LensOffIcon className="w-4 h-4" />
                    ) : (
                      <LensIcon className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>
              <div>
                <Label className="text-muted-foreground mb-2 block">
                  New Password
                </Label>
                <Input
                  type="password"
                  placeholder="Enter new password"
                  className="bg-muted/50"
                />
              </div>
              <div>
                <Label className="text-muted-foreground mb-2 block">
                  Confirm Password
                </Label>
                <Input
                  type="password"
                  placeholder="Confirm new password"
                  className="bg-muted/50"
                />
              </div>
              <Button className="glow-sm">
                <PadlockIcon className="w-4 h-4 mr-2" />
                Update Password
              </Button>
            </CardContent>
          </Card>

          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardHeader className="pb-4">
              <CardTitle>API Keys & Integrations</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Manage your API keys for third-party integrations
              </p>
              <div className="space-y-2">
                {apiKeys.map((key) => (
                  <div
                    key={key.id}
                    className="flex items-center justify-between p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors"
                  >
                    <div>
                      <p className="font-medium flex items-center gap-2">
                        <KeyIcon className="w-4 h-4" />
                        {key.name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Created {key.created} • Last used {key.lastUsed}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-xs px-2 py-1 rounded ${key.active ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-500 border border-emerald-500/20" : "bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-500/20"}`}
                      >
                        {key.active ? "Active" : "Inactive"}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDeleteApiKey(key.id)}
                        className="text-red-500 hover:text-red-600 hover:bg-red-500/10"
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <Button variant="outline">
                <KeyIcon className="w-4 h-4 mr-2" />
                Generate New API Key
              </Button>
            </CardContent>
          </Card>

          <Card className="bg-red-500/5 border-red-500/20">
            <CardHeader className="pb-4">
              <CardTitle className="text-red-600 flex items-center gap-2">
                <ShieldColumnIcon className="w-5 h-5" />
                Danger Zone
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Irreversible actions. Please proceed with caution.
              </p>
              <Button
                variant="outline"
                className="text-red-500 hover:text-red-600 border-red-500/20"
              >
                <ExitIcon className="w-4 h-4 mr-2" />
                Logout from All Devices
              </Button>
              <Button
                variant="outline"
                className="w-full text-red-500 hover:text-red-600 border-red-500/20 hover:bg-red-500/10"
              >
                Delete Account
              </Button>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
