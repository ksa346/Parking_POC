import React from 'react';

export default function StatCards({ occupancy, stats }) {
  const available = occupancy?.available_spots ?? '—';
  const occupied = occupancy?.occupied_spots ?? '—';

  let avgOccupancy = '—';
  if (stats?.today_average_occupancy !== undefined) {
    avgOccupancy = Math.round(stats.today_average_occupancy);
  }

  let peakHourLabel = '—';
  let peakSuffix = 'PM';
  if (stats?.peak_hour !== undefined) {
    const h = stats.peak_hour;
    peakHourLabel = h === 0 ? '12' : h > 12 ? h - 12 : h;
    peakSuffix = h >= 12 ? 'PM' : 'AM';
  }

  return (
    <div className="stats-grid">
      <div className="stat-card primary">
        <div className="stat-label">Available Spots</div>
        <div className="stat-value">{available}</div>
        <div className="stat-sub">of {occupancy?.total_spots ?? '—'} total</div>
      </div>
      <div className="stat-card accent">
        <div className="stat-label">Occupied</div>
        <div className="stat-value">{occupied}</div>
        <div className="stat-sub">vehicles detected</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Avg. Occupancy</div>
        <div className="stat-value">
          {avgOccupancy}<span className="stat-unit"> cars</span>
        </div>
        <div className="stat-sub">today's average</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Peak Hour</div>
        <div className="stat-value">
          {peakHourLabel}<span className="stat-unit">{peakSuffix}</span>
        </div>
        <div className="stat-sub">highest traffic</div>
      </div>
    </div>
  );
}
