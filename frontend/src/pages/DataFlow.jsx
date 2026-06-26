import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import * as API from '../services/api';

export default function DataFlow({ embedded = false }) {
  const [tab, setTab] = useState('realtime');
  const [healthInfo, setHealthInfo] = useState({ status: null, text: 'Checking backend status…' });

  useEffect(() => {
    API.health()
      .then((data) =>
        setHealthInfo({
          status: data.status === 'healthy' ? 'ok' : 'err',
          text: `Backend: ${data.status} · YOLO: ${data.sam_loaded ? 'loaded' : 'not loaded'} · Stream: ${data.stream_active ? 'active' : 'inactive'} · v${data.version}`,
        })
      )
      .catch(() =>
        setHealthInfo({ status: 'err', text: 'Backend unreachable — make sure the service is running.' })
      );
  }, []);

  return (
    <>
      {!embedded && (
        <header className="header">
          <h1><i className="fas fa-project-diagram"></i> Data Flow Architecture</h1>
          <Link to="/" className="nav-link"><i className="fas fa-arrow-left"></i> Back to Dashboard</Link>
        </header>
      )}

      <div className="tabs">
        <div className="tabs__left">
          {['realtime', 'detection', 'forecast'].map((t) => (
            <button
              key={t}
              className={`tab ${tab === t ? 'active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t === 'realtime' ? 'Real-Time Occupancy' : t === 'detection' ? 'YOLOv8 6-Segment Pipeline' : 'Forecasting Pipeline'}
            </button>
          ))}
        </div>
        <div className="tabs__health">
          <span className={`health-dot ${healthInfo.status || ''}`} />
          <span>{healthInfo.text}</span>
        </div>
      </div>

      <main className="content">
        {tab === 'realtime' && <RealtimeTab />}
        {tab === 'detection' && <DetectionTab />}
        {tab === 'forecast' && <ForecastTab />}
      </main>
    </>
  );
}

/* ---------- Sub-tabs ---------- */

function FlowDiagram({ nodes }) {
  return (
    <div className="flow-diagram">
      {nodes.map((n, i) => (
        <React.Fragment key={i}>
          {i > 0 && <i className="fas fa-arrow-right flow-arrow" />}
          <div className={`flow-node ${n.cls || ''}`}>
            <i className={n.icon} />
            <span>{n.label}</span>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

function RealtimeTab() {
  return (
    <div className="flow-container">
      <h2 className="flow-title"><i className="fas fa-broadcast-tower"></i> Real-Time Occupancy Flow</h2>
      <FlowDiagram nodes={[
        { icon: 'fas fa-film', label: 'Local Video' },
        { icon: 'fas fa-camera', label: 'Frame Capture', cls: 'teal' },
        { icon: 'fas fa-th', label: '6-Segment Split', cls: 'purple' },
        { icon: 'fas fa-crosshairs', label: 'YOLOv8 Detection', cls: 'accent' },
        { icon: 'fas fa-server', label: 'FastAPI + WS', cls: 'orange' },
      ]} />
      <div className="stage-section">
        <h3>Pipeline Stages</h3>
        <div className="stage-grid">
          <div className="stage-card"><h4>1. Video Ingestion</h4><p>Local MP4 file (<code>Parking_Lot_Video.mp4</code>) captured via OpenCV at configurable interval</p></div>
          <div className="stage-card teal"><h4>2. Frame Segmentation</h4><p>Each frame is split into a <code>2×3</code> grid (6 segments) with 12% overlap at boundaries for edge coverage</p></div>
          <div className="stage-card purple"><h4>3. Per-Segment Detection</h4><p>YOLOv8m runs independently on each segment at <code>imgsz=1280</code>, boosting effective resolution per vehicle</p></div>
          <div className="stage-card accent"><h4>4. NMS &amp; Zone Assignment</h4><p>Cross-segment NMS (IoU 0.4) deduplicates boundary detections, then vehicles are mapped to 6 zones by centre position</p></div>
        </div>
      </div>
      <div className="tech-specs">
        <div className="tech-item"><i className="fas fa-clock"></i><h5>10 s Update</h5><p>Detection interval</p></div>
        <div className="tech-item"><i className="fas fa-parking"></i><h5>474 Spots</h5><p>Total capacity</p></div>
        <div className="tech-item"><i className="fas fa-bolt"></i><h5>WebSocket</h5><p>Real-time push</p></div>
        <div className="tech-item"><i className="fas fa-database"></i><h5>PostgreSQL</h5><p>History storage</p></div>
      </div>
    </div>
  );
}

function DetectionTab() {
  return (
    <div className="flow-container">
      <h2 className="flow-title"><i className="fas fa-crosshairs"></i> YOLOv8 6-Segment Detection Pipeline</h2>
      <FlowDiagram nodes={[
        { icon: 'fas fa-image', label: 'Input Frame' },
        { icon: 'fas fa-border-all', label: '2×3 Grid Split', cls: 'teal' },
        { icon: 'fas fa-microchip', label: 'YOLOv8m ×6', cls: 'purple' },
        { icon: 'fas fa-compress-arrows-alt', label: 'Coord Remap', cls: 'accent' },
        { icon: 'fas fa-filter', label: 'NMS + Filter', cls: 'orange' },
      ]} />
      <div className="stage-section">
        <h3>Detection Configuration</h3>
        <div className="stage-grid">
          <div className="stage-card"><h4>Model: YOLOv8m</h4><p>Ultralytics YOLOv8 Medium — balanced speed and accuracy, pretrained on COCO with <code>conf=0.15</code></p></div>
          <div className="stage-card teal"><h4>6-Segment Grid</h4><p><code>2 cols × 3 rows</code> with <code>12%</code> overlap padding on each interior edge to avoid splitting vehicles at boundaries</p></div>
          <div className="stage-card purple"><h4>Class Acceptance</h4><p>Accepts <code>car, truck, bus, motorcycle</code> plus common aerial mis-classifications (top-down cars → "cell phone", "suitcase", etc.)</p></div>
          <div className="stage-card accent"><h4>Geometry Filter</h4><p>Area: <code>800–80K px²</code>, min dim: <code>15 px</code>, aspect ratio: <code>&lt;5.0</code>. Relaxed near edges: area ≥300, dim ≥10</p></div>
        </div>
      </div>
      <div className="stage-section">
        <h3>Zone Layout (2×3 Segments)</h3>
        <div className="stage-grid">
          <div className="stage-card"><h4>TL — Top-Left</h4><p>96 parking spaces</p></div>
          <div className="stage-card teal"><h4>TR — Top-Right</h4><p>94 parking spaces</p></div>
          <div className="stage-card purple"><h4>ML — Mid-Left</h4><p>88 parking spaces</p></div>
          <div className="stage-card accent"><h4>MR — Mid-Right</h4><p>90 parking spaces</p></div>
          <div className="stage-card"><h4>BL — Bot-Left</h4><p>52 parking spaces</p></div>
          <div className="stage-card teal"><h4>BR — Bot-Right</h4><p>54 parking spaces</p></div>
        </div>
      </div>
    </div>
  );
}

function ForecastTab() {
  return (
    <div className="flow-container">
      <h2 className="flow-title"><i className="fas fa-chart-line"></i> Forecasting Pipeline</h2>
      <FlowDiagram nodes={[
        { icon: 'fas fa-database', label: 'History DB' },
        { icon: 'fas fa-table', label: 'Feature Extract', cls: 'teal' },
        { icon: 'fas fa-calculator', label: 'Hour Patterns', cls: 'purple' },
        { icon: 'fas fa-eye', label: 'Prediction', cls: 'accent' },
      ]} />
      <div className="stage-section">
        <h3>Forecast Method</h3>
        <div className="stage-grid">
          <div className="stage-card"><h4>Historical Analysis</h4><p>Aggregates occupancy data by hour of day and day of week from PostgreSQL</p></div>
          <div className="stage-card teal"><h4>Pattern Recognition</h4><p>Identifies peak hours (typically 10 AM – 2 PM) and low-traffic periods</p></div>
          <div className="stage-card purple"><h4>Confidence Scoring</h4><p>Based on sample count: High (&gt;100), Medium (50–100), Low (&lt;50)</p></div>
        </div>
      </div>
    </div>
  );
}
