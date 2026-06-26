import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import Globe from '../components/Globe';
import AppIcon from '../components/AppIcon';
import AuroraBackground from '../components/AuroraBackground';
import ThemeToggle from '../components/ThemeToggle';

const FEATURES = [
  { icon: 'fas fa-video',          label: 'Real-Time Video Analytics', color: '#00D4FF' },
  { icon: 'fas fa-brain',          label: 'AI Parking Assistant',      color: '#86BC25' },
  { icon: 'fas fa-chart-line',     label: 'Predictive Forecasting',   color: '#A855F7' },
  { icon: 'fas fa-map-marked-alt', label: 'Interactive Zone Map',     color: '#FF4D6A' },
  { icon: 'fas fa-gauge-high',     label: 'Occupancy Dashboard',      color: '#F7C325' },
  { icon: 'fas fa-satellite-dish', label: 'WebSocket Live Updates',   color: '#00D4FF' },
];

const STATS = [
  { value: '6',    label: 'Parking Zones',     icon: 'fas fa-layer-group' },
  { value: '<1s',  label: 'Update Latency',    icon: 'fas fa-bolt' },
  { value: '474',  label: 'Total Spots',       icon: 'fas fa-parking' },
  { value: '24/7', label: 'Live Monitoring',    icon: 'fas fa-clock' },
];

export default function LandingPage() {
  useEffect(() => {
    document.title = 'Smart Parking Solution — AI-Powered Parking Intelligence';
  }, []);

  return (
    <div className="lp">
      <AuroraBackground />

      {/* ── Nav ── */}
      <nav className="lp-nav">
        <div className="lp-brand">
          <AppIcon size={38} className="lp-brand-icon" />
          <div>
            <div className="lp-brand-name">Smart Parking Solution</div>
            <div className="lp-brand-sub">AI-Powered Intelligence</div>
          </div>
        </div>
        <div className="lp-nav-right">
          <ThemeToggle />
          <Link to="/get-started" className="lp-nav-cta">
            Get Started <i className="fas fa-arrow-right"></i>
          </Link>
        </div>
      </nav>

      {/* ── Main two-column hero ── */}
      <div className="lp-hero">
        {/* Left column */}
        <div className="lp-hero-text">
          <div className="lp-badge">
            <i className="fas fa-sparkles"></i> Next-Gen Smart Parking
          </div>

          <h1 className="lp-title">
            Smart Parking<br /><span className="lp-title-accent">Solution</span>
          </h1>

          <p className="lp-subtitle">
            AI-powered parking intelligence — real-time video analytics,
            predictive occupancy forecasting, and a conversational assistant,
            all in one dashboard.
          </p>

          {/* Feature chips */}
          <div className="lp-chips">
            {FEATURES.map((f) => (
              <span key={f.label} className="lp-chip" style={{ '--chip-c': f.color }}>
                <i className={f.icon}></i> {f.label}
              </span>
            ))}
          </div>

          {/* CTAs */}
          <div className="lp-ctas">
            <Link to="/get-started" className="lp-cta-primary">
              Get Started <i className="fas fa-arrow-right"></i>
            </Link>
          </div>

          {/* Stats */}
          <div className="lp-stats">
            {STATS.map((s) => (
              <div key={s.label} className="lp-stat">
                <span className="lp-stat-val"><i className={s.icon}></i> {s.value}</span>
                <span className="lp-stat-lbl">{s.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Right column — Globe */}
        <div className="lp-globe-wrap">
          <Globe className="lp-globe" />
        </div>
      </div>

      {/* ── Footer ── */}
      <footer className="lp-footer">
        © 2026 Smart Parking Solution · Built for intelligent cities
      </footer>
    </div>
  );
}
