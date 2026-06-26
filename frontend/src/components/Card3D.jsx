import React, { useRef, useState } from 'react';
import { motion } from 'framer-motion';

export default function Card3D({ icon, title, subtitle, description, actionLabel, onAction, accentColor, children }) {
  const cardRef = useRef(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [isHovered, setIsHovered] = useState(false);

  const handleMouseMove = (e) => {
    if (!cardRef.current) return;
    const rect = cardRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left - rect.width / 2) / (rect.width / 2);
    const y = (e.clientY - rect.top - rect.height / 2) / (rect.height / 2);
    setMousePos({ x, y });
  };

  const rotateX = isHovered ? mousePos.y * -15 : 0;
  const rotateY = isHovered ? mousePos.x * 15 : 0;
  const glowX = isHovered ? 50 + mousePos.x * 30 : 50;
  const glowY = isHovered ? 50 + mousePos.y * 30 : 50;

  return (
    <motion.div
      ref={cardRef}
      className="card3d"
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => { setIsHovered(false); setMousePos({ x: 0, y: 0 }); }}
      onClick={onAction}
      style={{
        '--accent': accentColor || '#00D4FF',
        perspective: '1200px',
      }}
      initial={{ opacity: 0, y: 40 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
    >
      <motion.div
        className="card3d__inner"
        animate={{
          rotateX,
          rotateY,
          scale: isHovered ? 1.04 : 1,
        }}
        transition={{ type: 'spring', stiffness: 300, damping: 25 }}
        style={{ transformStyle: 'preserve-3d' }}
      >
        {/* Glow layer */}
        <div
          className="card3d__glow"
          style={{
            opacity: isHovered ? 1 : 0,
            background: `radial-gradient(circle at ${glowX}% ${glowY}%, ${accentColor || '#00D4FF'}33 0%, transparent 60%)`,
          }}
        />

        {/* Reflection */}
        <div
          className="card3d__reflection"
          style={{
            opacity: isHovered ? 0.12 : 0,
            background: `linear-gradient(${135 + mousePos.y * 40}deg, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0.05) 50%, transparent 50.1%)`,
          }}
        />

        {/* Content */}
        <motion.div
          className="card3d__content"
          style={{
            translateZ: isHovered ? 40 : 0,
            translateX: isHovered ? mousePos.x * 8 : 0,
            translateY: isHovered ? mousePos.y * 8 : 0,
          }}
        >
          {icon && (
            <div className="card3d__icon" style={{ color: accentColor || '#00D4FF' }}>
              <i className={icon} />
            </div>
          )}
          {children}
          {title && <h3 className="card3d__title">{title}</h3>}
          {subtitle && <p className="card3d__subtitle">{subtitle}</p>}
          {description && <p className="card3d__desc">{description}</p>}
          {actionLabel && (
            <span className="card3d__action" style={{ '--accent': accentColor || '#00D4FF' }}>
              {actionLabel} <i className="fas fa-arrow-right" />
            </span>
          )}
        </motion.div>

        {/* Border glow */}
        <div
          className="card3d__border"
          style={{
            boxShadow: isHovered
              ? `0 0 0 1px ${accentColor || '#00D4FF'}44, 0 20px 60px -10px ${accentColor || '#00D4FF'}30`
              : '0 0 0 1px rgba(255,255,255,0.06)',
          }}
        />
      </motion.div>
    </motion.div>
  );
}
