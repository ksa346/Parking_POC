import React from 'react';

export default function AppIcon({ size = 42, className = '' }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={className}
      style={{ flexShrink: 0 }}
    >
      <defs>
        <linearGradient id="sps-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#00D4FF" />
          <stop offset="100%" stopColor="#A855F7" />
        </linearGradient>
        <linearGradient id="sps-p" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#fff" />
          <stop offset="100%" stopColor="#E0F7FF" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="url(#sps-bg)" />
      <text
        x="32"
        y="44"
        textAnchor="middle"
        fontFamily="Inter,system-ui,sans-serif"
        fontWeight="900"
        fontSize="36"
        fill="url(#sps-p)"
      >
        P
      </text>
      <circle cx="48" cy="14" r="6" fill="#86BC25" />
      <path
        d="M45.5 14 L47.2 15.7 L50.5 12.3"
        stroke="#fff"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}
