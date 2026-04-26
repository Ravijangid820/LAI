import { Link } from "react-router";
import { Button } from "@/react-app/components/ui/button";
import { Card } from "@/react-app/components/ui/card";
import { Logo } from "@/react-app/components/Logo";
import { ThemeToggle } from "@/react-app/components/ThemeToggle";

// ─────────────────────────────────────────────────────────────────────────────
// UNIQUE SCHEMATIC ICONS
// Aesthetic: architectural/engineering blueprint style
// Dual-line construction — outer skeleton + inner detail
// Inspired by technical drawings from wind farm engineering documents
// ─────────────────────────────────────────────────────────────────────────────

// Wind rose / compass rose — navigating complexity (hero badge)
const WindRoseIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="2" />
    <circle cx="12" cy="12" r="9" strokeDasharray="2 2" strokeWidth="0.8" />
    {/* 8-point compass rose */}
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4" strokeWidth="1.5" />
    <path
      d="M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"
      strokeWidth="0.9"
    />
    {/* North indicator */}
    <path d="M12 3l1 3h-2l1-3z" fill="currentColor" stroke="none" />
  </svg>
);

// Legal manuscript / parchment with quill strike — document analysis
const ManuscriptIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* Parchment body with rolled top-left corner */}
    <path d="M6 4c0-1.1.9-2 2-2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6" />
    <path d="M6 4c0 1.1-.9 2-2 2s-2 .9-2 2v12a2 2 0 0 0 2 2h2" />
    {/* Text lines — legal clauses */}
    <line x1="10" y1="8" x2="17" y2="8" strokeWidth="1" />
    <line x1="10" y1="11" x2="17" y2="11" strokeWidth="1" />
    <line x1="10" y1="14" x2="14" y2="14" strokeWidth="1" />
    {/* Paragraph mark ¶ */}
    <path d="M8 7v8M8 7c0-1.1.9-2 2-2s2 .9 2 2-.9 2-2 2H8" strokeWidth="1.3" />
  </svg>
);

// Traffic signal tower — schematic style with pole
const SignalTowerIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* Housing */}
    <rect x="8" y="2" width="8" height="15" rx="2" strokeWidth="1.4" />
    {/* Inner rings — signal circles */}
    <circle cx="12" cy="5.5" r="1.8" strokeWidth="1" />
    <circle cx="12" cy="9.5" r="1.8" strokeWidth="1" />
    <circle cx="12" cy="13.5" r="1.8" strokeWidth="1" />
    {/* Active fill — bottom green */}
    <circle cx="12" cy="13.5" r="1" fill="currentColor" stroke="none" />
    {/* Pole */}
    <line x1="12" y1="17" x2="12" y2="21" strokeWidth="1.4" />
    {/* Base plate */}
    <line x1="9" y1="21" x2="15" y2="21" strokeWidth="1.6" />
    {/* Bracket arm */}
    <path d="M12 4H9M9 4V2" strokeWidth="0.9" />
  </svg>
);

// Meridian arc / surveying instrument — regulatory precision
const MeridianIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* Theodolite / surveying arc */}
    <path d="M12 20V12" strokeWidth="1.4" />
    <path d="M12 12 A8 8 0 0 1 20 12" strokeWidth="1.4" />
    <path d="M12 12 A8 8 0 0 0 4 12" strokeWidth="1.4" />
    {/* Degree tick marks */}
    <path d="M12 4v2M19.3 7.7l-1.4 1.4M4.7 7.7l1.4 1.4" strokeWidth="1" />
    {/* Sight line */}
    <path d="M12 12l5.6-5.6" strokeDasharray="1.5 1" strokeWidth="0.9" />
    {/* Base */}
    <path d="M9 20h6M10 22h4" strokeWidth="1.3" />
    <circle cx="12" cy="12" r="1.5" strokeWidth="1" />
  </svg>
);

// Hourglass with sand flow — 5-day countdown, time-critical legal work
const SandglassIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* Top & bottom plates */}
    <line x1="5" y1="2" x2="19" y2="2" strokeWidth="1.8" />
    <line x1="5" y1="22" x2="19" y2="22" strokeWidth="1.8" />
    {/* Glass body */}
    <path d="M7 2l5 8 5-8" />
    <path d="M7 22l5-8 5 8" />
    {/* Sand — upper chamber nearly empty */}
    <path
      d="M9.5 5.5l2.5 3.5 2.5-3.5"
      fill="currentColor"
      stroke="none"
      opacity="0.3"
    />
    {/* Sand — lower chamber collecting */}
    <path d="M9 19l3-3 3 3" fill="currentColor" stroke="none" opacity="0.6" />
    {/* Drip point */}
    <circle cx="12" cy="12" r="0.7" fill="currentColor" stroke="none" />
  </svg>
);

// Bolt through circuit node — enterprise infrastructure
const CircuitBoltIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* Circuit board traces */}
    <path d="M3 12h3M18 12h3M12 3v3M12 18v3" strokeWidth="1" />
    <path d="M6 12H3M6 12v-3M6 9H9" strokeWidth="1" />
    <path d="M18 12h3M18 12v3M18 15h-3" strokeWidth="1" />
    {/* Node circles */}
    <circle cx="6" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="18" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="6" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="18" r="1.2" fill="currentColor" stroke="none" />
    {/* Central lightning bolt */}
    <path d="M13.5 8l-3 4.5h3L10.5 17" strokeWidth="1.6" />
  </svg>
);

// Wax seal with column — legal authority / jurisdiction
const SealColumnIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* Outer seal ring */}
    <circle cx="12" cy="11" r="8" strokeDasharray="3 1.5" strokeWidth="1" />
    {/* Inner seal */}
    <circle cx="12" cy="11" r="5.5" strokeWidth="1.3" />
    {/* Column inside — classical legal pillar */}
    <rect x="10" y="7" width="4" height="8" rx="0.5" strokeWidth="1" />
    <line x1="9.5" y1="7" x2="14.5" y2="7" strokeWidth="1.5" />
    <line x1="9.5" y1="15" x2="14.5" y2="15" strokeWidth="1.5" />
    {/* Column flutes */}
    <line x1="11.3" y1="7.5" x2="11.3" y2="14.5" strokeWidth="0.6" />
    <line x1="12.7" y1="7.5" x2="12.7" y2="14.5" strokeWidth="0.6" />
    {/* Ribbon bottom */}
    <path d="M8 19l4-3 4 3" strokeWidth="1.2" />
  </svg>
);

// Arrow — CTA
const LongArrowIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 12h16M14 6l6 6-6 6" />
  </svg>
);

// Step connector
const StepArrowIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M9 18l6-6-6-6" />
  </svg>
);

// ── Data ──────────────────────────────────────────────────────────────────────

const stats = [
  { value: "5", unit: " days", label: "vs 8–12 weeks traditional" },
  { value: "50%", unit: "", label: "cost reduction" },
  { value: "99.5%", unit: "", label: "accuracy rate" },
];

const features = [
  {
    Icon: ManuscriptIcon,
    title: "Automated Document Analysis",
    description:
      "Process hundreds of legal documents in minutes with intelligent clause-level extraction and cross-referencing.",
  },
  {
    Icon: SignalTowerIcon,
    title: "Traffic Light Assessment",
    description:
      "Visual risk categorization with red, yellow, green signal indicators for fast, confident decisions.",
  },
  {
    Icon: MeridianIcon,
    title: "Regulatory Monitoring",
    description:
      "Precision tracking of German wind energy law changes and BImSchG compliance with surveyor accuracy.",
  },
  {
    Icon: SandglassIcon,
    title: "5-Day Workflow",
    description:
      "Transform months of manual review into a structured, automated 5-day due diligence process.",
  },
  {
    Icon: CircuitBoltIcon,
    title: "Enterprise Ready",
    description:
      "Bank-grade security with SOC 2 compliance and German data residency for regulated environments.",
  },
  {
    Icon: SealColumnIcon,
    title: "Legal Precision",
    description:
      "Built on German wind energy law with clause-level citation, jurisdiction mapping and audit trails.",
  },
];

const workflow = [
  {
    step: 1,
    title: "Upload Documents",
    description:
      "Drag and drop permits, leases, grid agreements and environmental reports.",
  },
  {
    step: 2,
    title: "AI Analysis",
    description:
      "LAI extracts, classifies and cross-references every legal clause automatically.",
  },
  {
    step: 3,
    title: "Risk Assessment",
    description:
      "Receive traffic-light risk indicators per document, clause, and category.",
  },
  {
    step: 4,
    title: "Generate Report",
    description:
      "Export a comprehensive, audit-ready due diligence report in minutes.",
  },
];

// ── Page ──────────────────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background">
      {/* ── Navigation ── */}
      <nav className="fixed top-0 left-0 right-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <Logo size="md" />
            <div className="flex items-center gap-3">
              <Link to="/login">
                <Button variant="ghost" size="sm">
                  Sign In
                </Button>
              </Link>
              <Link to="/signup">
                <Button size="sm" className="shadow-sm">
                  Get Started
                </Button>
              </Link>
              <ThemeToggle />
            </div>
          </div>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section className="relative pt-32 pb-20 overflow-hidden">
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          {/* Removed blurred decorations for sharp/minimal UI */}
        </div>

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-4xl mx-auto">
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary/10 border border-primary/20 text-primary text-sm font-medium mb-8">
              <WindRoseIcon className="w-4 h-4" />
              Revolutionizing Wind Energy Due Diligence in Germany
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight mb-6">
              Legal AI That Transforms
              <span className="block text-primary mt-2">
                Due Diligence Forever
              </span>
            </h1>

            <p className="text-lg sm:text-xl text-muted-foreground max-w-2xl mx-auto mb-10">
              Transform your wind energy due diligence from a manual 8–12 week
              process into an automated 5-day workflow. Reduce costs by 50%
              while achieving 99.5% accuracy.
            </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16">
              <Link to="/signup">
                <Button size="lg" className="shadow-sm text-lg px-8 h-14 bg-blue-600 hover:bg-blue-700 text-white">
                  Start Free Trial
                  <LongArrowIcon className="ml-2 w-5 h-5" />
                </Button>
              </Link>
              <Button variant="outline" size="lg" className="text-lg px-8 h-14">
                Watch Demo
              </Button>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-8 max-w-3xl mx-auto">
              {stats.map((stat) => (
                <div key={stat.label} className="text-center">
                  <div className="text-3xl sm:text-4xl font-bold text-primary">
                    {stat.value}
                    <span className="text-primary/80">{stat.unit}</span>
                  </div>
                  <div className="text-sm text-muted-foreground mt-1">
                    {stat.label}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="py-20 border-t border-border/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4">
              Enterprise-Grade Legal AI
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Built specifically for German wind energy regulations with
              cutting-edge AI technology
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((feature) => (
              <Card
                key={feature.title}
                className="p-6 bg-card/50 backdrop-blur border-border/50 hover:border-primary/50 transition-all group"
              >
                <div className="w-12 h-12 rounded-md bg-primary/10 flex items-center justify-center mb-4 group-hover:bg-primary/20 transition-colors">
                  <feature.Icon className="w-6 h-6 text-primary" />
                </div>
                <h3 className="text-lg font-semibold mb-2">{feature.title}</h3>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  {feature.description}
                </p>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* ── Workflow ── */}
      <section
        id="workflow"
        className="py-20 border-t border-border/50 bg-card/30"
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold mb-4">
              How LAI Works
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Four structured steps to transform your due diligence process
            </p>
          </div>

          <div className="grid md:grid-cols-4 gap-6">
            {workflow.map((item, index) => (
              <div key={item.step} className="relative">
                <div className="flex flex-col items-center text-center">
                  <div className="w-16 h-16 rounded-md bg-slate-800 flex items-center justify-center text-2xl font-bold text-slate-200 mb-4 shadow-sm border border-slate-700">
                    {item.step}
                  </div>
                  <h3 className="text-lg font-semibold mb-2">{item.title}</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {item.description}
                  </p>
                </div>
                {index < workflow.length - 1 && (
                  <StepArrowIcon className="hidden md:block absolute top-8 -right-3 w-6 h-6 text-primary/50" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="py-20 border-t border-border/50">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <div className="relative">
            <div className="absolute inset-0 bg-gradient-to-r from-primary/20 to-indigo-500/20 rounded-md blur-3xl" />
            <div className="relative bg-card/80 backdrop-blur border border-border/50 rounded-md p-12">
              <div className="flex justify-center mb-6">
                <SealColumnIcon className="w-14 h-14 text-primary opacity-50" />
              </div>
              <h2 className="text-3xl sm:text-4xl font-bold mb-4">
                Ready to Transform Your Due Diligence?
              </h2>
              <p className="text-lg text-muted-foreground mb-8 max-w-xl mx-auto">
                Join leading wind energy companies already using LAI to
                accelerate their legal workflows.
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                <Link to="/signup">
                  <Button size="lg" className="shadow-sm text-lg px-8 h-14 bg-blue-600 hover:bg-blue-700 text-white">
                    Start Free Trial
                    <LongArrowIcon className="ml-2 w-5 h-5" />
                  </Button>
                </Link>
                <Link to="/login">
                  <Button
                    variant="outline"
                    size="lg"
                    className="text-lg px-8 h-14"
                  >
                    Sign In
                  </Button>
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="py-12 border-t border-border/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <Logo size="sm" />
            <div className="flex items-center gap-6 text-sm text-muted-foreground">
              <a href="#" className="hover:text-foreground transition-colors">
                Privacy
              </a>
              <a href="#" className="hover:text-foreground transition-colors">
                Terms
              </a>
              <a href="#" className="hover:text-foreground transition-colors">
                Contact
              </a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
