"use client";

import { useState, useMemo } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";
import { Button } from "@/react-app/components/ui/button";
import { Badge } from "@/react-app/components/ui/badge";
import { Progress } from "@/react-app/components/ui/progress";
import { Separator } from "@/react-app/components/ui/separator";
import { cn } from "@/react-app/lib/utils";
import {
  DownloadIcon,
  ManuscriptIcon,
  CheckIcon,
  CheckRingIcon,
  SandglassIcon,
  ArrowRightIcon,
  LensIcon,
  ArchiveIcon,
  SearchIcon,
} from "@/react-app/components/icons";
import { Input } from "@/react-app/components/ui/input";

import {
  DEMO_REPORT,
  PRESETS,
  FORMAT_OPTIONS,
  SECTION_META,
  type Ampel,
  type DDiQReportData,
  type AusgabeblattSection,
  type WEAStatus,
  type DocumentItem,
  type CadastralParcel,
  type ParcelStatus,
  type ReportPreset,
  type ExportFormat,
} from "@/react-app/lib/ddiqDemoData";

// ═══════════════════════════════════════════════════════════════════════════════
// UI HELPERS
// ═══════════════════════════════════════════════════════════════════════════════

const AmpelDot = ({
  status,
  size = "sm",
}: {
  status: Ampel;
  size?: "sm" | "md";
}) => (
  <span
    className={cn(
      "inline-block rounded-full flex-shrink-0",
      size === "md" ? "w-3 h-3" : "w-2 h-2",
      { green: "bg-emerald-500", yellow: "bg-amber-500", red: "bg-rose-500" }[
        status
      ],
    )}
  />
);

const AmpelBadge = ({ status }: { status: Ampel }) => {
  const c = {
    green: {
      bg: "bg-emerald-500/10",
      text: "text-emerald-700 dark:text-emerald-400",
      l: "Secured",
    },
    yellow: {
      bg: "bg-amber-500/10",
      text: "text-amber-700 dark:text-amber-400",
      l: "Partial",
    },
    red: {
      bg: "bg-rose-500/10",
      text: "text-rose-700 dark:text-rose-400",
      l: "Open",
    },
  }[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium",
        c.bg,
        c.text,
      )}
    >
      <AmpelDot status={status} />
      {c.l}
    </span>
  );
};

// ── Ausgabeblatt Table ──────────────────────────────────────────────────────

const AusgabeblattTable = ({ section }: { section: AusgabeblattSection }) => (
  <div className="rounded-lg border border-border/60 overflow-hidden">
    <div className="bg-slate-50 dark:bg-slate-800/60 px-4 py-2.5 border-b border-border/40">
      <h4 className="text-sm font-semibold">{section.title}</h4>
    </div>
    <div className="divide-y divide-border/30">
      {section.rows.map((r, i) => (
        <div key={i} className="flex items-start gap-3 px-4 py-2.5 text-sm">
          <span className="text-muted-foreground font-medium min-w-[200px] flex-shrink-0">
            {r.label}
          </span>
          <div className="flex-1 min-w-0">
            <div className="flex items-start gap-2">
              {r.ampel && <AmpelDot status={r.ampel} size="md" />}
              <span>{r.value}</span>
            </div>
            {r.note && (
              <p className="text-xs text-amber-600 dark:text-amber-400 mt-1 italic">
                {r.note}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  </div>
);

// ── Status Map (traffic-light cards) ────────────────────────────────────────

const StatusMap = ({ statuses }: { statuses: WEAStatus[] }) => {
  const c = {
    green: statuses.filter((s) => s.ampel === "green").length,
    yellow: statuses.filter((s) => s.ampel === "yellow").length,
    red: statuses.filter((s) => s.ampel === "red").length,
  };
  return (
    <div className="rounded-lg border border-border/60 overflow-hidden">
      <div className="bg-slate-50 dark:bg-slate-800/60 px-4 py-2.5 border-b border-border/40">
        <h4 className="text-sm font-semibold">Land Security Status Map</h4>
      </div>
      <div className="p-4">
        <div className="flex items-center gap-6 mb-4 text-xs">
          <span className="flex items-center gap-1.5">
            <AmpelDot status="green" size="md" />
            Fully Secured ({c.green})
          </span>
          <span className="flex items-center gap-1.5">
            <AmpelDot status="yellow" size="md" />
            In Negotiation ({c.yellow})
          </span>
          <span className="flex items-center gap-1.5">
            <AmpelDot status="red" size="md" />
            Open Issues ({c.red})
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {statuses.map((w) => (
            <div
              key={w.name}
              className={cn(
                "p-3 rounded-md border",
                {
                  green: "border-emerald-500/40 bg-emerald-500/5",
                  yellow: "border-amber-500/40 bg-amber-500/5",
                  red: "border-rose-500/40 bg-rose-500/5",
                }[w.ampel],
              )}
            >
              <div className="flex items-center gap-2 mb-1">
                <AmpelDot status={w.ampel} size="md" />
                <span className="text-sm font-semibold">{w.name}</span>
                <AmpelBadge status={w.ampel} />
              </div>
              <div className="text-xs text-muted-foreground space-y-0.5 ml-5">
                <p>Owner: {w.owner}</p>
                <p>Parcel: {w.parcel}</p>
                <p>Contract: {w.contract}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// ── Findings ────────────────────────────────────────────────────────────────

const FindingsTable = ({
  findings,
}: {
  findings: DDiQReportData["findings"];
}) => (
  <div className="rounded-lg border border-border/60 overflow-hidden">
    <div className="bg-slate-50 dark:bg-slate-800/60 px-4 py-2.5 border-b border-border/40">
      <h4 className="text-sm font-semibold">Action Items & Open Issues</h4>
    </div>
    <div className="divide-y divide-border/30">
      {findings.map((f, i) => (
        <div key={i} className="flex items-start gap-3 px-4 py-3">
          <AmpelDot status={f.severity} size="md" />
          <div className="flex-1 min-w-0">
            <span className="text-xs font-medium text-muted-foreground">
              {f.domain}
            </span>
            <p className="text-sm mt-0.5">{f.text}</p>
          </div>
        </div>
      ))}
    </div>
  </div>
);

// ── Cadastral Parcel Table (preview) ────────────────────────────────────────

const CadastralTable = ({ parcels }: { parcels: CadastralParcel[] }) => {
  const totalArea = parcels.reduce((s, p) => s + p.area, 0);
  const securedArea = parcels
    .filter(
      (p) =>
        p.status === "secured" ||
        p.status === "buffer" ||
        p.status === "easement",
    )
    .reduce((s, p) => s + p.area, 0);

  return (
    <div className="rounded-lg border border-border/60 overflow-hidden">
      <div className="bg-slate-50 dark:bg-slate-800/60 px-4 py-2.5 border-b border-border/40 flex items-center justify-between">
        <h4 className="text-sm font-semibold">
          Cadastral Parcels (Flurstücke)
        </h4>
        <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
          <span>{parcels.length} parcels</span>
          <span>{totalArea.toFixed(1)} ha total</span>
          <span>{((securedArea / totalArea) * 100).toFixed(0)}% secured</span>
        </div>
      </div>
      <div className="divide-y divide-border/30">
        {parcels.map((p) => {
          const pc = PARCEL_STATUS_COLORS[p.status];
          return (
            <div
              key={p.id}
              className="flex items-start gap-3 px-4 py-2.5 text-sm"
            >
              <span
                style={{
                  width: 14,
                  height: 10,
                  borderRadius: 2,
                  background: pc.fill,
                  border: `1.5px solid ${pc.stroke}`,
                  flexShrink: 0,
                  marginTop: 4,
                }}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold">Flst. {p.parcelNumber}</span>
                  <span className="text-xs text-muted-foreground">
                    Gemarkung {p.gemarkung}, Flur {p.flur}
                  </span>
                  <span
                    style={{ color: pc.stroke, background: `${pc.stroke}10` }}
                    className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
                  >
                    {pc.label}
                  </span>
                  {p.linkedWEA && (
                    <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                      {p.linkedWEA}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                  <span>{p.owner}</span>
                  <span>{p.area} ha</span>
                  {p.contractRef && (
                    <span className="text-emerald-600 dark:text-emerald-500">
                      {p.contractRef}
                    </span>
                  )}
                </div>
                {p.notes && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1 italic">
                    {p.notes}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// LOCATION MAP — Uses ProjectLocationMap (react-leaflet) for preview,
//                static SVG for downloaded exports
// ═══════════════════════════════════════════════════════════════════════════════

import ProjectLocationMap from "@/react-app/components/ProjectLocationMap";

const AMPEL_HEX: Record<Ampel, string> = {
  green: "#059669",
  yellow: "#d97706",
  red: "#dc2626",
};
const AMPEL_LABEL: Record<Ampel, string> = {
  green: "Secured",
  yellow: "Partial",
  red: "Open",
};

const PARCEL_STATUS_COLORS: Record<
  ParcelStatus,
  { fill: string; stroke: string; label: string }
> = {
  secured: { fill: "#05966930", stroke: "#059669", label: "Secured" },
  negotiation: { fill: "#d9770630", stroke: "#d97706", label: "Negotiation" },
  open: { fill: "#dc262630", stroke: "#dc2626", label: "Open" },
  buffer: { fill: "#3b82f620", stroke: "#3b82f6", label: "Buffer Zone" },
  easement: { fill: "#6366f118", stroke: "#6366f1", label: "Easement" },
};

// ═══════════════════════════════════════════════════════════════════════════════
// REPORT GENERATORS
// ═══════════════════════════════════════════════════════════════════════════════

function generateHTML(d: DDiQReportData, a: string[]): string {
  const secs = d.sections.filter((s) => a.includes(s.id));
  const ac = (x: Ampel) =>
    ({ green: "#059669", yellow: "#d97706", red: "#dc2626" })[x];
  const al = (x: Ampel) =>
    ({ green: "Secured", yellow: "Partial", red: "Open" })[x];

  const secH = secs
    .map(
      (s) =>
        `<h2 style="font-size:15px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">${s.title}</h2>` +
        `<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="background:#f8fafc;">` +
        `<th style="text-align:left;padding:8px 12px;border:1px solid #e2e8f0;width:220px;">Category</th>` +
        `<th style="text-align:left;padding:8px 12px;border:1px solid #e2e8f0;">Status / Details</th></tr></thead><tbody>` +
        s.rows
          .map(
            (r) =>
              `<tr><td style="padding:8px 12px;border:1px solid #e2e8f0;font-weight:500;vertical-align:top;">${r.label}</td>` +
              `<td style="padding:8px 12px;border:1px solid #e2e8f0;">${r.ampel ? `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${ac(r.ampel)};margin-right:6px;vertical-align:middle;"></span>` : ""}${r.value}${r.note ? `<br><em style="color:#d97706;font-size:12px;">${r.note}</em>` : ""}</td></tr>`,
          )
          .join("") +
        `</tbody></table>`,
    )
    .join("");

  const docList =
    d.analyzedDocuments.length > 0
      ? `<h2 style="font-size:15px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">Analyzed Documents</h2><ul style="font-size:13px;color:#475569;">${d.analyzedDocuments.map((n) => `<li style="margin:4px 0;">${n}</li>`).join("")}</ul>`
      : "";

  const mapH = a.includes("statusmap")
    ? `<h2 style="font-size:15px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">Land Security Status Map</h2>` +
      `<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="background:#f8fafc;">` +
      `<th style="padding:8px 12px;border:1px solid #e2e8f0;">WEA</th><th style="padding:8px 12px;border:1px solid #e2e8f0;">Status</th>` +
      `<th style="padding:8px 12px;border:1px solid #e2e8f0;">Owner</th><th style="padding:8px 12px;border:1px solid #e2e8f0;">Parcel</th>` +
      `<th style="padding:8px 12px;border:1px solid #e2e8f0;">Contract</th></tr></thead><tbody>` +
      d.weaStatuses
        .map(
          (w) =>
            `<tr><td style="padding:8px 12px;border:1px solid #e2e8f0;font-weight:600;">${w.name}</td>` +
            `<td style="padding:8px 12px;border:1px solid #e2e8f0;"><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${ac(w.ampel)};margin-right:6px;vertical-align:middle;"></span>${al(w.ampel)}</td>` +
            `<td style="padding:8px 12px;border:1px solid #e2e8f0;">${w.owner}</td>` +
            `<td style="padding:8px 12px;border:1px solid #e2e8f0;">${w.parcel}</td>` +
            `<td style="padding:8px 12px;border:1px solid #e2e8f0;">${w.contract}</td></tr>`,
        )
        .join("") +
      `</tbody></table>`
    : "";

  // ── Cadastral Parcels table for export ──
  const PC: Record<string, { stroke: string; label: string }> = {
    secured: { stroke: "#059669", label: "Secured" },
    negotiation: { stroke: "#d97706", label: "Negotiation" },
    open: { stroke: "#dc2626", label: "Open" },
    buffer: { stroke: "#3b82f6", label: "Buffer Zone" },
    easement: { stroke: "#6366f1", label: "Easement" },
  };
  const cadastH =
    a.includes("cadastralmap") && d.parcels.length > 0
      ? `<h2 style="font-size:15px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">Cadastral Parcels (Flurstücke)</h2>` +
        `<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="background:#f8fafc;">` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Flurstück</th>` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Gemarkung</th>` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Owner</th>` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Area</th>` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">WEA</th>` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Contract</th>` +
        `<th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Status</th></tr></thead><tbody>` +
        d.parcels
          .map((p) => {
            const pc = PC[p.status] || { stroke: "#64748b", label: p.status };
            return (
              `<tr><td style="padding:6px 10px;border:1px solid #e2e8f0;font-weight:600;">${p.parcelNumber}</td>` +
              `<td style="padding:6px 10px;border:1px solid #e2e8f0;">${p.gemarkung}, Flur ${p.flur}</td>` +
              `<td style="padding:6px 10px;border:1px solid #e2e8f0;">${p.owner}</td>` +
              `<td style="padding:6px 10px;border:1px solid #e2e8f0;">${p.area} ha</td>` +
              `<td style="padding:6px 10px;border:1px solid #e2e8f0;">${p.linkedWEA || "—"}</td>` +
              `<td style="padding:6px 10px;border:1px solid #e2e8f0;">${p.contractRef || "—"}</td>` +
              `<td style="padding:6px 10px;border:1px solid #e2e8f0;"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${pc.stroke};margin-right:4px;vertical-align:middle;"></span>${pc.label}</td></tr>`
            );
          })
          .join("") +
        `</tbody></table>`
      : "";

  // ── Location Map: full interactive Leaflet with Turbines/Parcels toggle ──
  const hasLocMap = a.includes("locationmap");
  const hasCadast = a.includes("cadastralmap") && d.parcels.length > 0;
  const leafletHead = hasLocMap
    ? `<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>` +
      `<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"><\/script>`
    : "";
  const locH = hasLocMap
    ? (() => {
        const center = {
          lat:
            d.weaStatuses.reduce((s, w) => s + w.lat, 0) / d.weaStatuses.length,
          lng:
            d.weaStatuses.reduce((s, w) => s + w.lng, 0) / d.weaStatuses.length,
        };
        const cableStart = d.infrastructure.find(
          (p) => p.type === "cable_start",
        );
        const cableEnd = d.infrastructure.find((p) => p.type === "cable_end");

        // Parcel colors for JS
        const PCS: Record<string, string> = {
          secured: "#059669",
          negotiation: "#d97706",
          open: "#dc2626",
          buffer: "#3b82f6",
          easement: "#8b5cf6",
        };
        const PCL: Record<string, string> = {
          secured: "Secured",
          negotiation: "In Negotiation",
          open: "Not Secured",
          buffer: "Buffer Zone",
          easement: "Cable Easement",
        };

        // Toggle button HTML (only if parcels exist)
        const toggleHTML = hasCadast
          ? `
      <div id="ddiq-toggle" style="display:flex;gap:0;background:#f1f5f9;border-radius:6px;padding:2px;border:1px solid #e2e8f0;margin-bottom:12px;width:fit-content;">
        <button id="btn-turbines" onclick="switchView('turbines')" style="font:600 12px/1 system-ui;padding:7px 16px;border-radius:4px;border:none;cursor:pointer;background:#fff;color:#0f172a;box-shadow:0 1px 3px rgba(0,0,0,.08);transition:all .15s;">Turbines</button>
        <button id="btn-parcels" onclick="switchView('parcels')" style="font:600 12px/1 system-ui;padding:7px 16px;border-radius:4px;border:none;cursor:pointer;background:transparent;color:#64748b;transition:all .15s;">Parcels</button>
      </div>`
          : "";

        return (
          `<h2 style="font-size:15px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">Project Location Map</h2>` +
          toggleHTML +
          `<div id="ddiq-map" style="width:100%;height:480px;border-radius:10px;border:1px solid #cbd5e1;margin-bottom:16px;"></div>` +
          `<script>
(function(){
  var map = L.map('ddiq-map', { zoomControl: true }).setView([${center.lat}, ${center.lng}], 14);
  var street = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap', maxZoom: 19 });
  var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: '© Esri', maxZoom: 18 });
  var topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { attribution: '© OpenTopoMap', maxZoom: 17 });
  street.addTo(map);
  L.control.layers({ 'Street': street, 'Satellite': satellite, 'Topographic': topo }, {}, { position: 'topright' }).addTo(map);

  // ── Shared: Cable + Infrastructure ──
  var sharedGroup = L.layerGroup().addTo(map);

  ${cableStart && cableEnd ? `L.polyline([[${cableStart.lat},${cableStart.lng}],[${cableEnd.lat},${cableEnd.lng}]], { color: '#6366f1', weight: 2.5, dashArray: '10 6', opacity: 0.7 }).addTo(sharedGroup).bindPopup('<b>Cable Route</b><br><span style="color:#64748b;">4.2 km → Substation Tostedt</span>');` : ""}

  ${d.infrastructure
    .filter((p) => p.type !== "cable_start")
    .map((p) => {
      const emoji =
        {
          substation: "⚡",
          cable_end: "⚡",
          access_road: "🛤",
          cable_start: "·",
        }[p.type] || "·";
      const bg =
        p.type === "substation" || p.type === "cable_end"
          ? "#6366f1"
          : "#64748b";
      return `L.marker([${p.lat},${p.lng}], { icon: L.divIcon({ className:'', iconSize:[26,26], iconAnchor:[13,13], popupAnchor:[0,-14],
      html:'<div style="width:26px;height:26px;background:${bg}15;border:1.5px solid ${bg};border-radius:5px;display:flex;align-items:center;justify-content:center;font-size:12px;">${emoji}</div>'
    })}).addTo(sharedGroup).bindPopup('<b>${p.name}</b><br><span style="color:#94a3b8;font-size:11px;">${p.lat.toFixed(5)}°N, ${p.lng.toFixed(5)}°E</span>');`;
    })
    .join("\n  ")}

  // ── Turbines Layer ──
  var turbineGroup = L.layerGroup().addTo(map);

  ${d.weaStatuses
    .map((w) => {
      const c = AMPEL_HEX[w.ampel];
      return `(function(){
    var ic = L.divIcon({ className:'', iconSize:[30,30], iconAnchor:[15,15], popupAnchor:[0,-17],
      html:'<div style="width:30px;height:30px;"><div style="position:absolute;inset:0;background:${c};border:2.5px solid #fff;border-radius:50%;box-shadow:0 2px 8px ${c}55;display:flex;align-items:center;justify-content:center;"><span style="color:#fff;font-size:11px;font-weight:800;font-family:system-ui;">${w.name.replace("WEA ", "")}</span></div></div>'
    });
    L.marker([${w.lat},${w.lng}], { icon: ic }).addTo(turbineGroup)
      .bindPopup('<div style="font:12px/1.6 system-ui;min-width:200px;padding:10px 12px;">'
        + '<div style="display:flex;align-items:center;gap:6px;padding-bottom:6px;margin-bottom:6px;border-bottom:1px solid #f1f5f9;">'
        + '<span style="width:9px;height:9px;border-radius:50%;background:${c};border:1.5px solid #fff;box-shadow:0 0 0 1px ${c}40;"></span>'
        + '<strong style="font-size:13px;">${w.name}</strong>'
        + '<span style="font-size:9px;font-weight:700;padding:1px 7px;border-radius:4px;background:${c}10;color:${c};margin-left:auto;">${AMPEL_LABEL[w.ampel]}</span></div>'
        + '<div style="font-size:11px;color:#475569;">'
        + '<div><b style="color:#1e293b;">Owner</b> ${w.owner}</div>'
        + '<div><b style="color:#1e293b;">Parcel</b> ${w.parcel}</div>'
        + '<div><b style="color:#1e293b;">Address</b> ${w.address}</div>'
        + '<div><b style="color:#1e293b;">Contract</b> ${w.contract}</div></div>'
        + '<div style="font-size:10px;color:#94a3b8;margin-top:5px;padding-top:5px;border-top:1px solid #f1f5f9;">${w.lat.toFixed(5)}°N, ${w.lng.toFixed(5)}°E</div></div>')
      .bindTooltip('${w.name}', { direction:'top', offset:[0,-17], permanent:true,
        className:'plm-wea-tt' });
  })();`;
    })
    .join("\n  ")}

  ${
    hasCadast
      ? `
  // ── Parcels Layer ──
  var parcelGroup = L.layerGroup();

  ${d.parcels
    .map((p) => {
      const c = PCS[p.status] || "#64748b";
      const lb = PCL[p.status] || p.status;
      const isEasement = p.status === "easement";
      // Find top-left corner for label
      const tlIdx = p.polygon.reduce(
        (bi, pt, i, arr) =>
          pt[0] > arr[bi][0] || (pt[0] === arr[bi][0] && pt[1] < arr[bi][1])
            ? i
            : bi,
        0,
      );
      const tl = p.polygon[tlIdx];
      return `(function(){
    L.polygon([${p.polygon.map((pt) => `[${pt[0]},${pt[1]}]`).join(",")}], {
      fillColor:'${c}', fillOpacity:0.2, color:'${c}', weight:${isEasement ? 1.5 : 2.5},
      ${isEasement ? "dashArray:'6 4'," : ""} opacity:0.9
    }).addTo(parcelGroup).bindPopup(
      '<div style="font:12px/1.6 system-ui;min-width:200px;padding:10px 12px;">'
      + '<div style="display:flex;align-items:center;gap:6px;padding-bottom:6px;margin-bottom:6px;border-bottom:1px solid #f1f5f9;">'
      + '<span style="width:9px;height:9px;border-radius:50%;background:${c};border:1.5px solid #fff;box-shadow:0 0 0 1px ${c}40;"></span>'
      + '<strong style="font-size:13px;">Flst. ${p.parcelNumber}</strong>'
      + '<span style="font-size:9px;font-weight:700;padding:1px 7px;border-radius:4px;background:${c}10;color:${c};margin-left:auto;">${lb}</span></div>'
      + '<div style="font-size:11px;color:#475569;">'
      + '<div><b style="color:#1e293b;">Gemarkung</b> ${p.gemarkung}, Flur ${p.flur}</div>'
      + '<div><b style="color:#1e293b;">Owner</b> ${p.owner}</div>'
      + '<div><b style="color:#1e293b;">Area</b> ${p.area} ha</div>'
      ${p.linkedWEA ? `+ '<div><b style="color:#1e293b;">Turbine</b> ${p.linkedWEA}</div>'` : ""}
      ${p.contractRef ? `+ '<div><b style="color:#1e293b;">Contract</b> ${p.contractRef}</div>'` : ""}
      ${p.notes ? `+ '<div style="font-size:10px;color:#94a3b8;margin-top:5px;padding-top:5px;border-top:1px solid #f1f5f9;font-style:italic;">${p.notes}</div>'` : ""}
      + '</div></div>'
    );
    // Corner label
    L.marker([${tl[0]},${tl[1]}], { interactive:false, icon: L.divIcon({ className:'', iconSize:[0,0], iconAnchor:[-4,14],
      html:'<div style="font:800 11px/1 system-ui;color:${c};white-space:nowrap;pointer-events:none;text-shadow:0 0 3px #fff,0 0 3px #fff,0 0 6px #fff,0 0 6px #fff,1px 1px 2px rgba(0,0,0,.15);">${p.parcelNumber}</div>'
    })}).addTo(parcelGroup);
  })();`;
    })
    .join("\n  ")}

  // Small WEA dots in parcel view
  ${d.weaStatuses
    .map((w) => {
      const c = AMPEL_HEX[w.ampel];
      return `L.marker([${w.lat},${w.lng}], { icon: L.divIcon({ className:'', iconSize:[12,12], iconAnchor:[6,6],
    html:'<div style="width:12px;height:12px;background:${c};border:1.5px solid #fff;border-radius:50%;box-shadow:0 1px 3px rgba(0,0,0,.25);opacity:.8;"></div>'
  })}).addTo(parcelGroup).bindTooltip('${w.name}', { direction:'top', offset:[0,-8] });`;
    })
    .join("\n  ")}
  `
      : ""
  }

  // ── Legend ──
  var legendCtrl = L.control({ position: 'bottomright' });
  var currentLegend = null;
  function buildLegend(mode) {
    if (currentLegend) map.removeControl(currentLegend);
    currentLegend = L.control({ position: 'bottomright' });
    currentLegend.onAdd = function() {
      var d = L.DomUtil.create('div');
      var dot = function(c) { return '<span style="width:8px;height:8px;border-radius:50%;background:'+c+';flex-shrink:0;"></span>'; };
      var sw = function(c,ds) { return '<span style="width:16px;height:8px;border-radius:2px;border:1.5px '+(ds?'dashed':'solid')+' '+c+';background:'+c+'20;flex-shrink:0;"></span>'; };
      var row = function(i,t) { return '<div style="display:flex;align-items:center;gap:7px;padding:1px 0;">'+i+'<span>'+t+'</span></div>'; };
      var b = '';
      if (mode === 'turbines') {
        b += '<div style="font-weight:600;font-size:9px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;">Turbine Status</div>';
        b += row(dot('#059669'), 'Secured (${d.weaStatuses.filter((w) => w.ampel === "green").length})');
        b += row(dot('#d97706'), 'Negotiation (${d.weaStatuses.filter((w) => w.ampel === "yellow").length})');
        b += row(dot('#dc2626'), 'Open (${d.weaStatuses.filter((w) => w.ampel === "red").length})');
      } else {
        b += '<div style="font-weight:600;font-size:9px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;">Land Status</div>';
        b += row(sw('#059669'), 'Secured');
        b += row(sw('#d97706'), 'In Negotiation');
        b += row(sw('#dc2626'), 'Not Secured');
        b += row(sw('#3b82f6'), 'Buffer Zone');
        b += row(sw('#8b5cf6',true), 'Cable Easement');
      }
      b += '<div style="height:1px;background:#e2e8f0;margin:4px 0;"></div>';
      b += row('<span style="width:16px;height:0;border-top:2px dashed #6366f1;flex-shrink:0;"></span>', 'Cable Route');
      d.innerHTML = '<div style="background:rgba(255,255,255,.96);backdrop-filter:blur(8px);padding:9px 12px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.08);border:1px solid #e2e8f0;font:10px/1.6 system-ui;color:#475569;">'+b+'</div>';
      return d;
    };
    currentLegend.addTo(map);
  }
  buildLegend('turbines');

  // ── Toggle Logic ──
  ${
    hasCadast
      ? `
  window.switchView = function(mode) {
    var btnT = document.getElementById('btn-turbines');
    var btnP = document.getElementById('btn-parcels');
    if (mode === 'turbines') {
      map.removeLayer(parcelGroup);
      turbineGroup.addTo(map);
      btnT.style.background = '#fff'; btnT.style.color = '#0f172a'; btnT.style.boxShadow = '0 1px 3px rgba(0,0,0,.08)';
      btnP.style.background = 'transparent'; btnP.style.color = '#64748b'; btnP.style.boxShadow = 'none';
    } else {
      map.removeLayer(turbineGroup);
      parcelGroup.addTo(map);
      btnP.style.background = '#fff'; btnP.style.color = '#0f172a'; btnP.style.boxShadow = '0 1px 3px rgba(0,0,0,.08)';
      btnT.style.background = 'transparent'; btnT.style.color = '#64748b'; btnT.style.boxShadow = 'none';
    }
    buildLegend(mode);
  };
  `
      : ""
  }

  // Fit bounds
  var bounds = L.latLngBounds([${d.weaStatuses.map((w) => `[${w.lat},${w.lng}]`).join(",")}]);
  map.fitBounds(bounds.pad(0.15));

  // WEA tooltip style
  var style = document.createElement('style');
  style.textContent = '.plm-wea-tt{background:none!important;border:none!important;box-shadow:none!important;font:700 9.5px/1 system-ui;color:#0f172a;padding:0!important;text-shadow:0 0 4px #fff,0 0 4px #fff,0 0 8px #fff}.plm-wea-tt::before{display:none}';
  document.head.appendChild(style);
})();
<\/script>` +
          // WEA coordinates table
          `<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:12px;">` +
          `<thead><tr style="background:#f8fafc;"><th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">WEA</th><th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Lat</th><th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Lng</th><th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Address</th><th style="text-align:left;padding:6px 10px;border:1px solid #e2e8f0;">Status</th></tr></thead>` +
          `<tbody>${d.weaStatuses.map((w) => `<tr><td style="padding:6px 10px;border:1px solid #e2e8f0;font-weight:600;">${w.name}</td><td style="padding:6px 10px;border:1px solid #e2e8f0;">${w.lat.toFixed(4)}</td><td style="padding:6px 10px;border:1px solid #e2e8f0;">${w.lng.toFixed(4)}</td><td style="padding:6px 10px;border:1px solid #e2e8f0;">${w.address}</td><td style="padding:6px 10px;border:1px solid #e2e8f0;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${ac(w.ampel)};margin-right:4px;vertical-align:middle;"></span>${al(w.ampel)}</td></tr>`).join("")}</tbody></table>`
        );
      })()
    : "";

  const findH = a.includes("findings")
    ? `<h2 style="font-size:15px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">Action Items</h2>` +
      `<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="background:#f8fafc;">` +
      `<th style="width:24px;border:1px solid #e2e8f0;padding:8px;"></th><th style="text-align:left;padding:8px 12px;border:1px solid #e2e8f0;width:140px;">Domain</th><th style="text-align:left;padding:8px 12px;border:1px solid #e2e8f0;">Recommendation</th></tr></thead><tbody>` +
      d.findings
        .map(
          (f) =>
            `<tr><td style="text-align:center;padding:8px;border:1px solid #e2e8f0;"><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${ac(f.severity)};"></span></td>` +
            `<td style="padding:8px 12px;border:1px solid #e2e8f0;font-weight:500;">${f.domain}</td>` +
            `<td style="padding:8px 12px;border:1px solid #e2e8f0;">${f.text}</td></tr>`,
        )
        .join("") +
      `</tbody></table>`
    : "";

  return (
    `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>DDiQ Report – ${d.projectName}</title>` +
    `${leafletHead}` +
    `<style>@media print{body{font-size:12px}h1{font-size:18px}h2{font-size:14px}table{page-break-inside:avoid}#ddiq-map{height:360px!important}.leaflet-control-layers,.leaflet-control-zoom{display:none!important}}</style></head>` +
    `<body style="max-width:900px;margin:40px auto;padding:0 24px;font-family:system-ui,-apple-system,sans-serif;color:#1e293b;line-height:1.5;">` +
    `<div style="border-bottom:3px solid #1e293b;padding-bottom:16px;margin-bottom:32px;">` +
    `<h1 style="font-size:22px;font-weight:800;margin:0;">DDiQ Due Diligence Report</h1>` +
    `<p style="font-size:18px;font-weight:600;color:#475569;margin:4px 0 0;">${d.projectName}</p>` +
    `<div style="display:flex;gap:24px;margin-top:12px;font-size:12px;color:#64748b;">` +
    `<span>Prepared for: ${d.preparedFor}</span><span>By: ${d.preparedBy}</span><span>Date: ${d.date}</span></div></div>` +
    `${docList}${secH}${mapH}${cadastH}${locH}${findH}` +
    `<div style="margin-top:40px;padding-top:16px;border-top:2px solid #e2e8f0;font-size:11px;color:#94a3b8;">Auto-generated by LAI · DDiQ v1. Does not substitute legal review.</div>` +
    `</body></html>`
  );
}

function generateCSV(d: DDiQReportData, a: string[]): string {
  const l = ["Section,Category,Value,Status,Latitude,Longitude"];
  d.sections
    .filter((s) => a.includes(s.id))
    .forEach((s) =>
      s.rows.forEach((r) =>
        l.push(
          `"${s.title}","${r.label}","${r.value.replace(/"/g, '""')}","${r.ampel || ""}","",""`,
        ),
      ),
    );
  if (a.includes("statusmap"))
    d.weaStatuses.forEach((w) =>
      l.push(
        `"Status Map","${w.name}","Owner: ${w.owner} | Parcel: ${w.parcel} | Contract: ${w.contract}","${w.ampel}","${w.lat}","${w.lng}"`,
      ),
    );
  if (a.includes("locationmap"))
    d.weaStatuses.forEach((w) =>
      l.push(
        `"Location Map","${w.name}","${w.address}","${w.ampel}","${w.lat}","${w.lng}"`,
      ),
    );
  if (a.includes("cadastralmap"))
    d.parcels.forEach((p) =>
      l.push(
        `"Cadastral Parcel","Flst. ${p.parcelNumber}","${p.gemarkung} Flur ${p.flur} | ${p.owner} | ${p.area} ha | ${p.contractRef || "No contract"}","${p.status}","",""`,
      ),
    );
  if (a.includes("findings"))
    d.findings.forEach((f) =>
      l.push(
        `"Action Items","${f.domain}","${f.text.replace(/"/g, '""')}","${f.severity}","",""`,
      ),
    );
  return l.join("\n");
}

function generateTXT(d: DDiQReportData, a: string[]): string {
  const l = [
    "=".repeat(72),
    `  DDiQ Due Diligence Report`,
    `  ${d.projectName}`,
    "=".repeat(72),
    "",
    `  For: ${d.preparedFor}`,
    `  By: ${d.preparedBy}`,
    `  Date: ${d.date}`,
    "",
  ];
  if (d.analyzedDocuments.length) {
    l.push("  Analyzed Documents:");
    d.analyzedDocuments.forEach((n) => l.push(`    - ${n}`));
    l.push("");
  }
  d.sections
    .filter((s) => a.includes(s.id))
    .forEach((s) => {
      l.push(
        "",
        `--- ${s.title.toUpperCase()} ${"─".repeat(Math.max(0, 58 - s.title.length))}`,
        "",
      );
      s.rows.forEach((r) => {
        l.push(
          `  ${r.label.padEnd(28)} ${r.value}${r.ampel ? ` [${r.ampel.toUpperCase()}]` : ""}`,
        );
        if (r.note) l.push(`  ${"".padEnd(28)} >> ${r.note}`);
      });
    });
  if (a.includes("statusmap")) {
    l.push(
      "",
      "--- STATUS MAP ────────────────────────────────────────────────────",
      "",
    );
    d.weaStatuses.forEach((w) =>
      l.push(
        `  [${w.ampel.toUpperCase().padEnd(6)}] ${w.name}  |  ${w.owner}  |  ${w.parcel}  |  ${w.contract}`,
      ),
    );
  }
  if (a.includes("locationmap")) {
    l.push(
      "",
      "--- LOCATION MAP (COORDINATES) ────────────────────────────────────",
      "",
    );
    d.weaStatuses.forEach((w) =>
      l.push(
        `  ${w.name.padEnd(8)} ${w.lat.toFixed(4)}°N, ${w.lng.toFixed(4)}°E  |  ${w.address}  [${w.ampel.toUpperCase()}]`,
      ),
    );
    l.push("", "  Infrastructure:");
    d.infrastructure.forEach((p) =>
      l.push(
        `  ${p.name.padEnd(28)} ${p.lat.toFixed(4)}°N, ${p.lng.toFixed(4)}°E`,
      ),
    );
  }
  if (a.includes("cadastralmap") && d.parcels.length > 0) {
    const statusLabel: Record<string, string> = {
      secured: "SECURED",
      negotiation: "NEGOTIATION",
      open: "OPEN",
      buffer: "BUFFER",
      easement: "EASEMENT",
    };
    l.push(
      "",
      "--- CADASTRAL PARCELS (FLURSTÜCKE) ────────────────────────────────",
      "",
    );
    d.parcels.forEach((p) => {
      l.push(
        `  [${(statusLabel[p.status] || p.status).padEnd(11)}] Flst. ${p.parcelNumber.padEnd(6)} | Gemarkung ${p.gemarkung}, Flur ${p.flur} | ${p.owner} | ${p.area} ha`,
      );
      if (p.linkedWEA)
        l.push(
          `  ${"".padEnd(16)} → ${p.linkedWEA}  Contract: ${p.contractRef || "None"}`,
        );
      if (p.notes) l.push(`  ${"".padEnd(16)} >> ${p.notes}`);
    });
    const totalArea = d.parcels.reduce((s, p) => s + p.area, 0);
    const securedArea = d.parcels
      .filter((p) => ["secured", "buffer", "easement"].includes(p.status))
      .reduce((s, p) => s + p.area, 0);
    l.push(
      "",
      `  Total: ${d.parcels.length} parcels, ${totalArea.toFixed(1)} ha, ${((securedArea / totalArea) * 100).toFixed(0)}% secured`,
    );
  }
  if (a.includes("findings")) {
    l.push(
      "",
      "--- ACTION ITEMS ──────────────────────────────────────────────────",
      "",
    );
    d.findings.forEach((f, i) =>
      l.push(
        `  ${i + 1}. [${f.severity.toUpperCase()}] ${f.domain}: ${f.text}`,
      ),
    );
  }
  l.push("", "=".repeat(72), "  LAI DDiQ v1", "=".repeat(72));
  return l.join("\n");
}

function downloadFile(content: string, filename: string, mime: string) {
  const b = new Blob([content], { type: mime });
  const u = URL.createObjectURL(b);
  const a = document.createElement("a");
  a.href = u;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  URL.revokeObjectURL(u);
  document.body.removeChild(a);
}

function downloadFormat(fmt: ExportFormat, d: DDiQReportData, a: string[]) {
  const s = `DDiQ_${d.projectName.replace(/\s+/g, "_")}_${new Date().toISOString().split("T")[0]}`;
  switch (fmt) {
    case "html":
      return downloadFile(
        generateHTML(d, a),
        `${s}.html`,
        "text/html;charset=utf-8",
      );
    case "csv":
      return downloadFile(
        generateCSV(d, a),
        `${s}.csv`,
        "text/csv;charset=utf-8",
      );
    case "txt":
      return downloadFile(
        generateTXT(d, a),
        `${s}.txt`,
        "text/plain;charset=utf-8",
      );
    case "pdf":
      return downloadFile(
        generateHTML(d, a),
        `${s}.html`,
        "text/html;charset=utf-8",
      );
    case "docx":
      return downloadFile(generateHTML(d, a), `${s}.doc`, "application/msword");
    case "xlsx":
      return downloadFile(
        generateCSV(d, a),
        `${s}.csv`,
        "text/csv;charset=utf-8",
      );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════

type Step = "select-docs" | "configure" | "preview" | "exporting";

interface Props {
  documents: DocumentItem[];
  className?: string;
}

export default function ReportDownloadPanel({
  documents: rawDocs,
  className,
}: Props) {
  // Guard: if caller passes undefined/null explicitly, default param won't catch it
  const documents = rawDocs ?? [];
  const analyzedDocs = useMemo(
    () => documents.filter((d) => d.status === "analyzed"),
    [documents],
  );

  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());
  const [docSearch, setDocSearch] = useState("");
  const [step, setStep] = useState<Step>("select-docs");
  const [selectedPreset, setSelectedPreset] = useState<ReportPreset>(
    PRESETS[0],
  );
  const [activeSections, setActiveSections] = useState<string[]>(
    PRESETS[0].sections,
  );
  const [selectedFormats, setSelectedFormats] = useState<ExportFormat[]>([
    "pdf",
  ]);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportDone, setExportDone] = useState(false);

  const selectedDocs = useMemo(
    () => documents.filter((d) => selectedDocIds.has(d.id)),
    [documents, selectedDocIds],
  );
  const filteredAnalyzed = useMemo(() => {
    if (!docSearch) return analyzedDocs;
    const q = docSearch.toLowerCase();
    return analyzedDocs.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q),
    );
  }, [analyzedDocs, docSearch]);

  const statusCfg = {
    analyzed: {
      Icon: CheckRingIcon,
      color: "text-emerald-600 dark:text-emerald-500",
      bg: "bg-emerald-500/10 dark:bg-emerald-500/20",
      label: "Analyzed",
    },
    pending: {
      Icon: SandglassIcon,
      color: "text-amber-600 dark:text-amber-500",
      bg: "bg-amber-500/10 dark:bg-amber-500/20",
      label: "Pending",
    },
    archived: {
      Icon: ArchiveIcon,
      color: "text-slate-500 dark:text-slate-400",
      bg: "bg-slate-500/10 dark:bg-slate-500/20",
      label: "Archived",
    },
  };

  const toggleDoc = (id: string) =>
    setSelectedDocIds((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const selectAll = () =>
    setSelectedDocIds(new Set(analyzedDocs.map((d) => d.id)));
  const deselectAll = () => setSelectedDocIds(new Set());
  const toggleSection = (id: string) =>
    setActiveSections((p) =>
      p.includes(id) ? p.filter((s) => s !== id) : [...p, id],
    );
  const toggleFormat = (id: ExportFormat) =>
    setSelectedFormats((p) =>
      p.includes(id)
        ? p.length > 1
          ? p.filter((f) => f !== id)
          : p
        : [...p, id],
    );
  const pickPreset = (p: ReportPreset) => {
    setSelectedPreset(p);
    setActiveSections([...p.sections]);
  };
  const resetToStart = () => {
    setStep("select-docs");
    setExportDone(false);
  };
  const getReportData = (): DDiQReportData => ({
    ...DEMO_REPORT,
    analyzedDocuments: selectedDocs.map((d) => d.name),
  });

  const doExport = () => {
    setStep("exporting");
    setExportProgress(0);
    setExportDone(false);
    let p = 0;
    const iv = setInterval(() => {
      p += Math.random() * 20 + 8;
      if (p >= 100) {
        clearInterval(iv);
        setExportProgress(100);
        setExportDone(true);
      } else setExportProgress(Math.min(p, 98));
    }, 300);
  };

  const handleDownloadAll = () => {
    const rd = getReportData();
    selectedFormats.forEach((f) => downloadFormat(f, rd, activeSections));
  };
  const handleDownloadOne = (fmt: ExportFormat) =>
    downloadFormat(fmt, getReportData(), activeSections);

  // ═══════════ STEP 1: SELECT DOCUMENTS ═══════════════════════════════════

  if (step === "select-docs")
    return (
      <div className={cn("space-y-6", className)}>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">DDiQ Report Builder</h2>
            <p className="text-sm text-muted-foreground">
              Select documents for due diligence analysis, then configure your
              report
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="bg-card/50 backdrop-blur border-border/50">
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">
                    Total Uploaded
                  </p>
                  <p className="text-2xl font-bold mt-2">{documents.length}</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    From Documents page & Chat
                  </p>
                </div>
                <div className="p-2.5 rounded-md bg-slate-100 dark:bg-slate-800">
                  <ManuscriptIcon className="w-5 h-5 text-slate-600 dark:text-slate-400" />
                </div>
              </div>
            </CardContent>
          </Card>
          <Card className="bg-card/50 backdrop-blur border-border/50 border-l-4 border-l-emerald-500/50">
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">
                    Ready for Report
                  </p>
                  <p className="text-2xl font-bold mt-2 text-emerald-600 dark:text-emerald-500">
                    {analyzedDocs.length}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Analyzed documents
                  </p>
                </div>
                <div className="p-2.5 rounded-md bg-emerald-500/10">
                  <CheckRingIcon className="w-5 h-5 text-emerald-600 dark:text-emerald-500" />
                </div>
              </div>
            </CardContent>
          </Card>
          <Card className="bg-card/50 backdrop-blur border-border/50 border-l-4 border-l-blue-500/50">
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Selected</p>
                  <p className="text-2xl font-bold mt-2 text-blue-600 dark:text-blue-500">
                    {selectedDocIds.size}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    For this report
                  </p>
                </div>
                <div className="p-2.5 rounded-md bg-blue-500/10">
                  <DownloadIcon className="w-5 h-5 text-blue-600 dark:text-blue-500" />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold">
                Select Documents for Analysis
              </CardTitle>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={selectAll}
                  className="text-xs h-7"
                >
                  Select All
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={deselectAll}
                  className="text-xs h-7"
                >
                  Clear
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="relative mb-3">
              <SearchIcon className="absolute left-3 top-3 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search analyzed documents..."
                className="pl-10 h-9 text-sm"
                value={docSearch}
                onChange={(e) => setDocSearch(e.target.value)}
              />
            </div>

            {analyzedDocs.length === 0 ? (
              <div className="text-center py-8">
                <ManuscriptIcon className="w-10 h-10 text-muted-foreground mx-auto mb-3 opacity-40" />
                <p className="text-sm text-muted-foreground">
                  No analyzed documents available
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Upload and analyze documents on the Documents page first
                </p>
              </div>
            ) : (
              <div className="space-y-1.5">
                {filteredAnalyzed.map((doc) => {
                  const isSelected = selectedDocIds.has(doc.id);
                  return (
                    <div
                      key={doc.id}
                      onClick={() => toggleDoc(doc.id)}
                      className={cn(
                        "flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all",
                        isSelected
                          ? "border-primary/40 bg-primary/5"
                          : "border-transparent hover:bg-muted/40",
                      )}
                    >
                      <div
                        className={cn(
                          "w-5 h-5 rounded border flex items-center justify-center flex-shrink-0 transition-colors",
                          isSelected
                            ? "bg-primary border-primary"
                            : "border-border",
                        )}
                      >
                        {isSelected && (
                          <CheckIcon className="w-3 h-3 text-primary-foreground" />
                        )}
                      </div>
                      <div className="p-1.5 rounded-md bg-emerald-500/10">
                        <CheckRingIcon className="w-4 h-4 text-emerald-600 dark:text-emerald-500" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p
                          className={cn(
                            "text-sm font-medium truncate",
                            !isSelected && "text-muted-foreground",
                          )}
                        >
                          {doc.name}
                        </p>
                        <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                          <span>{doc.size.toFixed(1)} MB</span>
                          <span>{doc.uploadDate}</span>
                          <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary text-[10px]">
                            {doc.category}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {documents.filter((d) => d.status !== "analyzed").length > 0 && (
              <>
                <Separator className="my-4" />
                <p className="text-xs text-muted-foreground mb-2">
                  Not available for report (pending or archived):
                </p>
                <div className="space-y-1 opacity-50">
                  {documents
                    .filter((d) => d.status !== "analyzed")
                    .map((doc) => {
                      const sc = statusCfg[doc.status];
                      return (
                        <div
                          key={doc.id}
                          className="flex items-center gap-3 p-2.5 rounded-lg"
                        >
                          <div className="w-5 h-5 rounded border border-border flex items-center justify-center flex-shrink-0" />
                          <div className={cn("p-1.5 rounded-md", sc.bg)}>
                            <sc.Icon className={cn("w-4 h-4", sc.color)} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-muted-foreground truncate">
                              {doc.name}
                            </p>
                            <span
                              className={cn(
                                "text-[10px] font-medium px-1.5 py-0.5 rounded",
                                sc.bg,
                                sc.color,
                              )}
                            >
                              {sc.label}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <Button
            onClick={() => setStep("configure")}
            disabled={selectedDocIds.size === 0}
            className="shadow-sm"
          >
            Continue to Configure <ArrowRightIcon className="w-4 h-4 ml-2" />
          </Button>
        </div>
      </div>
    );

  // ═══════════ STEP 2: CONFIGURE ══════════════════════════════════════════

  if (step === "configure")
    return (
      <div className={cn("space-y-6", className)}>
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setStep("select-docs")}
            className="text-xs h-7 px-2"
          >
            ← Back
          </Button>
          <div>
            <h2 className="text-lg font-semibold">Configure Report</h2>
            <p className="text-sm text-muted-foreground">
              {selectedDocIds.size} document
              {selectedDocIds.size !== 1 ? "s" : ""} selected:{" "}
              {selectedDocs
                .map((d) => d.name)
                .slice(0, 2)
                .join(", ")}
              {selectedDocs.length > 2
                ? ` +${selectedDocs.length - 2} more`
                : ""}
            </p>
          </div>
        </div>

        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">
              Report Template
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              {PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => pickPreset(p)}
                  className={cn(
                    "text-left p-3 rounded-lg border transition-all",
                    selectedPreset.id === p.id
                      ? "border-primary bg-primary/5"
                      : "border-border/50 hover:bg-muted/40",
                  )}
                >
                  <div className="flex items-center gap-2 mb-1">
                    {selectedPreset.id === p.id && (
                      <CheckIcon className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                    )}
                    <span className="text-sm font-semibold">{p.name}</span>
                  </div>
                  <p className="text-[11px] text-muted-foreground leading-relaxed">
                    {p.description}
                  </p>
                  <Badge variant="outline" className="text-[9px] mt-2">
                    ~{p.estimatedPages} pages
                  </Badge>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">
              Customize Sections ({activeSections.length}/{SECTION_META.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {SECTION_META.map((sm) => {
              const active = activeSections.includes(sm.id);
              return (
                <div
                  key={sm.id}
                  onClick={() => toggleSection(sm.id)}
                  className={cn(
                    "flex items-center gap-3 p-3 rounded-md border cursor-pointer transition-all",
                    active
                      ? "border-primary/30 bg-primary/5"
                      : "border-transparent hover:bg-muted/40",
                  )}
                >
                  <div
                    className={cn(
                      "w-5 h-5 rounded border flex items-center justify-center flex-shrink-0",
                      active ? "bg-primary border-primary" : "border-border",
                    )}
                  >
                    {active && (
                      <CheckIcon className="w-3 h-3 text-primary-foreground" />
                    )}
                  </div>
                  <div className="flex-1">
                    <p
                      className={cn(
                        "text-sm font-medium",
                        !active && "text-muted-foreground",
                      )}
                    >
                      {sm.label}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {sm.desc}
                    </p>
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card className="bg-card/50 backdrop-blur border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">
              Export Formats
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {FORMAT_OPTIONS.map((fmt) => {
                const active = selectedFormats.includes(fmt.id);
                return (
                  <div
                    key={fmt.id}
                    onClick={() => toggleFormat(fmt.id)}
                    className={cn(
                      "flex items-center gap-3 p-3 rounded-md border cursor-pointer transition-all",
                      active
                        ? `${fmt.colorCls} border`
                        : "border-border/50 hover:bg-muted/40",
                    )}
                  >
                    <div
                      className={cn(
                        "w-4 h-4 rounded border flex items-center justify-center flex-shrink-0",
                        active ? "bg-primary border-primary" : "border-border",
                      )}
                    >
                      {active && (
                        <CheckIcon className="w-3 h-3 text-primary-foreground" />
                      )}
                    </div>
                    <div>
                      <p
                        className={cn(
                          "text-sm font-semibold",
                          !active && "text-muted-foreground",
                        )}
                      >
                        .{fmt.label}
                      </p>
                      <p className="text-[10px] text-muted-foreground">
                        {fmt.description}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-between">
          <Button variant="outline" onClick={() => setStep("select-docs")}>
            Back
          </Button>
          <Button
            onClick={() => setStep("preview")}
            disabled={activeSections.length === 0}
            className="shadow-sm"
          >
            <LensIcon className="w-4 h-4 mr-2" />
            Preview Report
          </Button>
        </div>
      </div>
    );

  // ═══════════ STEP 3: PREVIEW ════════════════════════════════════════════

  if (step === "preview") {
    const rd = getReportData();
    const visSec = rd.sections.filter((s) => activeSections.includes(s.id));
    return (
      <div className={cn("space-y-6", className)}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setStep("configure")}
              className="text-xs h-7 px-2"
            >
              ← Configure
            </Button>
            <div>
              <h2 className="text-lg font-semibold">Report Preview</h2>
              <p className="text-sm text-muted-foreground">
                {selectedPreset.name} · {selectedDocIds.size} document
                {selectedDocIds.size !== 1 ? "s" : ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex gap-1 mr-2">
              {selectedFormats.map((f) => {
                const fo = FORMAT_OPTIONS.find((x) => x.id === f);
                return (
                  <span
                    key={f}
                    className={cn(
                      "px-2 py-0.5 rounded text-[10px] font-semibold border",
                      fo?.colorCls,
                    )}
                  >
                    .{f.toUpperCase()}
                  </span>
                );
              })}
            </div>
            <Button onClick={doExport} className="shadow-sm">
              <DownloadIcon className="w-4 h-4 mr-2" />
              Export Report
            </Button>
          </div>
        </div>

        <div className="rounded-lg border border-border/60 p-6 bg-card">
          <div className="border-b-2 border-foreground pb-4 mb-4">
            <h1 className="text-xl font-bold">DDiQ Due Diligence Report</h1>
            <p className="text-lg font-semibold text-muted-foreground mt-1">
              {rd.projectName}
            </p>
            <div className="flex items-center gap-6 mt-3 text-xs text-muted-foreground">
              <span>For: {rd.preparedFor}</span>
              <span>By: {rd.preparedBy}</span>
              <span>Date: {rd.date}</span>
            </div>
          </div>

          {rd.analyzedDocuments.length > 0 && (
            <div className="mb-6 p-3 rounded-lg bg-muted/30 border border-border/30">
              <h4 className="text-xs font-semibold text-muted-foreground mb-2">
                Analyzed Documents ({rd.analyzedDocuments.length})
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {rd.analyzedDocuments.map((n) => (
                  <span
                    key={n}
                    className="text-xs px-2 py-1 rounded bg-primary/10 text-primary font-medium"
                  >
                    {n}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-6">
            {visSec.map((sec) => (
              <AusgabeblattTable key={sec.id} section={sec} />
            ))}
            {activeSections.includes("statusmap") && (
              <StatusMap statuses={rd.weaStatuses} />
            )}
            {activeSections.includes("cadastralmap") &&
              rd.parcels.length > 0 && <CadastralTable parcels={rd.parcels} />}
            {activeSections.includes("locationmap") && (
              <ProjectLocationMap
                statuses={rd.weaStatuses}
                infrastructure={rd.infrastructure}
                parcels={
                  activeSections.includes("cadastralmap") ? rd.parcels : []
                }
                projectName={rd.projectName}
              />
            )}
            {activeSections.includes("findings") && (
              <FindingsTable findings={rd.findings} />
            )}
          </div>
          <div className="mt-8 pt-4 border-t border-border/40 text-[11px] text-muted-foreground">
            Auto-generated by LAI · DDiQ v1. Does not substitute legal review.
          </div>
        </div>
      </div>
    );
  }

  // ═══════════ STEP 4: EXPORTING ══════════════════════════════════════════

  return (
    <div className={cn("space-y-6", className)}>
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setStep("preview")}
          disabled={!exportDone}
          className="text-xs h-7 px-2"
        >
          ← Preview
        </Button>
        <h2 className="text-lg font-semibold">
          {exportDone ? "Report Ready" : "Generating Report..."}
        </h2>
      </div>
      <Card className="bg-card/50 backdrop-blur border-border/50">
        <CardContent className="py-12">
          <div className="max-w-md mx-auto text-center space-y-6">
            {!exportDone ? (
              <>
                <div className="w-16 h-16 mx-auto rounded-full bg-primary/10 flex items-center justify-center">
                  <SandglassIcon className="w-8 h-8 text-primary animate-pulse" />
                </div>
                <div>
                  <h3 className="text-base font-semibold">
                    Generating DDiQ report...
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    {selectedPreset.name} · {activeSections.length} sections ·{" "}
                    {selectedDocIds.size} documents
                  </p>
                </div>
                <div>
                  <div className="flex justify-between text-xs text-muted-foreground mb-1.5">
                    <span>Progress</span>
                    <span>{Math.round(exportProgress)}%</span>
                  </div>
                  <Progress value={exportProgress} className="h-2" />
                </div>
              </>
            ) : (
              <>
                <div className="w-16 h-16 mx-auto rounded-full bg-emerald-500/10 flex items-center justify-center">
                  <CheckRingIcon className="w-8 h-8 text-emerald-600 dark:text-emerald-500" />
                </div>
                <div>
                  <h3 className="text-base font-semibold">
                    Report generated successfully
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    {DEMO_REPORT.projectName} — {selectedPreset.name}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {selectedDocIds.size} documents · {activeSections.length}{" "}
                    sections · {DEMO_REPORT.findings.length} action items
                  </p>
                </div>
                {selectedFormats.length > 1 && (
                  <Button onClick={handleDownloadAll} className="shadow-sm">
                    <DownloadIcon className="w-4 h-4 mr-2" />
                    Download All ({selectedFormats.length} files)
                  </Button>
                )}
                <div className="flex justify-center gap-2 flex-wrap">
                  {selectedFormats.map((fmt) => {
                    const fo = FORMAT_OPTIONS.find((x) => x.id === fmt)!;
                    return (
                      <button
                        key={fmt}
                        onClick={() => handleDownloadOne(fmt)}
                        className={cn(
                          "inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-semibold border cursor-pointer hover:opacity-80 transition-opacity",
                          fo.colorCls,
                        )}
                      >
                        <DownloadIcon className="w-3.5 h-3.5" />.
                        {fmt.toUpperCase()}
                      </button>
                    );
                  })}
                </div>
                <div className="flex justify-center gap-3 pt-2">
                  <Button variant="outline" size="sm" onClick={resetToStart}>
                    Back to Overview
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setStep("configure")}
                  >
                    Generate Another
                  </Button>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
