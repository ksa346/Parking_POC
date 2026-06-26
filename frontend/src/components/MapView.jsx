import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useActiveLocation } from '../contexts/ActiveLocationContext';

/**
 * Interactive Parking Map — styled pins with rich info popups on click.
 */

/* Default zone coordinates on the Walmart Mechanicsburg parking lot (2×3 grid) */
const DEFAULT_ZONE_COORDS = {
  TL: { lat: 40.24879, lng: -77.02448, label: 'Top-Left',  sublabel: 'NW Wing',       address: 'NW, Walmart Supercenter' },
  TR: { lat: 40.24879, lng: -77.02343, label: 'Top-Right', sublabel: 'NE Wing',       address: 'NE, Walmart Supercenter' },
  ML: { lat: 40.24838, lng: -77.02448, label: 'Mid-Left',  sublabel: 'West Central',  address: 'W. Central, Walmart Supercenter' },
  MR: { lat: 40.24838, lng: -77.02343, label: 'Mid-Right', sublabel: 'East Central',  address: 'E. Central, Walmart Supercenter' },
  BL: { lat: 40.24796, lng: -77.02448, label: 'Bot-Left',  sublabel: 'SW Wing',       address: 'SW, Walmart Supercenter' },
  BR: { lat: 40.24796, lng: -77.02343, label: 'Bot-Right', sublabel: 'SE Wing',       address: 'SE, Walmart Supercenter' },
};

const DEFAULT_CENTER = [40.248386, -77.0239493];

/* Default lot boundary polygon */
const DEFAULT_POLYGON = [
  [40.24900, -77.02500],
  [40.24900, -77.02290],
  [40.24775, -77.02290],
  [40.24775, -77.02500],
];

/** Generate zone coordinates in a grid around a center point */
function generateZoneCoords(centerLat, centerLon, zoneIds, locationName) {
  const spread = 0.0004;
  const cols = 2;
  const rows = Math.ceil(zoneIds.length / cols);
  const coords = {};
  zoneIds.forEach((id, i) => {
    const row = Math.floor(i / cols);
    const col = i % cols;
    coords[id] = {
      lat: centerLat + (rows / 2 - row) * spread,
      lng: centerLon + (col - cols / 2 + 0.5) * spread,
      label: `Zone ${id}`,
      sublabel: id,
      address: `${id}, ${locationName}`,
    };
  });
  return coords;
}

function statusColor(pct) {
  if (pct >= 85) return '#FF4D6A';
  if (pct >= 60) return '#F7C325';
  return '#86BC25';
}

function statusLabel(pct) {
  if (pct >= 85) return 'Limited';
  if (pct >= 60) return 'Moderate';
  return 'High Avail.';
}

/* Styled badge pin like the reference screenshot */
function createZoneIcon(L, zone, meta) {
  const pct = zone.total ? Math.round((zone.occupied / zone.total) * 100) : 0;
  const color = statusColor(pct);
  const id = zone.zone_id;

  return L.divIcon({
    className: 'zone-map-pin',
    html: `
      <div class="zone-pin-badge" style="--pin-color:${color}">
        <span class="zone-pin-id">${id}</span>
        <span class="zone-pin-label">${meta.sublabel}</span>
      </div>
      <div class="zone-pin-arrow" style="border-top-color:${color}"></div>
    `,
    iconSize: [80, 52],
    iconAnchor: [40, 52],
    popupAnchor: [0, -48],
  });
}

/* Rich info popup – styled like the reference screenshot */
function buildPopupHtml(zone, meta) {
  const pct = zone.total ? Math.round((zone.occupied / zone.total) * 100) : 0;
  const free = (zone.total || 0) - (zone.occupied || 0);
  const color = statusColor(pct);
  const status = statusLabel(pct);
  const availPct = 100 - pct;

  return `
    <div class="zone-popup">
      <div class="zp-header">
        <span class="zp-badge" style="background:${color}">${zone.zone_id}</span>
        <div class="zp-title">
          <strong>${meta.label} (${meta.sublabel})</strong>
        </div>
      </div>
      <div class="zp-grid">
        <div class="zp-stat"><i class="fas fa-th-large"></i> Total: <b>${zone.total}</b></div>
        <div class="zp-stat"><i class="fas fa-door-open" style="color:#86BC25"></i> Open: <b style="color:#86BC25">${free}</b></div>
        <div class="zp-stat"><i class="fas fa-car-side" style="color:#FF4D6A"></i> Used: <b style="color:#FF4D6A">${pct}%</b></div>
        <div class="zp-stat"><i class="fas fa-signal"></i> Conf: <b>high</b></div>
      </div>
      ${zone.double_parked > 0 ? `<div class="zp-dp"><i class="fas fa-exclamation-triangle" style="color:#FF4D6A"></i> <span style="color:#FF4D6A">${zone.double_parked} double-parked</span></div>` : ''}
      <div class="zp-avail">
        <span style="color:${color};font-weight:700">${availPct}% available</span>
        <span class="zp-status" style="background:${color}20;color:${color}">${status}</span>
      </div>
      <div class="zp-bar-bg">
        <div class="zp-bar" style="width:${availPct}%;background:${color}"></div>
      </div>
      <div class="zp-footer">
        <span><i class="fas fa-wheelchair" style="color:#00D4FF"></i> ADA</span>
        <span><i class="fas fa-map-marker-alt"></i> ${meta.address}</span>
      </div>
    </div>
  `;
}

export default function MapView({ occupancy }) {
  const activeLoc = useActiveLocation();
  const mapRef = useRef(null);
  const instanceRef = useRef(null);
  const markersRef = useRef({});

  const zones = occupancy?.zones || [];
  const totalAll = zones.reduce((s, z) => s + (z.total || 0), 0);
  const occupiedAll = zones.reduce((s, z) => s + (z.occupied || 0), 0);
  const freeAll = totalAll - occupiedAll;
  const pctAll = totalAll ? Math.round((occupiedAll / totalAll) * 100) : 0;

  // Determine location name and map data
  const locName = activeLoc?.title || 'Walmart Supercenter';
  const isDefault = !activeLoc || activeLoc.id === 'mechanicsburg';
  const center = (activeLoc?.lat && activeLoc?.lon)
    ? [activeLoc.lat, activeLoc.lon]
    : DEFAULT_CENTER;

  // Build zone coordinates: use defaults for Walmart, generate for others
  const ZONE_COORDS = isDefault
    ? DEFAULT_ZONE_COORDS
    : generateZoneCoords(center[0], center[1], zones.map(z => z.zone_id), locName);

  // Lot polygon: use defaults for Walmart, generate a rectangle for others
  const LOT_POLYGON = isDefault
    ? DEFAULT_POLYGON
    : [
        [center[0] + 0.001, center[1] - 0.001],
        [center[0] + 0.001, center[1] + 0.001],
        [center[0] - 0.001, center[1] + 0.001],
        [center[0] - 0.001, center[1] - 0.001],
      ];

  useEffect(() => {
    if (instanceRef.current) {
      instanceRef.current.remove();
      instanceRef.current = null;
      markersRef.current = {};
    }

    const map = L.map(mapRef.current, {
      zoomControl: false,
      scrollWheelZoom: true,
    }).setView(center, 18);

    L.control.zoom({ position: 'topright' }).addTo(map);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
      maxZoom: 20,
    }).addTo(map);

    L.polygon(LOT_POLYGON, {
      color: '#00D4FF',
      weight: 2,
      fillColor: '#00D4FF',
      fillOpacity: 0.06,
      dashArray: '6,4',
    }).addTo(map);

    instanceRef.current = map;

    return () => {
      instanceRef.current.remove();
      instanceRef.current = null;
      markersRef.current = {};
    };
  }, [activeLoc]);

  useEffect(() => {
    const map = instanceRef.current;
    if (!map || zones.length === 0) return;

    zones.forEach((z) => {
      const meta = ZONE_COORDS[z.zone_id];
      if (!meta) return;

      const icon = createZoneIcon(L, z, meta);
      const popup = buildPopupHtml(z, meta);

      if (markersRef.current[z.zone_id]) {
        markersRef.current[z.zone_id].setIcon(icon);
        markersRef.current[z.zone_id].setPopupContent(popup);
      } else {
        const marker = L.marker([meta.lat, meta.lng], { icon })
          .addTo(map)
          .bindPopup(popup, { maxWidth: 300, className: 'zone-popup-wrapper' });
        markersRef.current[z.zone_id] = marker;
      }
    });
  }, [zones, occupancy]);

  return (
    <section className="map-section map-view-fullpage">
      {/* Compact header overlaid */}
      <div className="map-top-bar">
        <div className="map-top-left">
          <i className="fas fa-map-marked-alt"></i>
          <span>{locName} — Parking Map</span>
        </div>
        <div className="map-legend">
          <span className="legend-item"><span className="legend-dot" style={{ background: '#86BC25' }} /> High Avail.</span>
          <span className="legend-item"><span className="legend-dot" style={{ background: '#F7C325' }} /> Moderate</span>
          <span className="legend-item"><span className="legend-dot" style={{ background: '#FF4D6A' }} /> Limited</span>
        </div>
      </div>

      {/* Full-height map */}
      <div ref={mapRef} className="map-canvas" />

      {/* Compact KPI strip */}
      <div className="map-kpi-strip">
        {zones.map((z) => {
          const pct = z.total ? Math.round((z.occupied / z.total) * 100) : 0;
          const free = (z.total || 0) - (z.occupied || 0);
          const color = statusColor(pct);
          const meta = ZONE_COORDS[z.zone_id] || { label: `Zone ${z.zone_id}`, sublabel: '' };
          return (
            <div key={z.zone_id} className="map-kpi" style={{ '--kpi-color': color }}>
              <span className="map-kpi-badge" style={{ background: color }}>{z.zone_id}</span>
              <span className="map-kpi-name">{meta.sublabel}</span>
              <span className="map-kpi-val open">{free}</span>
              <span className="map-kpi-lbl">open</span>
              <span className="map-kpi-sep">/</span>
              <span className="map-kpi-val">{z.total}</span>
              <div className="map-kpi-bar-bg"><div className="map-kpi-bar" style={{ width: `${100 - pct}%`, background: color }} /></div>
              <span className="map-kpi-pct" style={{ color }}>{100 - pct}%</span>
            </div>
          );
        })}
        <div className="map-kpi map-kpi--total">
          <i className="fas fa-parking"></i>
          <span className="map-kpi-name">Total</span>
          <span className="map-kpi-val open">{freeAll}</span>
          <span className="map-kpi-lbl">open</span>
          <span className="map-kpi-sep">/</span>
          <span className="map-kpi-val">{totalAll}</span>
          <div className="map-kpi-bar-bg"><div className="map-kpi-bar" style={{ width: `${100 - pctAll}%`, background: statusColor(pctAll) }} /></div>
          <span className="map-kpi-pct" style={{ color: statusColor(pctAll) }}>{100 - pctAll}%</span>
        </div>
      </div>
    </section>
  );
}
