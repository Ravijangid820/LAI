"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import {
  MapContainer,
  TileLayer,
  Polyline,
  Polygon,
  Popup,
  Marker,
  LayersControl,
  useMap,
  Tooltip as LeafletTooltip,
} from "react-leaflet";
import L from "leaflet";
import { cn } from "@/react-app/lib/utils";
import type {
  Ampel,
  WEAStatus,
  InfraPoint,
  CadastralParcel,
  ParcelStatus,
} from "@/react-app/lib/ddiqDemoData";
import "leaflet/dist/leaflet.css";

// ─── View Mode ──────────────────────────────────────────────────────────────

type ViewMode = "turbines" | "parcels";

// ─── Design Tokens ──────────────────────────────────────────────────────────

const AMPEL: Record<Ampel, { hex: string; label: string; tw: string }> = {
  green: { hex: "#059669", label: "Secured", tw: "bg-emerald-500" },
  yellow: { hex: "#d97706", label: "Partial", tw: "bg-amber-500" },
  red: { hex: "#dc2626", label: "Open", tw: "bg-rose-500" },
};

const PARCEL_STYLE: Record<ParcelStatus, { color: string; label: string }> = {
  secured: { color: "#059669", label: "Secured" },
  negotiation: { color: "#d97706", label: "In Negotiation" },
  open: { color: "#dc2626", label: "Not Secured" },
  buffer: { color: "#3b82f6", label: "Buffer Zone" },
  easement: { color: "#8b5cf6", label: "Cable Easement" },
};

// ─── Icon Factories ─────────────────────────────────────────────────────────

// Full WEA marker (turbines view)
function weaIcon(ampel: Ampel, num: string): L.DivIcon {
  const c = AMPEL[ampel].hex;
  return L.divIcon({
    className: "",
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -17],
    html: `<div style="width:30px;height:30px;position:relative;">
      <div style="position:absolute;inset:0;background:${c};border:2.5px solid #fff;border-radius:50%;
        box-shadow:0 2px 8px ${c}55;display:flex;align-items:center;justify-content:center;">
        <span style="color:#fff;font-size:11px;font-weight:800;font-family:system-ui;">${num}</span>
      </div>
    </div>`,
  });
}

// Small WEA dot (parcels view — just a position reference)
function weaDotIcon(ampel: Ampel): L.DivIcon {
  const c = AMPEL[ampel].hex;
  return L.divIcon({
    className: "",
    iconSize: [12, 12],
    iconAnchor: [6, 6],
    popupAnchor: [0, -8],
    html: `<div style="width:12px;height:12px;background:${c};border:1.5px solid #fff;border-radius:50%;
      box-shadow:0 1px 3px rgba(0,0,0,.25);opacity:.8;"></div>`,
  });
}

function infraIcon(type: InfraPoint["type"]): L.DivIcon {
  const map: Record<InfraPoint["type"], { ch: string; bg: string }> = {
    substation: { ch: "⚡", bg: "#6366f1" },
    cable_start: { ch: "·", bg: "#8b5cf6" },
    cable_end: { ch: "⚡", bg: "#6366f1" },
    access_road: { ch: "🛤", bg: "#64748b" },
  };
  const { ch, bg } = map[type];
  return L.divIcon({
    className: "",
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -14],
    html: `<div style="width:26px;height:26px;background:${bg}15;border:1.5px solid ${bg};border-radius:5px;
      display:flex;align-items:center;justify-content:center;font-size:12px;
      box-shadow:0 1px 4px rgba(0,0,0,0.12);">${ch}</div>`,
  });
}

// Parcel label — pinned to top-left corner of polygon
function topLeftCorner(poly: [number, number][]): [number, number] {
  let best = poly[0];
  for (const pt of poly) {
    if (pt[0] > best[0] || (pt[0] === best[0] && pt[1] < best[1])) best = pt;
  }
  return best;
}

function parcelLabelIcon(num: string, color: string): L.DivIcon {
  return L.divIcon({
    className: "",
    iconSize: [0, 0],
    iconAnchor: [-4, 14],
    html: `<div style="
      font:800 11px/1 system-ui; color:${color};
      white-space:nowrap; pointer-events:none;
      text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 6px #fff, 0 0 6px #fff,
                   1px 1px 2px rgba(0,0,0,.15);
    ">${num}</div>`,
  });
}

// ─── Map Helpers ────────────────────────────────────────────────────────────

function FitBounds({ points }: { points: L.LatLngExpression[] }) {
  const map = useMap();
  const done = useRef(false);
  useEffect(() => {
    if (done.current || points.length === 0) return;
    map.fitBounds(L.latLngBounds(points), { padding: [50, 50], maxZoom: 15 });
    done.current = true;
  }, [map, points]);
  return null;
}

function ScrollControl({ on }: { on: boolean }) {
  const map = useMap();
  useEffect(() => {
    on ? map.scrollWheelZoom.enable() : map.scrollWheelZoom.disable();
  }, [map, on]);
  return null;
}

function SetupControls() {
  const map = useMap();
  useEffect(() => {
    map.attributionControl.setPrefix(false);
    const z = L.control.zoom({ position: "bottomleft" });
    z.addTo(map);
    return () => {
      z.remove();
    };
  }, [map]);
  return null;
}

// ─── Legend (adapts to active view) ─────────────────────────────────────────

function MapLegend({
  statuses,
  mode,
}: {
  statuses: WEAStatus[];
  mode: ViewMode;
}) {
  const map = useMap();
  useEffect(() => {
    const ctrl = new L.Control({ position: "bottomright" });
    ctrl.onAdd = () => {
      const el = L.DomUtil.create("div");
      const dot = (c: string) =>
        `<span style="width:8px;height:8px;border-radius:50%;background:${c};flex-shrink:0;"></span>`;
      const swatch = (c: string, dashed = false) =>
        `<span style="width:16px;height:8px;border-radius:2px;border:1.5px ${dashed ? "dashed" : "solid"} ${c};background:${c}20;flex-shrink:0;"></span>`;
      const row = (icon: string, text: string) =>
        `<div style="display:flex;align-items:center;gap:7px;padding:1px 0;">${icon}<span>${text}</span></div>`;

      let body = "";

      if (mode === "turbines") {
        const g = statuses.filter((s) => s.ampel === "green").length;
        const y = statuses.filter((s) => s.ampel === "yellow").length;
        const r = statuses.filter((s) => s.ampel === "red").length;
        body += `<div style="font-weight:600;font-size:9px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;">Turbine Status</div>`;
        body += row(dot("#059669"), `Secured (${g})`);
        body += row(dot("#d97706"), `Negotiation (${y})`);
        body += row(dot("#dc2626"), `Open (${r})`);
      } else {
        body += `<div style="font-weight:600;font-size:9px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;">Land Status</div>`;
        body += row(swatch("#059669"), "Secured");
        body += row(swatch("#d97706"), "In Negotiation");
        body += row(swatch("#dc2626"), "Not Secured");
        body += row(swatch("#3b82f6"), "Buffer Zone");
        body += row(swatch("#8b5cf6", true), "Cable Easement");
      }

      body += `<div style="height:1px;background:#e2e8f0;margin:4px 0;"></div>`;
      body += row(
        `<span style="width:16px;height:0;border-top:2px dashed #6366f1;flex-shrink:0;"></span>`,
        "Cable Route",
      );

      el.innerHTML = `<div style="background:rgba(255,255,255,.96);backdrop-filter:blur(8px);
        padding:9px 12px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.08);
        border:1px solid #e2e8f0;font:10px/1.6 system-ui;color:#475569;">${body}</div>`;
      return el;
    };
    ctrl.addTo(map);
    return () => {
      ctrl.remove();
    };
  }, [map, statuses, mode]);
  return null;
}

// ─── Shared Popup Styles ────────────────────────────────────────────────────

const POP: React.CSSProperties = {
  fontFamily: "system-ui",
  fontSize: 12,
  lineHeight: 1.55,
  minWidth: 210,
  padding: "10px 12px",
};
const LABEL_S: React.CSSProperties = {
  fontWeight: 600,
  color: "#1e293b",
  marginRight: 4,
};
const META_S: React.CSSProperties = {
  fontSize: 10,
  color: "#94a3b8",
  marginTop: 5,
  paddingTop: 5,
  borderTop: "1px solid #f1f5f9",
};

function PopupHead({
  color,
  title,
  badge,
}: {
  color: string;
  title: string;
  badge: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        paddingBottom: 6,
        marginBottom: 6,
        borderBottom: "1px solid #f1f5f9",
      }}
    >
      <span
        style={{
          width: 9,
          height: 9,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          border: "1.5px solid #fff",
          boxShadow: `0 0 0 1px ${color}40`,
        }}
      />
      <strong style={{ fontSize: 13, color: "#0f172a" }}>{title}</strong>
      <span
        style={{
          fontSize: 9,
          fontWeight: 700,
          padding: "1px 7px",
          borderRadius: 4,
          background: `${color}10`,
          color,
          marginLeft: "auto",
          whiteSpace: "nowrap",
        }}
      >
        {badge}
      </span>
    </div>
  );
}

function PopupRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ fontSize: 11, color: "#475569", padding: "1px 0" }}>
      <span style={LABEL_S}>{label}</span>
      {value}
    </div>
  );
}

// ─── Tiles ──────────────────────────────────────────────────────────────────

const TILES = {
  street: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    a: "© OpenStreetMap",
  },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    a: "© Esri",
  },
  topo: {
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    a: "© OpenTopoMap",
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════

interface Props {
  statuses: WEAStatus[];
  infrastructure: InfraPoint[];
  parcels?: CadastralParcel[];
  projectName: string;
  className?: string;
}

export default function ProjectLocationMap({
  statuses,
  infrastructure,
  parcels = [],
  projectName,
  className,
}: Props) {
  const [active, setActive] = useState(false);
  const [mode, setMode] = useState<ViewMode>("turbines");

  const center: [number, number] = useMemo(
    () => [
      statuses.reduce((s, w) => s + w.lat, 0) / (statuses.length || 1),
      statuses.reduce((s, w) => s + w.lng, 0) / (statuses.length || 1),
    ],
    [statuses],
  );

  const cStart = infrastructure.find((p) => p.type === "cable_start");
  const cEnd = infrastructure.find((p) => p.type === "cable_end");
  const cable: [number, number][] =
    cStart && cEnd
      ? [
          [cStart.lat, cStart.lng],
          [cEnd.lat, cEnd.lng],
        ]
      : [];

  const allPts = useMemo<L.LatLngExpression[]>(
    () => [
      ...statuses.map((w) => [w.lat, w.lng] as L.LatLngExpression),
      ...infrastructure.map((p) => [p.lat, p.lng] as L.LatLngExpression),
      ...(mode === "parcels"
        ? parcels.flatMap((p) => p.polygon as L.LatLngExpression[])
        : []),
    ],
    [statuses, infrastructure, parcels, mode],
  );

  const parcelStats = useMemo(() => {
    const total = parcels.reduce((s, p) => s + p.area, 0);
    const secured = parcels
      .filter((p) => ["secured", "buffer", "easement"].includes(p.status))
      .reduce((s, p) => s + p.area, 0);
    return {
      count: parcels.length,
      total,
      secured,
      pct: total > 0 ? Math.round((secured / total) * 100) : 0,
    };
  }, [parcels]);

  return (
    <div
      className={cn(
        "rounded-lg border border-border/60 overflow-hidden",
        className,
      )}
    >
      {/* ── Header ── */}
      <div className="bg-muted/30 dark:bg-muted/10 px-4 py-2.5 border-b border-border/40 flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold truncate">{projectName}</h4>

        {/* View toggle */}
        {parcels.length > 0 && (
          <div className="flex items-center bg-muted/50 dark:bg-muted/30 rounded-md p-0.5 border border-border/40">
            <button
              onClick={() => setMode("turbines")}
              className={cn(
                "text-[10px] font-medium px-3 py-1 rounded-[5px] transition-all",
                mode === "turbines"
                  ? "bg-background dark:bg-muted text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Turbines
            </button>
            <button
              onClick={() => setMode("parcels")}
              className={cn(
                "text-[10px] font-medium px-3 py-1 rounded-[5px] transition-all",
                mode === "parcels"
                  ? "bg-background dark:bg-muted text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Parcels
            </button>
          </div>
        )}
      </div>

      {/* ── Map ── */}
      <div
        className="relative"
        onClick={() => setActive(true)}
        onMouseLeave={() => setActive(false)}
      >
        <style>{`
          .plm .leaflet-control-attribution{font-size:9px;opacity:0.35;transition:opacity .2s}
          .plm .leaflet-control-attribution:hover{opacity:0.8}
          .plm .leaflet-control-attribution a{color:#64748b!important}
          .plm .leaflet-popup-content-wrapper{border-radius:10px;box-shadow:0 4px 20px rgba(0,0,0,.1);border:1px solid #e2e8f0}
          .plm .leaflet-popup-content{margin:0;padding:0}
          .plm .leaflet-popup-close-button{font-size:18px;color:#cbd5e1;top:6px;right:8px;width:20px;height:20px}
          .plm .leaflet-popup-close-button:hover{color:#64748b}
          .plm .leaflet-popup-tip{border-top-color:#fff;box-shadow:0 2px 4px rgba(0,0,0,.06)}
          .plm .leaflet-control-layers{border-radius:8px;border:1px solid #e2e8f0;box-shadow:0 1px 8px rgba(0,0,0,.06);overflow:hidden}
          .plm .leaflet-control-layers-toggle{width:30px;height:30px;background-size:16px}
          .plm .leaflet-control-zoom{border-radius:8px;border:1px solid #e2e8f0;box-shadow:0 1px 8px rgba(0,0,0,.06);overflow:hidden}
          .plm .leaflet-control-zoom a{width:30px;height:30px;line-height:30px;font-size:14px;color:#475569}
          .plm .leaflet-control-zoom a:hover{background:#f8fafc}
          .plm .plm-wea-label{background:none!important;border:none!important;box-shadow:none!important;
            font:700 9.5px/1 system-ui;color:#0f172a;padding:0!important;
            text-shadow:0 0 4px #fff,0 0 4px #fff,0 0 8px #fff}
          .plm .plm-wea-label::before{display:none}
        `}</style>

        <div className="plm">
          <MapContainer
            center={center}
            zoom={14}
            scrollWheelZoom={false}
            zoomControl={false}
            style={{ height: "460px", width: "100%" }}
          >
            <SetupControls />
            <ScrollControl on={active} />
            <FitBounds points={allPts} />
            <MapLegend statuses={statuses} mode={mode} />

            <LayersControl position="topright" collapsed>
              <LayersControl.BaseLayer checked name="Street">
                <TileLayer
                  url={TILES.street.url}
                  attribution={TILES.street.a}
                  maxZoom={19}
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Satellite">
                <TileLayer
                  url={TILES.satellite.url}
                  attribution={TILES.satellite.a}
                  maxZoom={18}
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Topographic">
                <TileLayer
                  url={TILES.topo.url}
                  attribution={TILES.topo.a}
                  maxZoom={17}
                />
              </LayersControl.BaseLayer>
            </LayersControl>

            {/* ═══ SHARED: Cable Route + Infrastructure (both views) ═══ */}

            {cable.length === 2 && (
              <Polyline
                positions={cable}
                pathOptions={{
                  color: "#6366f1",
                  weight: 2.5,
                  dashArray: "10 6",
                  opacity: 0.7,
                }}
              >
                <Popup>
                  <div style={POP}>
                    <strong>Cable Route</strong>
                    <div
                      style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}
                    >
                      4.2 km → Substation Tostedt
                    </div>
                  </div>
                </Popup>
              </Polyline>
            )}

            {infrastructure
              .filter((p) => p.type !== "cable_start")
              .map((p) => (
                <Marker
                  key={p.name}
                  position={[p.lat, p.lng]}
                  icon={infraIcon(p.type)}
                >
                  <Popup>
                    <div style={POP}>
                      <strong>{p.name}</strong>
                      <div style={META_S}>
                        {p.lat.toFixed(5)}°N, {p.lng.toFixed(5)}°E
                      </div>
                    </div>
                  </Popup>
                </Marker>
              ))}

            {/* ═══ TURBINES VIEW ═══ */}

            {mode === "turbines" &&
              statuses.map((w) => (
                <Marker
                  key={w.name}
                  position={[w.lat, w.lng]}
                  icon={weaIcon(w.ampel, w.name.replace("WEA ", ""))}
                >
                  <Popup>
                    <div style={POP}>
                      <PopupHead
                        color={AMPEL[w.ampel].hex}
                        title={w.name}
                        badge={AMPEL[w.ampel].label}
                      />
                      <PopupRow label="Owner" value={w.owner} />
                      <PopupRow label="Parcel" value={w.parcel} />
                      <PopupRow label="Address" value={w.address} />
                      <PopupRow label="Contract" value={w.contract} />
                      <div style={META_S}>
                        {w.lat.toFixed(5)}°N, {w.lng.toFixed(5)}°E
                      </div>
                    </div>
                  </Popup>
                  <LeafletTooltip
                    direction="top"
                    offset={[0, -17]}
                    permanent
                    className="plm-wea-label"
                  >
                    {w.name}
                  </LeafletTooltip>
                </Marker>
              ))}

            {/* ═══ PARCELS VIEW ═══ */}

            {mode === "parcels" && (
              <>
                {/* Parcel polygons + corner labels */}
                {parcels.map((parcel) => {
                  const ps = PARCEL_STYLE[parcel.status];
                  const isEasement = parcel.status === "easement";
                  const labelPos = topLeftCorner(parcel.polygon);
                  return (
                    <span key={parcel.id}>
                      <Polygon
                        positions={parcel.polygon}
                        pathOptions={{
                          fillColor: ps.color,
                          fillOpacity: 0.2,
                          color: ps.color,
                          weight: isEasement ? 1.5 : 2.5,
                          dashArray: isEasement ? "6 4" : undefined,
                          opacity: 0.9,
                        }}
                      >
                        <Popup>
                          <div style={POP}>
                            <PopupHead
                              color={ps.color}
                              title={`Flst. ${parcel.parcelNumber}`}
                              badge={ps.label}
                            />
                            <PopupRow
                              label="Gemarkung"
                              value={`${parcel.gemarkung}, Flur ${parcel.flur}`}
                            />
                            <PopupRow label="Owner" value={parcel.owner} />
                            <PopupRow
                              label="Area"
                              value={`${parcel.area} ha`}
                            />
                            {parcel.linkedWEA && (
                              <PopupRow
                                label="Turbine"
                                value={parcel.linkedWEA}
                              />
                            )}
                            {parcel.contractRef && (
                              <PopupRow
                                label="Contract"
                                value={parcel.contractRef}
                              />
                            )}
                            {parcel.notes && (
                              <div style={{ ...META_S, fontStyle: "italic" }}>
                                {parcel.notes}
                              </div>
                            )}
                          </div>
                        </Popup>
                      </Polygon>
                      <Marker
                        position={labelPos}
                        icon={parcelLabelIcon(parcel.parcelNumber, ps.color)}
                        interactive={false}
                      />
                    </span>
                  );
                })}

                {/* Small WEA reference dots (position only, no labels) */}
                {statuses.map((w) => (
                  <Marker
                    key={`dot-${w.name}`}
                    position={[w.lat, w.lng]}
                    icon={weaDotIcon(w.ampel)}
                  >
                    <LeafletTooltip direction="top" offset={[0, -8]}>
                      {w.name}
                    </LeafletTooltip>
                  </Marker>
                ))}
              </>
            )}
          </MapContainer>
        </div>

        {/* Scroll hint */}
        {!active && (
          <div className="absolute inset-0 z-[1000] flex items-end justify-center pb-3.5 pointer-events-none">
            <div className="bg-foreground/55 text-background text-[10px] font-medium px-3 py-1 rounded-full backdrop-blur-sm">
              Click map to zoom & pan
            </div>
          </div>
        )}
      </div>

      {/* ── Bottom Panel: contextual to active view ── */}

      {mode === "turbines" && (
        <div className="border-t border-border/40 bg-muted/10">
          <div className="px-4 py-2">
            <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">
              WEA Coordinates
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-5 gap-y-0.5">
              {statuses.map((w) => (
                <div
                  key={w.name}
                  className="flex items-center gap-1.5 text-[10.5px] py-0.5"
                >
                  <span
                    className={cn(
                      "w-1.5 h-1.5 rounded-full flex-shrink-0",
                      AMPEL[w.ampel].tw,
                    )}
                  />
                  <span className="font-semibold">{w.name}</span>
                  <span className="text-muted-foreground tabular-nums">
                    {w.lat.toFixed(4)}, {w.lng.toFixed(4)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {mode === "parcels" && parcels.length > 0 && (
        <div className="border-t border-border/40 bg-muted/10">
          <div className="px-4 py-2.5">
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                Cadastral Parcels
              </p>
              <p className="text-[10px] text-muted-foreground">
                {parcelStats.count} parcels · {parcelStats.total.toFixed(1)} ha
                ·{" "}
                <span
                  className={
                    parcelStats.pct >= 80
                      ? "text-emerald-600"
                      : parcelStats.pct >= 50
                        ? "text-amber-600"
                        : "text-rose-600"
                  }
                >
                  {parcelStats.pct}% secured
                </span>
              </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-0.5">
              {parcels.map((p) => {
                const ps = PARCEL_STYLE[p.status];
                return (
                  <div
                    key={p.id}
                    className="flex items-center gap-2 py-1 text-[11px] border-b border-border/20 last:border-0"
                  >
                    <span
                      style={{
                        width: 12,
                        height: 7,
                        borderRadius: 2,
                        background: `${ps.color}20`,
                        border: `1.5px solid ${ps.color}`,
                      }}
                      className="flex-shrink-0"
                    />
                    <span className="font-semibold min-w-[48px]">
                      {p.parcelNumber}
                    </span>
                    <span className="text-muted-foreground truncate flex-1">
                      {p.owner}
                    </span>
                    <span className="text-muted-foreground/60 text-[10px]">
                      {p.area}ha
                    </span>
                    {p.linkedWEA && (
                      <span className="text-[10px] text-muted-foreground/50">
                        →{p.linkedWEA}
                      </span>
                    )}
                    <span
                      style={{ color: ps.color }}
                      className="text-[9px] font-semibold min-w-[72px] text-right"
                    >
                      {ps.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
