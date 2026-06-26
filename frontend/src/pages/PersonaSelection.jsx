import React, { useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import Card3D from '../components/Card3D';
import AppIcon from '../components/AppIcon';
import AuroraBackground from '../components/AuroraBackground';
import ThemeToggle from '../components/ThemeToggle';

const PERSONAS = [
  {
    id: 'user',
    icon: 'fas fa-user-shield',
    title: 'User Persona',
    subtitle: 'Parking Consumer',
    description: 'Find available parking spots, view live occupancy, and navigate to the nearest open space in real time.',
    actionLabel: 'Browse Locations',
    accent: '#00D4FF',
    route: '/locations',
  },
  {
    id: 'developer',
    icon: 'fas fa-code',
    title: 'Developer Persona',
    subtitle: 'API & Integration',
    description: 'Configure video feeds, segment grids, detect parking spots with GPT, tune hyperparameters, and publish new locations.',
    actionLabel: 'Start Setup',
    accent: '#A855F7',
    route: '/developer-setup',
  },
];

export default function PersonaSelection() {
  const navigate = useNavigate();

  useEffect(() => {
    document.title = 'Get Started — Smart Parking Solution';
  }, []);

  return (
    <div className="lp persona-page">
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
        <ThemeToggle />
      </nav>

      {/* Hero */}
      <div className="persona-hero">
        <div className="persona-hero__header">
          <div className="lp-badge">
            <i className="fas fa-sparkles" /> Choose Your Experience
          </div>
          <h1 className="lp-title">
            Who are <span className="lp-title-accent">you?</span>
          </h1>
          <p className="lp-subtitle" style={{ maxWidth: 560, textAlign: 'center' }}>
            Select your persona to get a tailored smart parking experience.
          </p>
        </div>

        {/* Persona Cards */}
        <div className="persona-cards">
          {PERSONAS.map((p) => (
            <Card3D
              key={p.id}
              icon={p.icon}
              title={p.title}
              subtitle={p.subtitle}
              description={p.description}
              actionLabel={p.actionLabel}
              accentColor={p.accent}
              onAction={p.route ? () => navigate(p.route) : undefined}
            />
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
