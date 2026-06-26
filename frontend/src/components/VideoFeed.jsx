import React, { useState, useEffect, useRef } from 'react';

const FRAME_URL = '/api/v1/video/frame';
const REFRESH_MS = 2000; // refresh frame every 2 seconds

export default function VideoFeed({ wsStatus, occupancy }) {
  const isConnected = wsStatus === 'connected';
  const [frameSrc, setFrameSrc] = useState(null);
  const intervalRef = useRef(null);

  useEffect(() => {
    const fetchFrame = () => {
      // Append timestamp to bust browser cache
      setFrameSrc(`${FRAME_URL}?t=${Date.now()}`);
    };

    fetchFrame(); // initial load
    intervalRef.current = setInterval(fetchFrame, REFRESH_MS);

    return () => clearInterval(intervalRef.current);
  }, []);

  return (
    <section className="video-section">
      <div className="video-header">
        <h2><i className="fas fa-video"></i> Live Parking Feed</h2>
        <div className="video-meta">
          <span>{occupancy?.detection_method?.startsWith('yolo:') ? occupancy.detection_method.slice(5).replace(/\.pt$/, '') : (occupancy?.detection_method || 'YOLO Detection')}</span>
          <span className={`status-badge ${isConnected ? 'online' : 'offline'}`}>
            <i className="fas fa-circle" style={{ fontSize: 6 }}></i>{' '}
            {isConnected ? 'Connected' : 'Connecting'}
          </span>
        </div>
      </div>
      <div className="video-container">
        {frameSrc ? (
          <img
            src={frameSrc}
            alt="Parking lot detection feed"
            onError={(e) => {
              e.target.style.display = 'none';
            }}
            onLoad={(e) => {
              e.target.style.display = 'block';
            }}
          />
        ) : (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: '#aaa', fontSize: 18
          }}>
            Waiting for video feed…
          </div>
        )}
      </div>
    </section>
  );
}
