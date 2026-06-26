import React from 'react';

export default function AuroraBackground() {
  return (
    <div className="aurora" aria-hidden="true">
      {/* Base gradient wash */}
      <div className="aurora__base" />

      {/* Animated radial waves */}
      <div className="aurora__wave aurora__wave--1" />
      <div className="aurora__wave aurora__wave--2" />
      <div className="aurora__wave aurora__wave--3" />
      <div className="aurora__wave aurora__wave--4" />

      {/* Depth overlay — fades edges to black */}
      <div className="aurora__vignette" />
    </div>
  );
}
