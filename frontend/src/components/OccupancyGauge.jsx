import React from 'react';
import { useActiveLocation } from '../contexts/ActiveLocationContext';

export default function OccupancyGauge({ occupancy }) {
  const activeLoc = useActiveLocation();
  const percent = occupancy?.occupancy_percent ?? 0;
  const percentDisplay = occupancy ? `${Math.round(percent)}%` : '—';

  // Gauge arc calculation: total arc length ~220
  const dashOffset = occupancy ? 220 - (220 * percent / 100) : 220;
  const color = percent >= 85 ? '#FF4D6A' : percent >= 60 ? '#F7C325' : '#86BC25';

  const zones = occupancy?.zones || [];
  const zonePct = (z) => z.total ? Math.round((z.occupied / z.total) * 100) : 0;

  // Build zone label map: prefer activeLoc.zoneList labels, fall back to zone_id
  const zoneLabels = React.useMemo(() => {
    const defaults = {
      TL: 'Top-Left', TR: 'Top-Right',
      ML: 'Mid-Left', MR: 'Mid-Right',
      BL: 'Bot-Left', BR: 'Bot-Right',
    };
    if (activeLoc?.zoneList && activeLoc.zoneList.length > 0) {
      const map = {};
      activeLoc.zoneList.forEach((z) => { map[z.zone_id] = z.zone_id; });
      return map;
    }
    return defaults;
  }, [activeLoc]);

  const getZoneLabel = (zoneId) => zoneLabels[zoneId] || `Zone ${zoneId}`;

  const confidenceLabel = (c) => {
    if (!c) return '—';
    return { high: 'High', medium: 'Medium', low: 'Low', no_data: 'No Data' }[c] || c;
  };

  const lastUpdated = occupancy?.timestamp
    ? new Date(occupancy.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—';

  const locName = activeLoc?.title || 'Live Detection';
  const totalSpots = occupancy?.total_spots ?? activeLoc?.spots ?? 0;

  return (
    <div className="occupancy-card">
      <h3><i className="fas fa-chart-pie"></i> {locName}</h3>

      <div className="gauge-container">
        <svg viewBox="0 0 200 110" style={{ width: '100%', height: '100%' }}>
          <path className="gauge-bg" d="M20,95 A75,75 0 0,1 180,95" />
          <path
            className="gauge-fill"
            d="M20,95 A75,75 0 0,1 180,95"
            strokeDasharray="236"
            strokeDashoffset={dashOffset}
            style={{ stroke: color }}
          />
        </svg>
        <div className="gauge-text">
          <div className="gauge-percent">{percentDisplay}</div>
          <div className="gauge-label">Occupied</div>
        </div>
      </div>

      {/* Double-Park Alert */}
      {(occupancy?.double_parked_count ?? 0) > 0 && (
        <div className="dp-alert">
          <i className="fas fa-exclamation-triangle"></i>
          <span>{occupancy.double_parked_count} double-parked vehicle{occupancy.double_parked_count > 1 ? 's' : ''} detected</span>
        </div>
      )}

      {/* Zone Breakdown */}
      <div className="zone-breakdown">
        {zones.map((z) => (
          <ZoneRow
            key={z.zone_id}
            label={getZoneLabel(z.zone_id)}
            cls={`zone-${z.zone_id.toLowerCase()}`}
            occupied={z.occupied}
            total={z.total}
            pct={zonePct(z)}
            doublePark={z.double_parked}
          />
        ))}
      </div>

      <div style={{ marginTop: '1rem' }}>
        <div className="meta-row">
          <span>Total Spots</span>
          <strong>{totalSpots || '—'}</strong>
        </div>
        <div className="meta-row">
          <span>Confidence</span>
          <strong>{confidenceLabel(occupancy?.confidence)}</strong>
        </div>
        <div className="meta-row">
          <span>Last Updated</span>
          <strong>{lastUpdated}</strong>
        </div>
      </div>
    </div>
  );
}

function ZoneRow({ label, cls, occupied, total, pct, doublePark }) {
  return (
    <div className={`zone-item ${cls}`}>
      <span className="zone-name"><span className="zone-dot" /> {label}</span>
      <span className="zone-stats">
        <span className="zone-count">{occupied ?? '—'}</span>
        <span className="zone-total">/{total}</span>
        {doublePark > 0 && <span className="dp-badge" title="Double-parked">2×{doublePark}</span>}
        <span className="zone-bar-bg">
          <span className="zone-bar" style={{ width: `${pct}%` }} />
        </span>
      </span>
    </div>
  );
}
