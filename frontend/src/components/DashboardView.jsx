import React, { useState, useMemo } from 'react';
import {
  Chart as ChartJS,
  ArcElement,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Legend,
  Tooltip,
  Title,
} from 'chart.js';
import { Doughnut, Line, Bar } from 'react-chartjs-2';
import { useActiveLocation } from '../contexts/ActiveLocationContext';

ChartJS.register(ArcElement, CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Legend, Tooltip, Title);

/* ── Default zone palette & labels (Walmart Mechanicsburg 6-zone) ── */
const DEFAULT_ZONE_META = {
  TL: { label: 'Top-Left',  color: '#6C5CE7', spots: 96  },
  TR: { label: 'Top-Right', color: '#00B894', spots: 94  },
  ML: { label: 'Mid-Left',  color: '#0984E3', spots: 88  },
  MR: { label: 'Mid-Right', color: '#E17055', spots: 90  },
  BL: { label: 'Bot-Left',  color: '#FDCB6E', spots: 52  },
  BR: { label: 'Bot-Right', color: '#E84393', spots: 54  },
};

const ZONE_COLORS = ['#6C5CE7', '#00B894', '#0984E3', '#E17055', '#FDCB6E', '#E84393', '#A855F7', '#00D4FF', '#FF4D6A', '#86BC25', '#F7C325', '#10B981'];

const CHART_TEXT = '#8B949E';
const GRID_COLOR = 'rgba(255,255,255,.06)';

/**
 * Build zone metadata with this priority:
 * 1. Walmart/Mechanicsburg — hardcoded precise labels (TL=Top-Left etc.)
 * 2. Developer-published location with zoneList — use stored zone metadata
 * 3. Any other location — derive from live occupancy zones (arbitrary zone IDs)
 * 4. No occupancy yet — fall back to Walmart defaults
 */
function buildZoneMeta(activeLoc, occupancyZones) {
  // Walmart: use curated labels
  if (!activeLoc || activeLoc.id === 'mechanicsburg') return DEFAULT_ZONE_META;

  // Developer-published location: use stored zone metadata
  if (activeLoc.zoneList && activeLoc.zoneList.length > 0) {
    const meta = {};
    activeLoc.zoneList.forEach((z, i) => {
      meta[z.zone_id] = {
        label: z.zone_id,
        color: ZONE_COLORS[i % ZONE_COLORS.length],
        spots: z.user_spots ?? z.estimated_spots ?? 0,
      };
    });
    return meta;
  }

  // Any location: build from whatever the live backend reports
  if (occupancyZones && occupancyZones.length > 0) {
    const meta = {};
    occupancyZones.forEach((z, i) => {
      meta[z.zone_id] = {
        label: z.zone_id,
        color: ZONE_COLORS[i % ZONE_COLORS.length],
        spots: z.total ?? 0,
      };
    });
    return meta;
  }

  return DEFAULT_ZONE_META;
}

/* ================================================================
   MAIN COMPONENT
   ================================================================ */
export default function DashboardView({ occupancy, stats, historyEntries, forecasts }) {
  const activeLoc = useActiveLocation();
  const zones = occupancy?.zones || [];
  const ZONE_META = useMemo(() => buildZoneMeta(activeLoc, zones), [activeLoc, zones]);
  const [mode, setMode] = useState('realtime');
  const occupied = occupancy?.occupied_spots ?? 0;
  const total = occupancy?.total_spots ?? activeLoc?.spots ?? 0;
  const available = occupancy?.available_spots ?? (total - occupied);
  const pct = occupancy ? Math.round(occupancy.occupancy_percent) : 0;

  const locName = activeLoc?.title ?? 'Smart Parking';
  const locSub  = activeLoc?.subtitle ?? '';

  return (
    <div className="dv">
      {/* ── Location Banner ── */}
      <div className="dv-location-banner">
        <div className="dv-location-banner__left">
          <i className="fas fa-map-marker-alt" style={{ color: activeLoc?.accent ?? '#00D4FF' }} />
          <span className="dv-location-banner__name">{locName}</span>
          {locSub && <span className="dv-location-banner__sub">{locSub}</span>}
        </div>
        <div className="dv-location-banner__right">
          {total > 0 && <span className="dv-location-banner__chip"><i className="fas fa-parking" /> {total} spots</span>}
          {zones.length > 0 && <span className="dv-location-banner__chip"><i className="fas fa-layer-group" /> {zones.length} zones</span>}
          {activeLoc?.dynamic && <span className="dv-location-banner__chip dv-location-banner__chip--live"><i className="fas fa-broadcast-tower" /> Published</span>}
        </div>
      </div>

      {/* ── Mode Toggle ── */}
      <div className="dv-toggle-bar">
        <button className={`dv-toggle-btn${mode === 'realtime' ? ' dv-toggle-btn--active' : ''}`} onClick={() => setMode('realtime')}>
          <i className="fas fa-satellite-dish" /> Real-Time
        </button>
        <button className={`dv-toggle-btn${mode === 'forecast' ? ' dv-toggle-btn--active' : ''}`} onClick={() => setMode('forecast')}>
          <i className="fas fa-chart-line" /> Forecast
        </button>
      </div>

      {mode === 'realtime' ? (
        <>
          {/* ── Row 1 · Hero KPIs ── */}
          <div className="dv-kpis">
            <KpiCard icon="fas fa-car-side" category="DETECTION" label="Vehicles Detected" value={occupied} accent="#00D4FF" sub={`of ${total} capacity`} badge={occupancy?.double_parked_count > 0 ? `${occupancy.double_parked_count} double-parked` : undefined} badgeColor="#FF4D6A" />
            <KpiCard icon="fas fa-parking" category="AVAILABILITY" label="Open Spots" value={available} accent="#86BC25" badge={pct < 60 ? 'Low Traffic' : pct < 85 ? 'Moderate' : 'High Traffic'} badgeColor={pct >= 85 ? '#FF4D6A' : pct >= 60 ? '#F7C325' : '#86BC25'} sub={`${100 - pct}% free`} />
            <KpiCard icon="fas fa-tachometer-alt" category="OCCUPANCY" label="Occupancy Rate" value={`${pct}%`} accent={pct >= 85 ? '#FF4D6A' : pct >= 60 ? '#F7C325' : '#86BC25'} sub="real-time" />
            <KpiCard icon="fas fa-clock" category="ANALYTICS" label="Peak Hour" value={formatPeak(stats?.peak_hour)} accent="#A855F7" sub="highest traffic today" />
          </div>

          {/* ── Row 2 · 3-column: Distribution + Zone Status + Heatmap ── */}
          <div className="dv-row3col">
            <div className="dv-card">
              <span className="dv-card__cat" style={{ color: '#A855F7' }}>DISTRIBUTION</span>
              <h3 className="dv-card__title"><i className="fas fa-chart-pie" /> Zone Occupancy</h3>
              <ZoneDoughnut zones={zones} zoneMeta={ZONE_META} />
            </div>
            <div className="dv-card">
              <span className="dv-card__cat" style={{ color: '#00D4FF' }}>ZONE STATUS</span>
              <h3 className="dv-card__title"><i className="fas fa-th-large" /> Segment Breakdown</h3>
              <ZoneStatusList zones={zones} zoneMeta={ZONE_META} />
            </div>
            <div className="dv-card">
              <span className="dv-card__cat" style={{ color: '#86BC25' }}>HEATMAP</span>
              <h3 className="dv-card__title"><i className="fas fa-grip-horizontal" /> Lot Grid</h3>
              <ZoneHeatmap zones={zones} zoneMeta={ZONE_META} />
            </div>
          </div>

          {/* ── Row 3 · History chart full width ── */}
          <div className="dv-card">
            <span className="dv-card__cat" style={{ color: '#F7C325' }}>TIMELINE</span>
            <h3 className="dv-card__title"><i className="fas fa-chart-area" /> Occupancy History</h3>
            <HistoryChart entries={historyEntries} />
          </div>

          {/* ── Row 4 · Recent Detections table ── */}
          <div className="dv-card">
            <div className="dv-card__header-row">
              <div>
                <span className="dv-card__cat" style={{ color: '#FF4D6A' }}>HISTORY</span>
                <h3 className="dv-card__title" style={{ marginBottom: 0 }}><i className="fas fa-history" /> Recent Detections</h3>
              </div>
              <span className="dv-card__meta">{historyEntries.length} records</span>
            </div>
            <RecentDetections entries={historyEntries} />
          </div>

          {/* ── Row 5 · Insights ── */}
          <InsightsStrip occupancy={occupancy} stats={stats} zones={zones} zoneMeta={ZONE_META} />
        </>
      ) : (
        <>
          <ForecastKpis forecasts={forecasts} stats={stats} />
          <div className="dv-card">
            <span className="dv-card__cat" style={{ color: '#F7C325' }}>PREDICTION</span>
            <h3 className="dv-card__title"><i className="fas fa-chart-bar" /> Hourly Forecast</h3>
            <ForecastChart forecasts={forecasts} />
          </div>
          <ForecastTable forecasts={forecasts} />
        </>
      )}
    </div>
  );
}

/* ── Helpers ── */
function formatPeak(h) {
  if (h == null) return '—';
  const suffix = h >= 12 ? 'PM' : 'AM';
  const hr = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${hr} ${suffix}`;
}
function pctOf(occ, tot) { return tot ? Math.round((occ / tot) * 100) : 0; }

/* ================================================================
   KPI CARD — EDI-style with category label + optional badge
   ================================================================ */
function KpiCard({ icon, category, label, value, accent, sub, badge, badgeColor }) {
  return (
    <div className="dv-kpi" style={{ '--kpi-accent': accent }}>
      <div className="dv-kpi__glow" />
      <div className="dv-kpi__icon"><i className={icon} /></div>
      <div className="dv-kpi__body">
        <span className="dv-kpi__cat">{category}</span>
        <span className="dv-kpi__label">{label}</span>
        <div className="dv-kpi__val-row">
          <span className="dv-kpi__value">{value}</span>
          {badge && <span className="dv-kpi__badge" style={{ '--badge-color': badgeColor }}>{badge}</span>}
        </div>
        <span className="dv-kpi__sub">{sub}</span>
      </div>
    </div>
  );
}

/* ================================================================
   ZONE STATUS LIST — progress bars beside zone counts
   ================================================================ */
function ZoneStatusList({ zones, zoneMeta }) {
  const safeMeta = zoneMeta || DEFAULT_ZONE_META;
  const ids = Object.keys(safeMeta);
  const zoneMap = {};
  zones.forEach((z) => { zoneMap[z.zone_id] = z; });

  return (
    <div className="dv-zstatus">
      {ids.map((id) => {
        const z = zoneMap[id] || {};
        const meta = safeMeta[id] || { color: '#888', label: id, spots: 0 };
        const occ = z.occupied ?? 0;
        const tot = z.total ?? meta.spots;
        const p = pctOf(occ, tot);
        return (
          <div key={id} className="dv-zstatus__row">
            <div className="dv-zstatus__left">
              <span className="dv-zstatus__dot" style={{ background: meta.color }} />
              <span className="dv-zstatus__name">{meta.label}</span>
            </div>
            <div className="dv-zstatus__right">
              <span className="dv-zstatus__count">{occ}<span className="dv-zstatus__total">/{tot}</span></span>
              <div className="dv-zstatus__bar-bg">
                <div className="dv-zstatus__bar" style={{ width: `${p}%`, background: meta.color }} />
              </div>
              <span className="dv-zstatus__pct">{p}%</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ================================================================
   ZONE HEATMAP — 2 × 3 visual grid
   ================================================================ */
function ZoneHeatmap({ zones, zoneMeta }) {
  const safeMeta = zoneMeta || DEFAULT_ZONE_META;
  const zoneMap = {};
  zones.forEach((z) => { zoneMap[z.zone_id] = z; });
  const ids = Object.keys(safeMeta);
  const cols = 2;
  const grid = [];
  for (let i = 0; i < ids.length; i += cols) {
    grid.push(ids.slice(i, i + cols));
  }

  return (
    <>
      <div className="dv-heatmap__grid">
        {grid.map((row) =>
          row.map((id) => {
            const z = zoneMap[id] || {};
            const meta = safeMeta[id] || { color: '#888', spots: 0 };
            const p = pctOf(z.occupied || 0, z.total || meta.spots);
            const intensity = Math.min(p / 100, 1);
            return (
              <div key={id} className="dv-heatmap__cell" style={{ '--cell-color': meta.color, '--cell-intensity': intensity }}>
                <span className="dv-heatmap__id">{id}</span>
                <span className="dv-heatmap__pct">{p}%</span>
                <span className="dv-heatmap__count">{z.occupied ?? 0}/{z.total ?? meta.spots}</span>
              </div>
            );
          })
        )}
      </div>
      <div className="dv-heatmap__legend">
        <span>0%</span>
        <div className="dv-heatmap__bar" />
        <span>100%</span>
      </div>
    </>
  );
}

/* ================================================================
   ZONE DOUGHNUT
   ================================================================ */
function ZoneDoughnut({ zones, zoneMeta }) {
  const meta = zoneMeta || DEFAULT_ZONE_META;
  const data = useMemo(() => {
    const ids = Object.keys(meta);
    const zoneMap = {};
    zones.forEach((z) => { zoneMap[z.zone_id] = z; });
    return {
      labels: ids.map((id) => meta[id].label),
      datasets: [{
        data: ids.map((id) => zoneMap[id]?.occupied ?? 0),
        backgroundColor: ids.map((id) => meta[id].color + 'CC'),
        borderColor: ids.map((id) => meta[id].color),
        borderWidth: 2,
        hoverOffset: 8,
      }],
    };
  }, [zones, meta]);

  const total = zones.reduce((s, z) => s + (z.occupied ?? 0), 0);

  return (
    <div className="dv-doughnut__wrap">
      <Doughnut
        data={data}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          cutout: '68%',
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 10, padding: 8, font: { family: 'Inter', size: 10 }, color: '#C9D1D9' } },
            tooltip: { backgroundColor: '#131B26', titleFont: { family: 'Inter' }, bodyFont: { family: 'Inter' }, padding: 10, cornerRadius: 8, borderColor: 'rgba(48,54,61,0.8)', borderWidth: 1 },
          },
        }}
      />
      <div className="dv-doughnut__center">
        <span className="dv-doughnut__total">{total}</span>
        <span className="dv-doughnut__lbl">vehicles</span>
      </div>
    </div>
  );
}

/* ================================================================
   HISTORY CHART (area) — rendered inside a card
   ================================================================ */
function HistoryChart({ entries }) {
  const sorted = useMemo(() =>
    [...entries].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp)),
    [entries]
  );

  if (!sorted.length) return <div className="dv-chart-empty"><i className="fas fa-chart-area" /> Collecting data — history will appear shortly...</div>;

  const labels = sorted.map((e) =>
    new Date(e.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  );

  const data = {
    labels,
    datasets: [
      { label: 'Occupied', data: sorted.map((e) => e.occupied), borderColor: '#00D4FF', backgroundColor: 'rgba(0,212,255,.12)', fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2 },
      { label: 'Total', data: sorted.map((e) => e.total), borderColor: '#86BC25', borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5, fill: false },
    ],
  };

  return <div className="dv-chart__body"><Line data={data} options={chartOpts('Vehicles')} /></div>;
}

/* ================================================================
   RECENT DETECTIONS TABLE — EDI "Recent Runs" style
   ================================================================ */
function RecentDetections({ entries }) {
  const sorted = useMemo(() =>
    [...entries].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)).slice(0, 15),
    [entries]
  );

  if (!sorted.length) return <div className="dv-chart-empty"><i className="fas fa-table" /> No detection records yet</div>;

  return (
    <div className="dv-ftable-wrap">
      <table className="dv-ftable">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Occupied</th>
            <th>Total</th>
            <th>Rate</th>
            <th>Status</th>
            <th style={{ width: '25%' }}>Utilization</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e, i) => {
            const p = e.total ? Math.round((e.occupied / e.total) * 100) : 0;
            const levelColor = p >= 85 ? '#FF4D6A' : p >= 60 ? '#F7C325' : '#86BC25';
            const levelLabel = p >= 85 ? 'HIGH' : p >= 60 ? 'MODERATE' : 'LOW';
            return (
              <tr key={i}>
                <td className="dv-ftable__hour">{new Date(e.timestamp).toLocaleString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</td>
                <td className="dv-ftable__pct" style={{ color: '#00D4FF' }}>{e.occupied}</td>
                <td>{e.total}</td>
                <td className="dv-ftable__pct">{p}%</td>
                <td><span className="dv-ftable__level" style={{ '--lvl-color': levelColor }}>{levelLabel}</span></td>
                <td>
                  <div className="dv-ftable__bar-bg">
                    <div className="dv-ftable__bar" style={{ width: `${p}%`, background: levelColor }} />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ================================================================
   FORECAST CHART (bar)
   ================================================================ */
function ForecastChart({ forecasts }) {
  const sorted = useMemo(() =>
    [...forecasts].sort((a, b) => a.target_hour - b.target_hour),
    [forecasts]
  );

  if (!sorted.length) return <div className="dv-chart-empty"><i className="fas fa-chart-bar" /> No forecast data yet</div>;

  const nowHour = new Date().getHours();
  const labels = sorted.map((f) => {
    const h = f.target_hour;
    return h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h - 12} PM`;
  });

  const data = {
    labels,
    datasets: [{
      label: 'Predicted Occupancy %',
      data: sorted.map((f) => f.predicted_occupancy),
      backgroundColor: sorted.map((f) =>
        f.target_hour === nowHour ? 'rgba(0,212,255,.7)' : 'rgba(247,195,37,.25)'
      ),
      borderColor: sorted.map((f) =>
        f.target_hour === nowHour ? '#00D4FF' : '#F7C325'
      ),
      borderWidth: 1.5,
      borderRadius: 6,
    }],
  };

  return <div className="dv-chart__body"><Bar data={data} options={chartOpts('Occupancy %', 100)} /></div>;
}

function chartOpts(yLabel, yMax) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 14, font: { family: 'Inter', size: 11 }, color: '#C9D1D9' } },
      tooltip: { backgroundColor: '#131B26', titleFont: { family: 'Inter' }, bodyFont: { family: 'Inter' }, padding: 10, cornerRadius: 8, borderColor: 'rgba(48,54,61,0.8)', borderWidth: 1 },
    },
    scales: {
      x: { grid: { color: GRID_COLOR }, ticks: { font: { family: 'Inter', size: 10 }, color: CHART_TEXT, maxTicksLimit: 12 } },
      y: { grid: { color: GRID_COLOR }, ticks: { font: { family: 'Inter', size: 10 }, color: CHART_TEXT }, beginAtZero: true, ...(yMax ? { max: yMax } : {}), title: { display: true, text: yLabel, font: { family: 'Inter', size: 11 } } },
    },
  };
}

/* ================================================================
   INSIGHTS STRIP
   ================================================================ */
function InsightsStrip({ occupancy, stats, zones, zoneMeta }) {
  const activeLoc = useActiveLocation();
  const safeMeta = zoneMeta || DEFAULT_ZONE_META;
  const pct = occupancy ? Math.round(occupancy.occupancy_percent) : 0;
  const statusColor = pct >= 85 ? '#FF4D6A' : pct >= 60 ? '#F7C325' : '#86BC25';
  const statusLabel = pct >= 85 ? 'HIGH' : pct >= 60 ? 'MODERATE' : 'LOW';

  let busiest = null, quietest = null;
  if (zones.length) {
    const withPct = zones.map((z) => ({ ...z, pct: pctOf(z.occupied, z.total) }));
    busiest = withPct.reduce((a, b) => a.pct > b.pct ? a : b);
    quietest = withPct.reduce((a, b) => a.pct < b.pct ? a : b);
  }

  const updated = occupancy?.timestamp
    ? new Date(occupancy.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—';

  return (
    <div className="dv-insights">
      <div className="dv-insight" style={{ '--ins-color': statusColor }}>
        <i className="fas fa-traffic-light" />
        <span>Status: <strong>{statusLabel}</strong></span>
      </div>
      {busiest && (
        <div className="dv-insight" style={{ '--ins-color': safeMeta[busiest.zone_id]?.color || '#FF4D6A' }}>
          <i className="fas fa-fire" />
          <span>Busiest: <strong>{safeMeta[busiest.zone_id]?.label || busiest.zone_id} ({busiest.pct}%)</strong></span>
        </div>
      )}
      {quietest && (
        <div className="dv-insight" style={{ '--ins-color': safeMeta[quietest.zone_id]?.color || '#86BC25' }}>
          <i className="fas fa-leaf" />
          <span>Quietest: <strong>{safeMeta[quietest.zone_id]?.label || quietest.zone_id} ({quietest.pct}%)</strong></span>
        </div>
      )}
      <div className="dv-insight" style={{ '--ins-color': '#00D4FF' }}>
        <i className="fas fa-sync-alt" />
        <span>Updated: <strong>{updated}</strong></span>
      </div>
      <div className="dv-insight" style={{ '--ins-color': '#A855F7' }}>
        <i className="fas fa-microchip" />
        <span>Model: <strong>{(() => {
          // Prefer model stored in postgres location parameters; fall back to live detection_method
          const stored = activeLoc?.parameters?.model_path;
          if (stored) return stored.replace(/\.pt$/, '').split('/').slice(-1)[0] + ' · 6-seg';
          if (occupancy?.detection_method)
            return occupancy.detection_method.replace('yolo:', '').replace(/\.pt$/, '') + ' · 6-seg';
          return 'YOLOv8 · 6-seg';
        })()}</strong></span>
      </div>
    </div>
  );
}

/* ================================================================
   FORECAST KPIs
   ================================================================ */
function ForecastKpis({ forecasts, stats }) {
  const sorted = useMemo(() =>
    [...forecasts].sort((a, b) => a.target_hour - b.target_hour),
    [forecasts]
  );

  const peak = sorted.length ? sorted.reduce((a, b) => a.predicted_occupancy > b.predicted_occupancy ? a : b) : null;
  const low = sorted.length ? sorted.reduce((a, b) => a.predicted_occupancy < b.predicted_occupancy ? a : b) : null;
  const avg = sorted.length ? Math.round(sorted.reduce((s, f) => s + f.predicted_occupancy, 0) / sorted.length) : '—';

  return (
    <div className="dv-kpis">
      <KpiCard icon="fas fa-chart-line" category="FORECAST" label="Data Points" value={sorted.length} accent="#00D4FF" sub="hourly predictions" />
      <KpiCard icon="fas fa-arrow-up" category="PEAK" label="Highest Predicted" value={peak ? `${Math.round(peak.predicted_occupancy)}%` : '—'} accent="#FF4D6A" sub={peak ? `at ${formatPeak(peak.target_hour)}` : ''} />
      <KpiCard icon="fas fa-arrow-down" category="LOW" label="Lowest Predicted" value={low ? `${Math.round(low.predicted_occupancy)}%` : '—'} accent="#86BC25" sub={low ? `at ${formatPeak(low.target_hour)}` : ''} />
      <KpiCard icon="fas fa-balance-scale" category="AVERAGE" label="Mean Forecast" value={typeof avg === 'number' ? `${avg}%` : avg} accent="#A855F7" sub="across all hours" />
    </div>
  );
}

/* ================================================================
   FORECAST TABLE
   ================================================================ */
function ForecastTable({ forecasts }) {
  const sorted = useMemo(() =>
    [...forecasts].sort((a, b) => a.target_hour - b.target_hour),
    [forecasts]
  );

  if (!sorted.length) return <div className="dv-card dv-chart-empty"><i className="fas fa-table" /> No forecast data yet</div>;

  const nowHour = new Date().getHours();

  return (
    <div className="dv-card">
      <div className="dv-card__header-row">
        <div>
          <span className="dv-card__cat" style={{ color: '#F7C325' }}>BREAKDOWN</span>
          <h3 className="dv-card__title" style={{ marginBottom: 0 }}><i className="fas fa-table" /> Hourly Breakdown</h3>
        </div>
        <span className="dv-card__meta">{sorted.length} hours</span>
      </div>
      <div className="dv-ftable-wrap">
        <table className="dv-ftable">
          <thead>
            <tr>
              <th>Hour</th>
              <th>Predicted</th>
              <th>Confidence</th>
              <th>Level</th>
              <th style={{ width: '35%' }}>Bar</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((f) => {
              const p = Math.round(f.predicted_occupancy);
              const isCurrent = f.target_hour === nowHour;
              const levelColor = p >= 85 ? '#FF4D6A' : p >= 60 ? '#F7C325' : '#86BC25';
              const levelLabel = p >= 85 ? 'High' : p >= 60 ? 'Moderate' : 'Low';
              return (
                <tr key={f.target_hour} className={isCurrent ? 'dv-ftable__current' : ''}>
                  <td className="dv-ftable__hour">{formatPeak(f.target_hour)}{isCurrent ? ' *' : ''}</td>
                  <td className="dv-ftable__pct">{p}%</td>
                  <td className="dv-ftable__conf">{f.confidence || '—'}</td>
                  <td><span className="dv-ftable__level" style={{ '--lvl-color': levelColor }}>{levelLabel}</span></td>
                  <td>
                    <div className="dv-ftable__bar-bg">
                      <div className="dv-ftable__bar" style={{ width: `${p}%`, background: levelColor }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
