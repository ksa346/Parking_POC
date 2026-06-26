import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import Header from '../components/Header';
import LiveFeed from '../components/LiveFeed';
import VideoFeed from '../components/VideoFeed';
import DashboardView from '../components/DashboardView';
import MapView from '../components/MapView';
import OccupancyGauge from '../components/OccupancyGauge';
import DataFlow from './DataFlow';
import FloatingChat from '../components/FloatingChat';
import { ActiveLocationProvider } from '../contexts/ActiveLocationContext';
import { useOccupancy } from '../hooks/useOccupancy';
import { useHistory, useForecast, useStats } from '../hooks/useData';
import { activateLocation, restoreDefaultLocation } from '../services/api';

const TABS = [
  { id: 'detection', label: 'Detection',   icon: 'fas fa-crosshairs',      color: '#A855F7' },
  { id: 'map',       label: 'Map View',    icon: 'fas fa-map-marked-alt',  color: '#86BC25' },
  { id: 'dashboard', label: 'Dashboard',   icon: 'fas fa-chart-line',      color: '#F7C325' },
  { id: 'dataflow',  label: 'Data Flow',   icon: 'fas fa-project-diagram', color: '#FF4D6A' },
];

export default function Dashboard() {
  const { state } = useLocation();
  const activeLocation = state?.location || null;
  const { data: occupancy, wsStatus } = useOccupancy();
  const historyEntries = useHistory(24);
  const forecasts = useForecast();
  const stats = useStats();
  const [activeTab, setActiveTab] = useState('dashboard');
  const [showLiveFeed, setShowLiveFeed] = useState(false);

  // Activate location when it's selected (load grid config and zones)
  React.useEffect(() => {
    if (!activeLocation) return;
    if (activeLocation.dynamic) {
      activateLocation(activeLocation.id).catch(err => {
        console.error('Failed to activate location:', err);
      });
    } else {
      restoreDefaultLocation().catch(() => {});
    }
  }, [activeLocation?.id, activeLocation?.dynamic]);

  return (
    <ActiveLocationProvider value={activeLocation}>
      <Header wsStatus={wsStatus} />

      {/* ── Tab Navigation (marketplace sidebar style) ── */}
      <nav className="nav-rail">
        <div className="nav-rail__shimmer" />
        {TABS.map((t) => {
          const isActive = activeTab === t.id;
          return (
            <button
              key={t.id}
              className={`nav-rail__item${isActive ? ' nav-rail__item--active' : ''}`}
              style={{ '--tab-color': t.color }}
              onClick={() => setActiveTab(t.id)}
            >
              {isActive && <span className="nav-rail__dot" />}
              <span className="nav-rail__glow" />
              <i className={t.icon} />
              <span className="nav-rail__label">{t.label}</span>
            </button>
          );
        })}
      </nav>

      {activeTab === 'dataflow' ? (
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
          <DataFlow embedded />
        </div>
      ) : (
        <main className="main">
          {/* LEFT PANEL — tab content */}
          <div className={`left-panel ${activeTab === 'detection' || activeTab === 'map' ? 'left-panel--fill' : 'left-panel--scroll'}`}>
            {activeTab === 'detection' && (
              <VideoFeed wsStatus={wsStatus} occupancy={occupancy} />
            )}

            {activeTab === 'map' && (
              <MapView occupancy={occupancy} />
            )}

            {activeTab === 'dashboard' && (
              <DashboardView occupancy={occupancy} stats={stats} historyEntries={historyEntries} forecasts={forecasts} />
            )}
          </div>

          {/* RIGHT PANEL — always visible */}
          <div className="right-panel">
            <OccupancyGauge occupancy={occupancy} />
            <button
              className={`live-feed-toggle${showLiveFeed ? ' live-feed-toggle--active' : ''}`}
              onClick={() => setShowLiveFeed(prev => !prev)}
            >
              <i className="fas fa-video" />
              {showLiveFeed ? 'Hide Live Feed' : 'Show Live Feed'}
            </button>
            {showLiveFeed && <LiveFeed wsStatus={wsStatus} />}
          </div>
        </main>
      )}
      <FloatingChat />
    </ActiveLocationProvider>
  );
}
