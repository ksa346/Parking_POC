import React, { useState, useEffect } from 'react';

export default function ThemeToggle() {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('theme');
    return saved ? saved === 'dark' : true;
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }, [dark]);

  return (
    <div className="theme-toggle">
      <i className="fas fa-sun" style={{ fontSize: '0.75rem', color: dark ? 'var(--g400)' : '#ED8B00' }} />
      <button
        className="theme-toggle__track"
        role="switch"
        aria-checked={dark}
        aria-label="Toggle dark mode"
        onClick={() => setDark(prev => !prev)}
      >
        <span className="theme-toggle__thumb">
          {dark ? '🌙' : '☀️'}
        </span>
      </button>
      <i className="fas fa-moon" style={{ fontSize: '0.75rem', color: dark ? '#A855F7' : 'var(--g400)' }} />
    </div>
  );
}
