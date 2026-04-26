// ═══════════════════════════════════════════════════════════════════════════════
// DDiQ Demo Data & Report Types
// ═══════════════════════════════════════════════════════════════════════════════

// ─── Core Types ─────────────────────────────────────────────────────────────

export type Ampel = "green" | "yellow" | "red";

export interface AusgabeblattRow { label: string; value: string; ampel?: Ampel; note?: string; }
export interface AusgabeblattSection { id: string; title: string; rows: AusgabeblattRow[]; }

export interface WEAStatus {
  name: string; ampel: Ampel; owner: string; parcel: string;
  contract: string; lat: number; lng: number; address: string;
}

export interface InfraPoint {
  name: string; type: "substation" | "cable_start" | "cable_end" | "access_road";
  lat: number; lng: number;
}

// ─── Cadastral Parcel Type ──────────────────────────────────────────────────

export type ParcelStatus = "secured" | "negotiation" | "open" | "buffer" | "easement";

export interface CadastralParcel {
  id: string;
  parcelNumber: string;           // Flurstück e.g. "12/4"
  gemarkung: string;              // Cadastral district e.g. "Tostedt"
  flur: number;                   // Section number
  polygon: [number, number][];    // [[lat, lng], ...] boundary ring
  status: ParcelStatus;
  owner: string;
  area: number;                   // hectares
  contractRef: string | null;
  linkedWEA: string | null;       // WEA name or null
  notes?: string;
}

// ─── Report Data ────────────────────────────────────────────────────────────

export interface DDiQReportData {
  projectName: string; preparedBy: string; preparedFor: string; date: string;
  projectCenter: { lat: number; lng: number };
  sections: AusgabeblattSection[];
  weaStatuses: WEAStatus[];
  infrastructure: InfraPoint[];
  parcels: CadastralParcel[];
  findings: { domain: string; severity: Ampel; text: string }[];
  analyzedDocuments: string[];
}

export interface ReportPreset { id: string; name: string; description: string; sections: string[]; estimatedPages: string; }
export type ExportFormat = "pdf" | "docx" | "html" | "xlsx" | "csv" | "txt";
export interface FormatOption { id: ExportFormat; label: string; description: string; colorCls: string; }
export interface SectionMeta { id: string; label: string; desc: string; }
export interface DocumentItem {
  id: string; name: string; size: number; uploadDate: string;
  type: string; status: "analyzed" | "pending" | "archived"; category: string;
}

// ─── Cadastral Parcel Demo Data ─────────────────────────────────────────────
// Realistic polygons around Tostedt, Harburg district, Lower Saxony
// At 53°N: 0.001° lat ≈ 111m, 0.001° lng ≈ 67m
// Each parcel ≈ 2–4 hectares (typical north German agricultural plot)

const DEMO_PARCELS: CadastralParcel[] = [
  // ── WEA-linked parcels ──
  {
    id: "p1", parcelNumber: "12/4", gemarkung: "Tostedt", flur: 3,
    polygon: [[53.2901,9.7022],[53.2903,9.7060],[53.2883,9.7063],[53.2880,9.7024]],
    status: "secured", owner: "Hofmann, Heinrich", area: 2.8,
    contractRef: "UC-2024-001", linkedWEA: "WEA 1",
  },
  {
    id: "p2", parcelNumber: "12/7", gemarkung: "Tostedt", flur: 3,
    polygon: [[53.2883,9.7098],[53.2885,9.7140],[53.2864,9.7142],[53.2862,9.7096]],
    status: "secured", owner: "Meier, Anna", area: 2.4,
    contractRef: "UC-2024-002", linkedWEA: "WEA 2",
  },
  {
    id: "p3", parcelNumber: "14/1", gemarkung: "Tostedt", flur: 4,
    polygon: [[53.2853,9.7180],[53.2855,9.7224],[53.2832,9.7226],[53.2830,9.7178]],
    status: "secured", owner: "Municipality of Tostedt", area: 3.1,
    contractRef: "UC-2024-003", linkedWEA: "WEA 3",
  },
  {
    id: "p4", parcelNumber: "15/2", gemarkung: "Tostedt", flur: 4,
    polygon: [[53.2826,9.7068],[53.2828,9.7112],[53.2806,9.7114],[53.2804,9.7066]],
    status: "secured", owner: "Kroeger, Thomas", area: 2.6,
    contractRef: "UC-2024-004", linkedWEA: "WEA 4",
  },
  {
    id: "p5", parcelNumber: "15/8", gemarkung: "Tostedt", flur: 4,
    polygon: [[53.2810,9.7154],[53.2812,9.7198],[53.2789,9.7200],[53.2787,9.7152]],
    status: "secured", owner: "Lueders, Karin", area: 2.9,
    contractRef: "UC-2024-005", linkedWEA: "WEA 5",
  },
  {
    id: "p6", parcelNumber: "16/3", gemarkung: "Tostedt", flur: 5,
    polygon: [[53.2868,9.7244],[53.2870,9.7288],[53.2847,9.7290],[53.2845,9.7242]],
    status: "negotiation", owner: "Schmidt Estate (heirs)", area: 2.7,
    contractRef: null, linkedWEA: "WEA 6",
    notes: "Draft contract sent. Heir coordination required.",
  },
  {
    id: "p7", parcelNumber: "17/1", gemarkung: "Tostedt", flur: 5,
    polygon: [[53.2921,9.7168],[53.2923,9.7212],[53.2900,9.7214],[53.2898,9.7166]],
    status: "negotiation", owner: "Petersen, Jens", area: 3.0,
    contractRef: null, linkedWEA: "WEA 7",
    notes: "Fee dispute. Landowner requesting €12,000/yr above market.",
  },
  {
    id: "p8", parcelNumber: "18/5", gemarkung: "Tostedt", flur: 5,
    polygon: [[53.2788,9.7228],[53.2790,9.7274],[53.2766,9.7276],[53.2764,9.7226]],
    status: "open", owner: "Unknown (heir investigation)", area: 3.3,
    contractRef: null, linkedWEA: "WEA 8",
    notes: "Owner not identified. Probate court inquiry pending.",
  },

  // ── Buffer zone parcels (adjacent to WEA parcels, needed for setback) ──
  {
    id: "p9", parcelNumber: "12/5", gemarkung: "Tostedt", flur: 3,
    polygon: [[53.2903,9.7060],[53.2905,9.7098],[53.2885,9.7100],[53.2883,9.7063]],
    status: "buffer", owner: "Hofmann, Heinrich", area: 2.1,
    contractRef: "BZ-2024-001", linkedWEA: null,
    notes: "Buffer zone between WEA 1 and WEA 2. Secured via same owner.",
  },
  {
    id: "p10", parcelNumber: "14/2", gemarkung: "Tostedt", flur: 4,
    polygon: [[53.2830,9.7118],[53.2832,9.7156],[53.2810,9.7158],[53.2808,9.7116]],
    status: "buffer", owner: "Municipality of Tostedt", area: 1.8,
    contractRef: "BZ-2024-002", linkedWEA: null,
    notes: "Buffer between WEA 4 and WEA 5. Municipal land, secured.",
  },
  {
    id: "p11", parcelNumber: "16/4", gemarkung: "Tostedt", flur: 5,
    polygon: [[53.2847,9.7200],[53.2850,9.7244],[53.2828,9.7246],[53.2826,9.7198]],
    status: "open", owner: "Brandt, Friedrich", area: 2.2,
    contractRef: null, linkedWEA: null,
    notes: "Adjacent to WEA 6. Owner not yet contacted for buffer agreement.",
  },

  // ── Cable route easement (narrow strip) ──
  {
    id: "p12", parcelNumber: "20/1", gemarkung: "Tostedt", flur: 6,
    polygon: [[53.2800,9.7170],[53.2802,9.7180],[53.2752,9.7086],[53.2750,9.7076]],
    status: "easement", owner: "Municipality of Tostedt", area: 0.6,
    contractRef: "CE-2024-001", linkedWEA: null,
    notes: "Cable route easement, 10m width, 4.2 km. Municipality agreement signed.",
  },
];

// ─── Demo Report ────────────────────────────────────────────────────────────

export const DEMO_REPORT: DDiQReportData = {
  projectName: "Windpark Nordheide",
  preparedBy: "LAI Due Diligence System",
  preparedFor: "Nordheide Invest GmbH & Co. KG",
  date: new Date().toLocaleDateString("en-US", { day: "2-digit", month: "long", year: "numeric" }),
  analyzedDocuments: [],
  projectCenter: { lat: 53.284, lng: 9.715 },

  sections: [
    { id: "overview", title: "Project Overview", rows: [
      { label: "Project Name", value: "Windpark Nordheide" },
      { label: "Location", value: "District of Harburg, Lower Saxony, Germany" },
      { label: "Project Status", value: "Under Permit Review (BImSchG application filed)" },
      { label: "Project Type", value: "Greenfield" },
      { label: "Number of WEA", value: "8 Wind Turbines" },
      { label: "Type & Capacity", value: "Vestas V162 – 6.2 MW per unit" },
      { label: "Total Capacity", value: "49.6 MW" },
      { label: "Project Company", value: "Nordheide Wind GmbH" },
      { label: "Investors", value: "Nordheide Invest GmbH & Co. KG, HansaWind AG" },
      { label: "Grid Connection", value: "Substation Tostedt, 4.2 km cable route" },
      { label: "Wind Priority Zone", value: "Yes – per Regional Plan (RROP) Harburg 2021" },
    ]},
    { id: "land", title: "Land Security & Ownership", rows: [
      { label: "Usage Contracts", value: "6 of 8 locations secured (75%)", ampel: "yellow" },
      { label: "Land Registry", value: "4 easements registered (50%)", ampel: "yellow" },
      { label: "Buffer Zone Security", value: "Partially secured – 2 areas open", ampel: "yellow" },
      { label: "Cable Route", value: "100% secured – agreement with municipality of Tostedt", ampel: "green" },
      { label: "Access Roads", value: "100% secured", ampel: "green" },
      { label: "Contract Error Rate", value: "2 contracts with missing signatures, 1 inconsistent parcel", ampel: "red", note: "Renegotiation required for 3 contracts" },
      { label: "Contracts Reviewed", value: "12 contracts (8 usage, 2 cable, 2 access)" },
      { label: "Contracting Entity", value: "All with Nordheide Wind GmbH – consistent", ampel: "green" },
    ]},
    { id: "permits", title: "Permits & Regulatory Conditions", rows: [
      { label: "BImSchG Permit", value: "Applied Sep 12, 2024 – decision pending", ampel: "yellow" },
      { label: "Environmental Impact", value: "EIA completed – no objections", ampel: "green" },
      { label: "Species Protection", value: "Red kite shutdown required (Apr–Aug)", ampel: "yellow", note: "BioConsult 2024 report available" },
      { label: "Noise & Shadow", value: "Conditions met – CUBE Engineering", ampel: "green" },
      { label: "Authority Consultations", value: "12 consulted, 11 clear, 1 follow-up (heritage)", ampel: "yellow" },
      { label: "Recurring Inspections", value: "N/A (new installation)", ampel: "green" },
    ]},
    { id: "economics", title: "Economics & Operations", rows: [
      { label: "Feed-in Tariff", value: "EEG 2023 – 7.35 ct/kWh awarded", ampel: "green" },
      { label: "PPA", value: "PPA with EnBW until 2040, 8.1 ct/kWh", ampel: "green" },
      { label: "Profitability", value: "IRR 7.2% at P75 – bankable", ampel: "green" },
      { label: "Financing", value: "KfW IPEX + NordLB, term sheet signed", ampel: "green" },
      { label: "Securities", value: "Land charges registered, bank guarantee €2.4M", ampel: "green" },
      { label: "Operations", value: "Deutsche Windtechnik AG", ampel: "green" },
      { label: "Maintenance", value: "Vestas full-service 15yr, 97% availability", ampel: "green" },
      { label: "Insurance", value: "Allianz Wind Energy Policy incl. revenue loss", ampel: "green" },
      { label: "Open Liability", value: "None known", ampel: "green" },
    ]},
  ],

  weaStatuses: [
    { name: "WEA 1", ampel: "green", owner: "Hofmann, Heinrich", parcel: "Plot 12/4", contract: "UC-2024-001, signed", lat: 53.2891, lng: 9.7042, address: "Dieckhofweg, 21255 Tostedt" },
    { name: "WEA 2", ampel: "green", owner: "Meier, Anna", parcel: "Plot 12/7", contract: "UC-2024-002, signed", lat: 53.2873, lng: 9.7118, address: "Am Bahnhof 14, 21255 Tostedt" },
    { name: "WEA 3", ampel: "green", owner: "Municipality of Tostedt", parcel: "Plot 14/1", contract: "UC-2024-003, signed", lat: 53.2842, lng: 9.7201, address: "Heidkamp, 21255 Tostedt" },
    { name: "WEA 4", ampel: "green", owner: "Kroeger, Thomas", parcel: "Plot 15/2", contract: "UC-2024-004, signed", lat: 53.2815, lng: 9.7089, address: "Zinnhütte 8, 21255 Tostedt" },
    { name: "WEA 5", ampel: "green", owner: "Lueders, Karin", parcel: "Plot 15/8", contract: "UC-2024-005, signed", lat: 53.2798, lng: 9.7175, address: "Todtglüsinger Str., 21255 Tostedt" },
    { name: "WEA 6", ampel: "yellow", owner: "Schmidt Estate (heirs)", parcel: "Plot 16/3", contract: "Draft sent, awaiting response", lat: 53.2856, lng: 9.7265, address: "Wiesenweg 3, 21255 Tostedt" },
    { name: "WEA 7", ampel: "yellow", owner: "Petersen, Jens", parcel: "Plot 17/1", contract: "Under negotiation – fee dispute", lat: 53.2910, lng: 9.7190, address: "Buxtehuder Str. 22, 21255 Tostedt" },
    { name: "WEA 8", ampel: "red", owner: "Unknown (heir investigation)", parcel: "Plot 18/5", contract: "No contract – owner unidentified", lat: 53.2775, lng: 9.7250, address: "Schützenstr., 21255 Tostedt" },
  ],

  infrastructure: [
    { name: "Substation Tostedt (UW)", type: "substation", lat: 53.275, lng: 9.708 },
    { name: "Cable Route Start", type: "cable_start", lat: 53.2798, lng: 9.7175 },
    { name: "Cable Route End (UW)", type: "cable_end", lat: 53.275, lng: 9.708 },
    { name: "Main Access Road Entry", type: "access_road", lat: 53.292, lng: 9.705 },
  ],

  parcels: DEMO_PARCELS,

  findings: [
    { domain: "Land Security", severity: "red", text: "WEA 8 (Plot 18/5): Owner not identified. Heir investigation via probate court recommended." },
    { domain: "Land Security", severity: "red", text: "3 usage contracts have defects: missing signatures (2x), inconsistent parcel ID (1x)." },
    { domain: "Land Security", severity: "yellow", text: "WEA 6 & 7: Contract signing pending. Deadline Q1 2025 recommended." },
    { domain: "Land Security", severity: "yellow", text: "Buffer parcel 16/4: Adjacent to WEA 6, owner not contacted. Buffer gap risk." },
    { domain: "Permits", severity: "yellow", text: "BImSchG decision still outstanding. Permit expected Q2 2025." },
    { domain: "Permits", severity: "yellow", text: "Heritage protection: Follow-up request for sightline assessment." },
    { domain: "Permits", severity: "yellow", text: "Red kite shutdown reduces expected yield by ~1.8%." },
    { domain: "Economics", severity: "green", text: "Financing secured, PPA long-term, maintenance fully covered." },
  ],
};

// ─── Presets ────────────────────────────────────────────────────────────────

export const PRESETS: ReportPreset[] = [
  { id: "full", name: "Full DDiQ Report", description: "All tables, status map, cadastral map, and action items", sections: ["overview","land","permits","economics","statusmap","cadastralmap","locationmap","findings"], estimatedPages: "16–22" },
  { id: "executive", name: "Executive Summary", description: "Overview, risk summary, location map", sections: ["overview","statusmap","locationmap","findings"], estimatedPages: "5–7" },
  { id: "land", name: "Land Security Audit", description: "Contracts, cadastral parcels, traffic-light map", sections: ["overview","land","statusmap","cadastralmap","locationmap","findings"], estimatedPages: "12–16" },
  { id: "permit", name: "Permit & Compliance", description: "BImSchG, environment, authority consultations", sections: ["overview","permits","findings"], estimatedPages: "6–8" },
  { id: "economics", name: "Economic Review", description: "EEG/PPA, financing, operations, insurance", sections: ["overview","economics","findings"], estimatedPages: "6–8" },
];

// ─── Formats ────────────────────────────────────────────────────────────────

export const FORMAT_OPTIONS: FormatOption[] = [
  { id: "pdf", label: "PDF", description: "Print-ready, fixed layout", colorCls: "text-rose-600 dark:text-rose-400 bg-rose-500/10 border-rose-500/30" },
  { id: "docx", label: "DOCX", description: "Editable Word document", colorCls: "text-blue-600 dark:text-blue-400 bg-blue-500/10 border-blue-500/30" },
  { id: "html", label: "HTML", description: "Interactive, shareable", colorCls: "text-violet-600 dark:text-violet-400 bg-violet-500/10 border-violet-500/30" },
  { id: "xlsx", label: "XLSX", description: "Spreadsheet for analysis", colorCls: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/30" },
  { id: "csv", label: "CSV", description: "Plain data, any tool", colorCls: "text-amber-600 dark:text-amber-400 bg-amber-500/10 border-amber-500/30" },
  { id: "txt", label: "TXT", description: "Plain text, lightweight", colorCls: "text-slate-600 dark:text-slate-400 bg-slate-500/10 border-slate-500/30" },
];

// ─── Section Metadata ───────────────────────────────────────────────────────

export const SECTION_META: SectionMeta[] = [
  { id: "overview", label: "Project Overview", desc: "Name, location, WEA specs, companies" },
  { id: "land", label: "Land Security & Ownership", desc: "Contracts, land registry, error rate" },
  { id: "permits", label: "Permits & Conditions", desc: "BImSchG, EIA, species protection" },
  { id: "economics", label: "Economics & Operations", desc: "EEG, PPA, financing, maintenance" },
  { id: "statusmap", label: "Status Map (Traffic Light)", desc: "Green / Yellow / Red per WEA" },
  { id: "cadastralmap", label: "Cadastral Parcel Map", desc: "Color-coded parcel boundaries with contract status" },
  { id: "locationmap", label: "Location Map", desc: "Interactive map with WEA positions & infrastructure" },
  { id: "findings", label: "Action Items & Recommendations", desc: "Prioritized issues and risks" },
];

// ─── Demo Documents ─────────────────────────────────────────────────────────

export const DEMO_DOCUMENTS: DocumentItem[] = [
  { id: "1", name: "permit_application_2024.pdf", size: 2.4, uploadDate: "2024-02-18", type: "PDF", status: "analyzed", category: "Permits" },
  { id: "2", name: "land_lease_agreement.docx", size: 1.1, uploadDate: "2024-02-15", type: "Word", status: "analyzed", category: "Legal" },
  { id: "3", name: "environmental_impact_report.pdf", size: 5.8, uploadDate: "2024-02-14", type: "PDF", status: "analyzed", category: "Environmental" },
  { id: "4", name: "technical_specifications.xlsx", size: 0.8, uploadDate: "2024-02-10", type: "Excel", status: "pending", category: "Technical" },
  { id: "5", name: "grid_connection_procedure.pdf", size: 3.2, uploadDate: "2024-02-08", type: "PDF", status: "archived", category: "Grid" },
];