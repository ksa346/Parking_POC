import React, { useRef, useEffect, useState } from 'react';

const VIDEO_STREAM_URL = '/api/v1/video/stream';

export default function LiveFeed({ wsStatus }) {
  const isConnected = wsStatus === 'connected';
  const videoRef = useRef(null);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const handlePause = () => {
      if (!video.ended) video.play().catch(() => {});
    };
    video.addEventListener('pause', handlePause);
    return () => video.removeEventListener('pause', handlePause);
  }, []);

  return (
    <section className="video-section">
      <div className="video-header">
        <h2><i className="fas fa-video"></i> Live Parking Feed</h2>
        <div className="video-meta">
          <span>Raw Camera Feed</span>
          <span className={`status-badge ${isConnected ? 'online' : 'offline'}`}>
            <i className="fas fa-circle" style={{ fontSize: 6 }}></i>{' '}
            {isConnected ? 'Live' : 'Connecting'}
          </span>
        </div>
      </div>
      <div className="video-container">
        {!hasError ? (
          <video
            ref={videoRef}
            src={VIDEO_STREAM_URL}
            autoPlay
            loop
            muted
            playsInline
            onError={() => setHasError(true)}
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
          />
        ) : (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: '#8B949E', fontSize: 18
          }}>
            Unable to load video feed
          </div>
        )}
      </div>
    </section>
  );
}
