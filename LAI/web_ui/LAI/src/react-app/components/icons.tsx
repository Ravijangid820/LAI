// ─────────────────────────────────────────────────────────────────────────────
// LAI ICON SYSTEM — Schematic / engineering blueprint aesthetic
// All icons are unique inline SVGs. Zero external dependencies.
// strokeWidth="1.4" base, accents at 1.8. Consistent 24×24 viewBox.
// ─────────────────────────────────────────────────────────────────────────────

type IconProps = { className?: string };

// ── Navigation & UI ───────────────────────────────────────────────────────────

// Wind rose compass — brand identity / wind domain
export const WindRoseIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="2" />
    <circle cx="12" cy="12" r="9" strokeDasharray="2 2" strokeWidth="0.8" />
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4" strokeWidth="1.6" />
    <path
      d="M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"
      strokeWidth="0.9"
    />
    <path d="M12 3l1 3h-2l1-3z" fill="currentColor" stroke="none" />
  </svg>
);

// Long arrow right — CTA, submit, proceed
export const ArrowRightIcon = ({ className }: IconProps) => (
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

// Diagonal arrow up-right — external link / trend up
export const ArrowUpRightIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M7 17L17 7M7 7h10v10" />
  </svg>
);

// Diagonal arrow down-right — trend down
export const ArrowDownRightIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M7 7l10 10M17 7v10H7" />
  </svg>
);

// Plus cross — new / add action
export const PlusIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
  >
    <path d="M12 4v16M4 12h16" />
  </svg>
);

// Close X — dismiss / cancel
export const CloseIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
  >
    <path d="M5 5l14 14M19 5L5 19" />
  </svg>
);

// Three vertical dots — context menu / kebab
export const DotsVerticalIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
  >
    <circle cx="12" cy="5" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="19" r="1.2" fill="currentColor" stroke="none" />
  </svg>
);

// Checkmark — success / confirmed
export const CheckIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 12l6 6L20 6" />
  </svg>
);

// Circle with checkmark — completed / verified status
export const CheckRingIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="9" />
    <path d="M8 12l3 3 5-6" strokeWidth="1.6" />
  </svg>
);

// Chevron right — step connector
export const ChevronRightIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M9 18l6-6-6-6" />
  </svg>
);

// ── Documents & Files ─────────────────────────────────────────────────────────

// Legal manuscript with paragraph mark — documents / analysis
export const ManuscriptIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M6 4c0-1.1.9-2 2-2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6" />
    <path d="M6 4c0 1.1-.9 2-2 2s-2 .9-2 2v12a2 2 0 0 0 2 2h2" />
    <line x1="10" y1="8" x2="17" y2="8" strokeWidth="1" />
    <line x1="10" y1="11" x2="17" y2="11" strokeWidth="1" />
    <line x1="10" y1="14" x2="14" y2="14" strokeWidth="1" />
    <path d="M8 7v8M8 7c0-1.1.9-2 2-2s2 .9 2 2-.9 2-2 2H8" strokeWidth="1.3" />
  </svg>
);

// Upload tray with arrow — file upload
export const UploadIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    <path d="M4 17H20" strokeWidth="1" strokeDasharray="2 2" />
    <path d="M12 15V3" strokeWidth="1.6" />
    <path d="M7 8l5-5 5 5" strokeWidth="1.6" />
  </svg>
);

// Download tray with arrow — file download
export const DownloadIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    <path d="M4 17H20" strokeWidth="1" strokeDasharray="2 2" />
    <path d="M12 3v12" strokeWidth="1.6" />
    <path d="M7 10l5 5 5-5" strokeWidth="1.6" />
  </svg>
);

// Archive box with lid — archive action
export const ArchiveIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="7" width="18" height="14" rx="1.5" />
    <path d="M3 7l2-4h14l2 4" />
    <path d="M9 12h6" strokeWidth="1.6" />
  </svg>
);

// Forensic lens — search / magnifier with crosshair
export const SearchIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M10.5 7v7M7 10.5h7" strokeWidth="0.9" />
    <path d="M15.5 15.5L21 21" strokeWidth="1.8" />
  </svg>
);

// Funnel — filter
export const FilterIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 5h18M6 10h12M9 15h6M11 20h2" />
  </svg>
);

// Delete / bin — trash, remove
export const TrashIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 7h16M10 7V5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v2" />
    <path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />
    <path d="M10 11v5M14 11v5" strokeWidth="1.1" />
  </svg>
);

// Storage cylinder — file storage / hard drive / size
export const StorageIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <ellipse cx="12" cy="6" rx="8" ry="3" />
    <path d="M4 6v5c0 1.66 3.58 3 8 3s8-1.34 8-3V6" />
    <path d="M4 11v5c0 1.66 3.58 3 8 3s8-1.34 8-3v-5" />
    <circle cx="16" cy="11" r="1" fill="currentColor" stroke="none" />
    <circle cx="16" cy="16" r="1" fill="currentColor" stroke="none" />
  </svg>
);

// Optical lens — eye / view / analyzed
export const LensIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
  </svg>
);

// Closed lens — eye off / hidden
export const LensOffIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M2 2l20 20" strokeWidth="1.6" />
    <path d="M6.7 6.7A10 10 0 0 0 2 12s3.5 7 10 7a9.9 9.9 0 0 0 5.3-1.53" />
    <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c6.5 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19" />
    <path d="M14.12 14.12A3 3 0 0 1 9.88 9.88" />
  </svg>
);

// Calendar grid — date / schedule
export const CalendarIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="5" width="18" height="17" rx="1.5" />
    <path d="M3 10h18" />
    <path d="M8 3v4M16 3v4" />
    <rect
      x="7"
      y="13"
      width="2"
      height="2"
      rx="0.4"
      fill="currentColor"
      stroke="none"
    />
    <rect
      x="11"
      y="13"
      width="2"
      height="2"
      rx="0.4"
      fill="currentColor"
      stroke="none"
    />
    <rect
      x="15"
      y="13"
      width="2"
      height="2"
      rx="0.4"
      fill="currentColor"
      stroke="none"
    />
    <rect
      x="7"
      y="17"
      width="2"
      height="2"
      rx="0.4"
      fill="currentColor"
      stroke="none"
    />
    <rect
      x="11"
      y="17"
      width="2"
      height="2"
      rx="0.4"
      fill="currentColor"
      stroke="none"
    />
  </svg>
);

// ── Projects & Cases ──────────────────────────────────────────────────────────

// Case folder — structured legal project
export const CaseFolderIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
    <line
      x1="3"
      y1="12"
      x2="21"
      y2="12"
      strokeWidth="1"
      strokeDasharray="2 1.5"
    />
    <path d="M8 16h4M8 14h2" strokeWidth="1" />
  </svg>
);

// Folder with plus — new project
export const NewFolderIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
    <path d="M12 11v6M9 14h6" strokeWidth="1.6" />
  </svg>
);

// Speech bubble with lines — legal consultation / chat
export const ConsultIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 4h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H8l-4 4V5a1 1 0 0 1 1-1z" />
    <line x1="8" y1="9" x2="16" y2="9" strokeWidth="1" />
    <line x1="8" y1="13" x2="13" y2="13" strokeWidth="1" />
  </svg>
);

// People silhouettes — team / users
export const TeamIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="9" cy="7" r="3" />
    <path d="M3 20v-1a6 6 0 0 1 6-6v0a6 6 0 0 1 6 6v1" />
    <circle cx="17" cy="8" r="2.5" />
    <path d="M21 20v-1a5 5 0 0 0-4-4.9" />
  </svg>
);

// ── Risk & Status ─────────────────────────────────────────────────────────────

// Traffic signal tower — risk assessment
export const SignalTowerIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="8" y="2" width="8" height="15" rx="2" strokeWidth="1.4" />
    <circle cx="12" cy="5.5" r="1.8" strokeWidth="1" />
    <circle cx="12" cy="9.5" r="1.8" strokeWidth="1" />
    <circle cx="12" cy="13.5" r="1.8" strokeWidth="1" />
    <circle cx="12" cy="13.5" r="1" fill="currentColor" stroke="none" />
    <line x1="12" y1="17" x2="12" y2="21" strokeWidth="1.4" />
    <line x1="9" y1="21" x2="15" y2="21" strokeWidth="1.6" />
    <path d="M12 4H9M9 4V2" strokeWidth="0.9" />
  </svg>
);

// Warning triangle with exclamation — alert / medium risk
export const AlertIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M10.3 3.5L2 19h20L13.7 3.5a2 2 0 0 0-3.4 0z" />
    <line x1="12" y1="10" x2="12" y2="14" strokeWidth="1.6" />
    <circle cx="12" cy="17" r="0.8" fill="currentColor" stroke="none" />
  </svg>
);

// X inside ring — high risk / error
export const DangerRingIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="9" />
    <path d="M9 9l6 6M15 9l-6 6" strokeWidth="1.6" />
  </svg>
);

// Trend line going up — positive trend / metrics
export const TrendUpIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polyline points="3 17 9 11 13 15 21 7" />
    <path d="M16 7h5v5" />
  </svg>
);

// Alert ring — active warning
export const AlertRingIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="9" />
    <line x1="12" y1="8" x2="12" y2="13" strokeWidth="1.6" />
    <circle cx="12" cy="16.5" r="0.8" fill="currentColor" stroke="none" />
  </svg>
);

// Hourglass — time / deadline
export const SandglassIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <line x1="5" y1="2" x2="19" y2="2" strokeWidth="1.8" />
    <line x1="5" y1="22" x2="19" y2="22" strokeWidth="1.8" />
    <path d="M7 2l5 8 5-8M7 22l5-8 5 8" />
    <circle cx="12" cy="12" r="0.7" fill="currentColor" stroke="none" />
  </svg>
);

// ── Settings & Account ────────────────────────────────────────────────────────

// Precision gear cog — settings
export const GearIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="3" />
    <path
      d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"
      strokeWidth="1.6"
    />
    <circle cx="12" cy="12" r="6" strokeWidth="0.8" strokeDasharray="2 1.5" />
  </svg>
);

// Person silhouette — user / profile
export const PersonIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="7" r="4" />
    <path d="M3 21v-1a9 9 0 0 1 9-9v0a9 9 0 0 1 9 9v1" />
  </svg>
);

// Bell with clapper — notifications
export const BellIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M6 10a6 6 0 0 1 12 0v5l2 2H4l2-2v-5z" />
    <path d="M12 3v1" strokeWidth="1.8" />
    <path d="M10 20a2 2 0 0 0 4 0" />
    <circle
      cx="18"
      cy="7"
      r="1.5"
      fill="currentColor"
      stroke="none"
      className="text-primary"
    />
  </svg>
);

// Padlock — security / locked
export const PadlockIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="5" y="11" width="14" height="11" rx="1.5" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    <circle cx="12" cy="16" r="1.5" strokeWidth="1.2" />
    <line x1="12" y1="17.5" x2="12" y2="19.5" strokeWidth="1.2" />
  </svg>
);

// Skeleton key — API keys / access
export const KeyIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="8" cy="9" r="4" />
    <path d="M12 9h9" />
    <path d="M18 9v3M21 9v2" strokeWidth="1.4" />
    <circle
      cx="8"
      cy="9"
      r="1.5"
      fill="currentColor"
      stroke="none"
      opacity="0.4"
    />
  </svg>
);

// Exit door with arrow — logout
export const ExitIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    <path d="M16 17l5-5-5-5" strokeWidth="1.6" />
    <path d="M21 12H9" strokeWidth="1.6" />
  </svg>
);

// Floppy disk — save
export const SaveIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M5 3h11l3 3v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
    <rect x="8" y="3" width="7" height="5" rx="0.5" strokeWidth="1" />
    <rect x="7" y="14" width="10" height="6" rx="0.5" strokeWidth="1" />
    <rect
      x="10"
      y="4"
      width="1.5"
      height="3"
      fill="currentColor"
      stroke="none"
    />
  </svg>
);

// Crescent moon — dark mode
export const MoonIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
  </svg>
);

// Sun with rays — light mode
export const SunIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
  </svg>
);

// Envelope — email / mail
export const EnvelopeIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="2" y="5" width="20" height="15" rx="1.5" />
    <path d="M2 7l10 7 10-7" />
  </svg>
);

// Telephone handset — phone
export const PhoneIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.24 11.5 11.5 0 0 0 3.6.6 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.21 2.45.6 3.57a1 1 0 0 1-.25 1L6.6 10.8z" />
  </svg>
);

// Location pin — timezone / location
export const PinIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7z" />
    <circle cx="12" cy="9" r="2.5" />
  </svg>
);

// Building — company / organization
export const BuildingIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="3" width="12" height="20" />
    <path d="M15 9h5a1 1 0 0 1 1 1v11H15" />
    <rect
      x="6"
      y="7"
      width="2.5"
      height="2.5"
      fill="currentColor"
      stroke="none"
      opacity="0.5"
    />
    <rect
      x="10"
      y="7"
      width="2.5"
      height="2.5"
      fill="currentColor"
      stroke="none"
      opacity="0.5"
    />
    <rect
      x="6"
      y="12"
      width="2.5"
      height="2.5"
      fill="currentColor"
      stroke="none"
      opacity="0.5"
    />
    <rect
      x="10"
      y="12"
      width="2.5"
      height="2.5"
      fill="currentColor"
      stroke="none"
      opacity="0.5"
    />
    <rect
      x="17"
      y="13"
      width="2"
      height="2"
      fill="currentColor"
      stroke="none"
      opacity="0.5"
    />
    <rect x="7" y="18" width="5" height="5" />
  </svg>
);

// Pencil with rule — edit / modify
export const EditIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M11 4H4a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h15a1 1 0 0 0 1-1v-7" />
    <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
  </svg>
);

// Shield with column — legal authority / security zone
export const ShieldColumnIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M12 2L4 6v6c0 5 3.5 9.7 8 11 4.5-1.3 8-6 8-11V6l-8-4z" />
    <rect x="10" y="8" width="4" height="8" rx="0.5" strokeWidth="1" />
    <line x1="9.5" y1="8" x2="14.5" y2="8" strokeWidth="1.5" />
    <line x1="9.5" y1="16" x2="14.5" y2="16" strokeWidth="1.5" />
    <line x1="11.3" y1="8.5" x2="11.3" y2="15.5" strokeWidth="0.6" />
    <line x1="12.7" y1="8.5" x2="12.7" y2="15.5" strokeWidth="0.6" />
  </svg>
);

// Circuit bolt — enterprise / power / quick action
export const CircuitBoltIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 12h3M18 12h3M12 3v3M12 18v3" strokeWidth="1" />
    <path d="M6 12H3M6 12v-3M6 9H9" strokeWidth="1" />
    <path d="M18 12h3M18 12v3M18 15h-3" strokeWidth="1" />
    <circle cx="6" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="18" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="6" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="12" cy="18" r="1.2" fill="currentColor" stroke="none" />
    <path d="M13.5 8l-3 4.5h3L10.5 17" strokeWidth="1.6" />
  </svg>
);

// Wax seal with column — legal precision CTA
export const SealColumnIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="11" r="8" strokeDasharray="3 1.5" strokeWidth="1" />
    <circle cx="12" cy="11" r="5.5" strokeWidth="1.3" />
    <rect x="10" y="7" width="4" height="8" rx="0.5" strokeWidth="1" />
    <line x1="9.5" y1="7" x2="14.5" y2="7" strokeWidth="1.5" />
    <line x1="9.5" y1="15" x2="14.5" y2="15" strokeWidth="1.5" />
    <line x1="11.3" y1="7.5" x2="11.3" y2="14.5" strokeWidth="0.6" />
    <line x1="12.7" y1="7.5" x2="12.7" y2="14.5" strokeWidth="0.6" />
    <path d="M8 19l4-3 4 3" strokeWidth="1.2" />
  </svg>
);

// ── Sidebar Layout Controls ───────────────────────────────────────────────────

// Panel collapse — sidebar open, click to close
export const PanelCollapseIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="3" width="4" height="18" rx="1" strokeWidth="1.4" />
    <line x1="11" y1="7" x2="21" y2="7" strokeWidth="1.1" />
    <line x1="11" y1="12" x2="21" y2="12" strokeWidth="1.1" />
    <line x1="11" y1="17" x2="18" y2="17" strokeWidth="1.1" />
    <path d="M16 9l-3 3 3 3" strokeWidth="1.6" />
  </svg>
);

// Panel expand — sidebar closed, click to open
export const PanelExpandIcon = ({ className }: IconProps) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="3" width="4" height="18" rx="1" strokeWidth="1.4" />
    <line x1="11" y1="7" x2="21" y2="7" strokeWidth="1.1" />
    <line x1="11" y1="12" x2="21" y2="12" strokeWidth="1.1" />
    <line x1="11" y1="17" x2="18" y2="17" strokeWidth="1.1" />
    <path d="M13 9l3 3-3 3" strokeWidth="1.6" />
  </svg>
);
