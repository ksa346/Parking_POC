import React, { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import Card3D from '../components/Card3D';
import AppIcon from '../components/AppIcon';
import AuroraBackground from '../components/AuroraBackground';
import ThemeToggle from '../components/ThemeToggle';
import { listPublishedLocations, deletePublishedLocation } from '../services/api';

const DEMO_LOCATIONS = [
  {
    id: 'mechanicsburg',
    icon: 'fas fa-store',
    title: 'Walmart Supercenter',
    subtitle: 'Mechanicsburg, PA',
    description: '474 spots across 6 zones — live YOLOv8 detection with < 1 s latency.',
    actionLabel: 'View Live Dashboard',
    accent: '#00D4FF',
    spots: 474,
    zones: 6,
    lat: 40.248386,
    lon: -77.0239493,
  },
  {
    id: 'elmhurst',
    icon: 'fas fa-book-open',
    title: 'Elmhurst Public Library',
    subtitle: 'Elmhurst, IL',
    description: '120 spots across 3 zones — community-friendly real-time occupancy.',
    actionLabel: 'View Live Dashboard',
    accent: '#86BC25',
    spots: 120,
    zones: 3,
    lat: 41.8994,
    lon: -87.9403,
  },
  {
    id: 'chicago-loop',
    icon: 'fas fa-city',
    title: 'Chicago Loop Garage',
    subtitle: 'Chicago, IL',
    description: '850 spots across 8 zones — multi-level downtown parking intelligence.',
    actionLabel: 'View Live Dashboard',
    accent: '#A855F7',
    spots: 850,
    zones: 8,
    lat: 41.8827,
    lon: -87.6233,
  },
  {
    id: 'ohare',
    icon: 'fas fa-plane-departure',
    title: "O'Hare Airport Lot E",
    subtitle: 'Chicago, IL',
    description: '2 400 spots across 12 zones — economy long-term parking analytics.',
    actionLabel: 'View Live Dashboard',
    accent: '#FF4D6A',
    spots: 2400,
    zones: 12,
    lat: 41.9742,
    lon: -87.9073,
  },
];

const DYNAMIC_ACCENTS = ['#00D4FF', '#86BC25', '#A855F7', '#FF4D6A', '#F59E0B', '#10B981'];

const HIDDEN_KEY = 'parking_hidden_locations';
function getHiddenIds() {
  try { return JSON.parse(localStorage.getItem(HIDDEN_KEY)) || []; } catch { return []; }
}
function addHiddenId(id) {
  const ids = getHiddenIds();
  if (!ids.includes(id)) { ids.push(id); localStorage.setItem(HIDDEN_KEY, JSON.stringify(ids)); }
}

export default function UserLocations() {
  const navigate = useNavigate();
  const [locations, setLocations] = useState(() =>
    DEMO_LOCATIONS.filter((d) => !getHiddenIds().includes(d.id))
  );
  const [deleting, setDeleting] = useState(null);

  const handleDelete = async (e, locId) => {
    e.stopPropagation();
    if (!window.confirm('Delete this location permanently?')) return;
    setDeleting(locId);
    try {
      const demo = DEMO_LOCATIONS.find((d) => d.id === locId);
      if (demo) {
        addHiddenId(locId);
      } else {
        await deletePublishedLocation(locId);
      }
      setLocations((prev) => prev.filter((l) => l.id !== locId));
    } catch (err) {
      alert('Failed to delete location');
    } finally {
      setDeleting(null);
    }
  };

  useEffect(() => {
    document.title = 'Parking Locations — Smart Parking Solution';
    const hidden = getHiddenIds();
    listPublishedLocations()
      .then((published) => {
        if (!Array.isArray(published) || published.length === 0) return;
        const dynamic = published.map((loc, i) => {
          // Use lat/lon from API (geocoded on backend)
          const lat = loc.lat ?? null;
          const lon = loc.lon ?? null;
          // Normalise parameters — may come back as a JSON string from postgres
          let parameters = loc.parameters;
          if (typeof parameters === 'string') {
            try { parameters = JSON.parse(parameters); } catch { parameters = {}; }
          }
          return {
            id: loc.id,
            icon: 'fas fa-map-pin',
            title: loc.name,
            subtitle: loc.google_maps_url ? 'Custom Location' : 'Developer Setup',
            description: `${loc.total_spots} spots across ${loc.zones?.length || 0} zones — developer-published location.`,
            actionLabel: 'View Live Dashboard',
            accent: DYNAMIC_ACCENTS[i % DYNAMIC_ACCENTS.length],
            spots: loc.total_spots,
            zones: loc.zones?.length || 0,
            zoneList: loc.zones,
            dynamic: true,
            lat,
            lon,
            google_maps_url: loc.google_maps_url,
            video_url: loc.video_url || null,
            parameters: parameters || {},
          };
        });
        setLocations([
          ...DEMO_LOCATIONS.filter((d) => !hidden.includes(d.id)),
          ...dynamic,
        ]);
      })
      .catch(() => { /* keep demo locations on error */ });
  }, []);

  return (
    <div className="lp locations-page">
      <AuroraBackground />

      {/* Nav */}
      <nav className="lp-nav">
        <Link to="/" className="lp-brand" style={{ textDecoration: 'none' }}>
          <AppIcon size={38} className="lp-brand-icon" />
          <div>
            <div className="lp-brand-name">Smart Parking Solution</div>
            <div className="lp-brand-sub">AI-Powered Intelligence</div>
          </div>
        </Link>
        <div className="lp-nav-right">
          <ThemeToggle />
          <Link to="/get-started" className="lp-nav-cta">
            <i className="fas fa-arrow-left" /> Choose Persona
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <div className="persona-hero locations-hero">
        <div className="persona-hero__header">
          <div className="lp-badge">
            <i className="fas fa-map-marker-alt" /> Live Parking Locations
          </div>
          <h1 className="lp-title">
            Choose a <span className="lp-title-accent">Location</span>
          </h1>
          <p className="lp-subtitle" style={{ maxWidth: 560, textAlign: 'center' }}>
            Select a parking facility to view its live occupancy dashboard.
          </p>
        </div>

        {/* Location Cards */}
        <div className="location-cards">
          {locations.map((loc) => (
            <Card3D
              key={loc.id}
              icon={loc.icon}
              title={loc.title}
              subtitle={loc.subtitle}
              description={loc.description}
              actionLabel={loc.actionLabel}
              accentColor={loc.accent}
              onAction={() => navigate('/dashboard', { state: { location: loc } })}
            >
              {/* Mini stats inside card */}
              <div className="loc-card-stats">
                <span><i className="fas fa-parking" /> {loc.spots} spots</span>
                <span><i className="fas fa-layer-group" /> {loc.zones} zones</span>
              </div>
              <button
                className="loc-card-delete"
                title="Delete location"
                disabled={deleting === loc.id}
                onClick={(e) => handleDelete(e, loc.id)}
              >
                {deleting === loc.id
                  ? <i className="fas fa-spinner fa-spin" />
                  : <i className="fas fa-trash-alt" />}
              </button>
            </Card3D>
          ))}
        </div>
      </div>

      {/* Footer */}
      <footer className="lp-footer">
        © 2026 Smart Parking Solution · Built for intelligent cities
      </footer>
    </div>
  );
}
