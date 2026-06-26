import React, { useCallback, useEffect, useRef, useState } from 'react';
import createGlobe from 'cobe';

const GLOBE_CONFIG = {
  width: 800,
  height: 800,
  onRender: () => {},
  devicePixelRatio: 2,
  phi: 0,
  theta: 0.3,
  dark: 1,
  diffuse: 1.2,
  mapSamples: 16000,
  mapBrightness: 6,
  baseColor: [0.3, 0.3, 0.3],
  markerColor: [0, 0.83, 1],           // #00D4FF cyan
  glowColor: [0.05, 0.2, 0.35],
  markers: [
    { location: [40.2484, -77.0239], size: 0.12 },   // Mechanicsburg, PA (Walmart)
    { location: [40.7128, -74.006],  size: 0.08 },    // New York
    { location: [34.0522, -118.2437], size: 0.08 },   // LA
    { location: [51.5074, -0.1278],  size: 0.06 },    // London
    { location: [35.6762, 139.6503], size: 0.06 },    // Tokyo
    { location: [19.076, 72.8777],   size: 0.07 },    // Mumbai
    { location: [-23.5505, -46.6333], size: 0.06 },   // São Paulo
    { location: [1.3521, 103.8198],  size: 0.05 },    // Singapore
    { location: [48.8566, 2.3522],   size: 0.05 },    // Paris
    { location: [55.7558, 37.6173],  size: 0.05 },    // Moscow
  ],
};

export default function Globe({ className = '', config = GLOBE_CONFIG }) {
  let phi = 0;
  let width = 0;
  const canvasRef = useRef(null);
  const pointerInteracting = useRef(null);
  const pointerInteractionMovement = useRef(0);
  const [r, setR] = useState(0);

  const updatePointerInteraction = (value) => {
    pointerInteracting.current = value;
    if (canvasRef.current) {
      canvasRef.current.style.cursor = value !== null ? 'grabbing' : 'grab';
    }
  };

  const updateMovement = (clientX) => {
    if (pointerInteracting.current !== null) {
      const delta = clientX - pointerInteracting.current;
      pointerInteractionMovement.current = delta;
      setR(delta / 200);
    }
  };

  const onRender = useCallback(
    (state) => {
      if (!pointerInteracting.current) phi += 0.005;
      state.phi = phi + r;
      state.width = width * 2;
      state.height = width * 2;
    },
    [r],
  );

  const onResize = () => {
    if (canvasRef.current) {
      width = canvasRef.current.offsetWidth;
    }
  };

  useEffect(() => {
    window.addEventListener('resize', onResize);
    onResize();

    const globe = createGlobe(canvasRef.current, {
      ...config,
      width: width * 2,
      height: width * 2,
      onRender,
    });

    setTimeout(() => {
      if (canvasRef.current) canvasRef.current.style.opacity = '1';
    });
    return () => {
      globe.destroy();
      window.removeEventListener('resize', onResize);
    };
  }, []);

  return (
    <div
      className={className}
      style={{
        position: 'relative',
        width: '100%',
        maxWidth: 600,
        aspectRatio: '1 / 1',
        margin: '0 auto',
      }}
    >
      <canvas
        ref={canvasRef}
        onPointerDown={(e) =>
          updatePointerInteraction(e.clientX - pointerInteractionMovement.current)
        }
        onPointerUp={() => updatePointerInteraction(null)}
        onPointerOut={() => updatePointerInteraction(null)}
        onMouseMove={(e) => updateMovement(e.clientX)}
        onTouchMove={(e) => e.touches[0] && updateMovement(e.touches[0].clientX)}
        style={{
          width: '100%',
          height: '100%',
          contain: 'layout paint size',
          opacity: 0,
          transition: 'opacity 0.6s ease',
          cursor: 'grab',
        }}
      />
    </div>
  );
}
