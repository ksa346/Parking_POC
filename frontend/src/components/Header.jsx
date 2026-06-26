import React from 'react';
import { Link } from 'react-router-dom';
import AppIcon from './AppIcon';
import ThemeToggle from './ThemeToggle';
import { useLocationData } from './LocationInfo';

export default function Header({ wsStatus }) {
  const isLive = wsStatus === 'connected';
  const { weather, wInfo, localTime, location } = useLocationData();

  return (
    <header className="header">
      <h1>
        <AppIcon size={28} /> Smart Parking Solution
      </h1>
      <div className="header-right">
        {/* Location + DateTime + Weather strip */}
        <div className="header-info-strip">
          <span className="header-info-loc">
            <i className="fas fa-map-marker-alt" style={{ color: '#FF4D6A' }}></i>
            {location.name}
          </span>
          <span className="header-info-sep">|</span>
          <span className="header-info-time">
            <i className="fas fa-clock" style={{ color: '#00D4FF' }}></i>
            {localTime || '—'}
          </span>
          {weather && wInfo && (
            <>
              <span className="header-info-sep">|</span>
              <span className="header-info-weather">
                <i className={`fas ${wInfo.icon}`} style={{ color: wInfo.color }}></i>
                {Math.round(weather.temperature_2m)}°F
                <span className="header-info-condition">{wInfo.label}</span>
              </span>
            </>
          )}
        </div>

        <Link to="/locations" className="nav-link">
          <i className="fas fa-arrow-left"></i> Locations
        </Link>
        <Link to="/" className="nav-link">
          <i className="fas fa-home"></i> Home
        </Link>
        <ThemeToggle />
        <div className="live-indicator">
          <span className="live-dot" style={{ background: isLive ? '#86BC25' : '#FF4D6A' }} />
          <span>{isLive ? 'Live' : 'Reconnecting…'}</span>
        </div>
      </div>
    </header>
  );
}
