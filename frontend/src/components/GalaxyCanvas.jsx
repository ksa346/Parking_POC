import React, { useCallback, useEffect, useRef } from 'react';

export default function GalaxyCanvas({ mousePos = { x: 0, y: 0 } }) {
  const canvasRef = useRef(null);
  const starsRef = useRef([]);
  const nebulaeRef = useRef([]);
  const frameRef = useRef(0);
  const timeRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const w = window.innerWidth;
    const h = window.innerHeight;
    canvas.width = w;
    canvas.height = h;

    const stars = [];
    for (let i = 0; i < 500; i++) {
      stars.push({
        x: Math.random() * w,
        y: Math.random() * h,
        z: Math.random() * 3 + 0.5,
        radius: Math.random() * 2.2 + 0.3,
        opacity: Math.random() * 0.8 + 0.2,
        speed: Math.random() * 0.4 + 0.05,
        hue: Math.random() < 0.7 ? 195 : Math.random() < 0.5 ? 270 : 140,
      });
    }
    starsRef.current = stars;

    const nebulae = [];
    const hues = [195, 270, 140, 330, 210];
    for (let i = 0; i < 5; i++) {
      nebulae.push({
        x: Math.random() * w,
        y: Math.random() * h,
        radius: Math.random() * 350 + 200,
        hue: hues[i],
        opacity: Math.random() * 0.08 + 0.03,
        drift: Math.random() * 0.3 + 0.1,
      });
    }
    nebulaeRef.current = nebulae;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const handleResize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    const draw = () => {
      const w = canvas.width;
      const h = canvas.height;
      timeRef.current += 0.01;
      const t = timeRef.current;

      ctx.fillStyle = '#05080D';
      ctx.fillRect(0, 0, w, h);

      nebulaeRef.current.forEach((nb) => {
        const px = nb.x + Math.sin(t * nb.drift) * 60 + (mousePos.x - w / 2) * 0.015;
        const py = nb.y + Math.cos(t * nb.drift * 0.7) * 40 + (mousePos.y - h / 2) * 0.015;
        const grad = ctx.createRadialGradient(px, py, 0, px, py, nb.radius);
        grad.addColorStop(0, `hsla(${nb.hue}, 80%, 50%, ${nb.opacity})`);
        grad.addColorStop(0.5, `hsla(${nb.hue}, 60%, 30%, ${nb.opacity * 0.4})`);
        grad.addColorStop(1, `hsla(${nb.hue}, 40%, 10%, 0)`);
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, w, h);
      });

      starsRef.current.forEach((s) => {
        const parallax = s.z * 0.02;
        const sx = s.x + Math.sin(t * s.speed) * 0.5 + (mousePos.x - w / 2) * parallax;
        const sy = s.y + Math.cos(t * s.speed * 0.7) * 0.5 + (mousePos.y - h / 2) * parallax;
        const twinkle = 0.6 + Math.sin(t * 3 + s.x * 0.01) * 0.4;

        ctx.beginPath();
        ctx.arc(sx, sy, s.radius * s.z * 0.5, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${s.hue}, 80%, 80%, ${s.opacity * twinkle})`;
        ctx.fill();

        if (s.radius > 1.5) {
          ctx.beginPath();
          ctx.arc(sx, sy, s.radius * s.z, 0, Math.PI * 2);
          ctx.fillStyle = `hsla(${s.hue}, 80%, 70%, ${s.opacity * twinkle * 0.15})`;
          ctx.fill();
        }
      });

      // Central glow
      const cx = w / 2 + (mousePos.x - w / 2) * 0.03;
      const cy = h * 0.45 + (mousePos.y - h / 2) * 0.03;
      const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, w * 0.5);
      coreGrad.addColorStop(0, 'rgba(0, 212, 255, 0.06)');
      coreGrad.addColorStop(0.3, 'rgba(168, 85, 247, 0.03)');
      coreGrad.addColorStop(0.6, 'rgba(134, 188, 37, 0.015)');
      coreGrad.addColorStop(1, 'transparent');
      ctx.fillStyle = coreGrad;
      ctx.fillRect(0, 0, w, h);

      // Shooting stars
      if (Math.random() < 0.003) {
        const sx2 = Math.random() * w;
        const sy2 = Math.random() * h * 0.5;
        const len = Math.random() * 120 + 60;
        const angle = Math.PI * 0.15 + Math.random() * 0.3;
        const grad2 = ctx.createLinearGradient(
          sx2, sy2,
          sx2 + Math.cos(angle) * len,
          sy2 + Math.sin(angle) * len
        );
        grad2.addColorStop(0, 'rgba(255, 255, 255, 0.9)');
        grad2.addColorStop(1, 'rgba(0, 212, 255, 0)');
        ctx.strokeStyle = grad2;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(sx2, sy2);
        ctx.lineTo(sx2 + Math.cos(angle) * len, sy2 + Math.sin(angle) * len);
        ctx.stroke();
      }

      frameRef.current = requestAnimationFrame(draw);
    };

    frameRef.current = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frameRef.current);
      window.removeEventListener('resize', handleResize);
    };
  }, [mousePos]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        zIndex: 0,
        pointerEvents: 'none',
      }}
    />
  );
}
