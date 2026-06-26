import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import AppIcon from '../components/AppIcon';
import AuroraBackground from '../components/AuroraBackground';
import ThemeToggle from '../components/ThemeToggle';
import YoloDatasetTool from '../components/YoloDatasetTool';
import {
  uploadVideo,
  captureFrame,
  estimateSpots,
  previewDetection,
  publishLocation,
  listTrainedModels,
} from '../services/api';

const STEPS = [
  { id: 'source', label: 'Video Source', icon: 'fas fa-video' },
  { id: 'frame', label: 'Capture Frame', icon: 'fas fa-camera' },
  { id: 'grid', label: 'Segment Grid', icon: 'fas fa-th' },
  { id: 'spots', label: 'Detect Spots', icon: 'fas fa-brain' },
  { id: 'preview', label: 'Preview & Tune', icon: 'fas fa-sliders-h' },
  { id: 'publish', label: 'Publish', icon: 'fas fa-rocket' },
];

const DEFAULT_PARAMS = {
  confidence_threshold: 0.15,
  nms_iou_threshold: 0.4,
  segment_overlap: 0.12,
  min_vehicle_area: 800,
  max_vehicle_area: 80000,
  user_prompt: '',
};

const clamp = (value, min, max, fallback) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
};

const toNonNegativeInt = (value, fallback = 0) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.trunc(n));
};

const clampAngle = (value, min = -30, max = 30, fallback = 0) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
};

const formatDuration = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  return `${mins}:${String(secs).padStart(2, '0')}`;
};

const readVideoDuration = (file) => new Promise((resolve) => {
  if (!file) {
    resolve(null);
    return;
  }
  const video = document.createElement('video');
  const objectUrl = URL.createObjectURL(file);
  video.preload = 'metadata';
  video.onloadedmetadata = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : null;
    URL.revokeObjectURL(objectUrl);
    resolve(duration);
  };
  video.onerror = () => {
    URL.revokeObjectURL(objectUrl);
    resolve(null);
  };
  video.src = objectUrl;
});

const sortLineAnglePairs = (lines, angles) => {
  const pairs = lines.map((line, index) => ({ line, angle: Number(angles[index] ?? 0) || 0 }));
  pairs.sort((a, b) => a.line - b.line);
  return {
    lines: pairs.map(p => p.line),
    angles: pairs.map(p => p.angle),
  };
};

async function downscalePreviewFrame(frameB64, maxWidth = 1280, quality = 0.78) {
  if (!frameB64) return frameB64;
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      if (img.width <= maxWidth) {
        resolve(frameB64);
        return;
      }

      const scale = maxWidth / img.width;
      const width = Math.round(img.width * scale);
      const height = Math.round(img.height * scale);

      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        resolve(frameB64);
        return;
      }

      ctx.drawImage(img, 0, 0, width, height);
      const dataUrl = canvas.toDataURL('image/jpeg', quality);
      const compressed = dataUrl.split(',')[1] || frameB64;
      resolve(compressed);
    };
    img.onerror = () => resolve(frameB64);
    img.src = `data:image/jpeg;base64,${frameB64}`;
  });
}

export default function DeveloperSetup() {
  const navigate = useNavigate();
  const [devTab, setDevTab] = useState('setup'); // 'setup' | 'yolo'
  const [step, setStep] = useState(0);

  // Step 1 — Video source
  const [sourceType, setSourceType] = useState('upload'); // 'upload' | 'url'
  const [videoUrl, setVideoUrl] = useState('');
  const [videoFile, setVideoFile] = useState(null);        // File object for upload
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState(false);          // Upload success flag
  const [uploadedFilename, setUploadedFilename] = useState(''); // server-assigned filename
  const fileInputRef = useRef(null);

  // Step 2 — Captured frame
  const [frameB64, setFrameB64] = useState(null);
  const [frameDims, setFrameDims] = useState({ w: 0, h: 0 });
  const [capturing, setCapturing] = useState(false);
  const [captureTimestamp, setCaptureTimestamp] = useState(0);
  const [captureDuration, setCaptureDuration] = useState(null);
  const [capturedAt, setCapturedAt] = useState(0);

  // Step 3 — Grid lines + border
  const [hLines, setHLines] = useState([0.333, 0.666]);
  const [vLines, setVLines] = useState([0.5]);
  const [hLineAngles, setHLineAngles] = useState([0, 0]);
  const [vLineAngles, setVLineAngles] = useState([0]);
  const [border, setBorder] = useState({ top: 0, right: 0, bottom: 0, left: 0 });
  const [excludeRegions, setExcludeRegions] = useState([]); // [[ [xFrac,yFrac], ... ], ...]
  const [draftExcludeRegion, setDraftExcludeRegion] = useState([]); // [ [xFrac,yFrac], ... ]
  const [gridTool, setGridTool] = useState('lines'); // 'lines' | 'exclude'
  const canvasRef = useRef(null);
  const frameImgRef = useRef(null); // Pre-loaded Image object
  const [dragging, setDragging] = useState(null); // { axis: 'h'|'v', index }

  // Step 4 — GPT zone estimation
  const [zones, setZones] = useState([]);
  const [totalSpots, setTotalSpots] = useState(0);
  const [estimating, setEstimating] = useState(false);
  const [zoneModal, setZoneModal] = useState(null); // zone object for info modal

  // Step 5 — Preview + parameters
  const [params, setParams] = useState({ ...DEFAULT_PARAMS });
  const [previewB64, setPreviewB64] = useState(null);
  const [vehicleCount, setVehicleCount] = useState(0);
  const [previewing, setPreviewing] = useState(false);

  // Step 6 — Publish
  const [locationName, setLocationName] = useState('');
  const [googleMapsUrl, setGoogleMapsUrl] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [published, setPublished] = useState(null);

  // Loading / error
  const [error, setError] = useState('');

  // Model picker — wizard-local only, no global API mutation
  const [trainedModels, setTrainedModels] = useState([]);
  const [selectedModelPath, setSelectedModelPath] = useState(''); // empty = user hasn't chosen yet
  const loadModels = useCallback(async () => {
    try { setTrainedModels(await listTrainedModels(true)); } catch (_) {}
  }, []);
  useEffect(() => { loadModels(); }, [loadModels]);

  useEffect(() => {
    document.title = 'Developer Setup — Smart Parking Solution';
  }, []);

  // ── Step navigation ─────────────────────────────────────────────
  const canGoNext = () => {
    if (step === 0) return (sourceType === 'upload' && uploaded) || (sourceType === 'url' && videoUrl.trim().length > 0);
    if (step === 1) return !!frameB64;
    if (step === 2) return true;
    if (step === 3) return zones.length > 0;
    if (step === 4) return true;
    if (step === 5) return !!published;
    return false;
  };

  const next = () => { if (step < STEPS.length - 1) { setError(''); setStep(s => s + 1); } };
  const prev = () => { if (step > 0) { setError(''); setStep(s => s - 1); } };

  // ── Step 1: Upload video ─────────────────────────────────────────
  const handleUpload = async () => {
    if (!videoFile) return;
    setUploading(true);
    setError('');
    try {
      const res = await uploadVideo(videoFile);
      setUploaded(true);
      // Store the server-assigned filename so publish can record the exact file
      setUploadedFilename(res?.filename || videoFile.name);
    } catch (e) {
      setError(e.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    if (!videoFile) {
      setCaptureDuration(null);
      setCaptureTimestamp(0);
      return;
    }
    readVideoDuration(videoFile).then((duration) => {
      if (cancelled) return;
      setCaptureDuration(duration);
      setCaptureTimestamp((prev) => {
        if (!Number.isFinite(duration) || duration === null) return prev;
        return Math.min(Math.max(0, prev), duration);
      });
    });
    return () => {
      cancelled = true;
    };
  }, [videoFile]);

  // ── Step 2: Capture frame ───────────────────────────────────────
  const handleCapture = async () => {
    setCapturing(true);
    setError('');
    try {
      const requestedTs = Math.max(0, Number(captureTimestamp) || 0);
      const res = await captureFrame(
        sourceType === 'url' ? videoUrl : '',
        sourceType === 'upload',
        requestedTs,
      );
      setFrameB64(res.frame_base64);
      setFrameDims({ w: res.width, h: res.height });
      setCapturedAt(Number(res.captured_at_seconds) || requestedTs);
      if (Number.isFinite(Number(res.duration_seconds))) {
        setCaptureDuration(Number(res.duration_seconds));
      }
      next();
    } catch (e) {
      setError(e.message || 'Failed to capture frame');
    } finally {
      setCapturing(false);
    }
  };

  // ── Step 3: Pre-load frame image once ────────────────────────────
  useEffect(() => {
    if (!frameB64) { frameImgRef.current = null; return; }
    const img = new Image();
    img.onload = () => { frameImgRef.current = img; };
    img.src = `data:image/jpeg;base64,${frameB64}`;
  }, [frameB64]);

  // ── Step 3: Draw grid on canvas (synchronous — no async image load) ─
  const drawGrid = useCallback(() => {
    const canvas = canvasRef.current;
    const img = frameImgRef.current;
    if (!canvas || !img) return;

    const ctx = canvas.getContext('2d');

    // Use a fixed display size so lines are always clearly visible.
    // Scale factor keeps everything proportional.
    const DISPLAY_W = 800;
    const scale = DISPLAY_W / img.width;
    const DISPLAY_H = Math.round(img.height * scale);

    canvas.width = DISPLAY_W;
    canvas.height = DISPLAY_H;
    ctx.drawImage(img, 0, 0, DISPLAY_W, DISPLAY_H);

    const w = DISPLAY_W;
    const h = DISPLAY_H;

    // Border region (dim outside)
    const bt = h * border.top / 100;
    const br_ = w * border.right / 100;
    const bb = h * border.bottom / 100;
    const bl = w * border.left / 100;

    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    if (bt > 0) ctx.fillRect(0, 0, w, bt);
    if (bb > 0) ctx.fillRect(0, h - bb, w, bb);
    if (bl > 0) ctx.fillRect(0, bt, bl, h - bt - bb);
    if (br_ > 0) ctx.fillRect(w - br_, bt, br_, h - bt - bb);

    // Active region
    const ax1 = bl, ay1 = bt, ax2 = w - br_, ay2 = h - bb;
    const aw = ax2 - ax1;
    const ah = ay2 - ay1;
    const cxMid = (ax1 + ax2) / 2;
    const cyMid = (ay1 + ay2) / 2;
    const drawHLine = (frac, angleDeg = 0) => {
      const hTan = Math.tan((angleDeg * Math.PI) / 180);
      const y0 = ay1 + ah * frac;
      const yL = y0 + hTan * (ax1 - cxMid);
      const yR = y0 + hTan * (ax2 - cxMid);
      return { x1: ax1, y1: yL, x2: ax2, y2: yR };
    };

    const drawVLine = (frac, angleDeg = 0) => {
      const vTan = Math.tan((angleDeg * Math.PI) / 180);
      const x0 = ax1 + aw * frac;
      const xT = x0 + vTan * (ay1 - cyMid);
      const xB = x0 + vTan * (ay2 - cyMid);
      return { x1: xT, y1: ay1, x2: xB, y2: ay2 };
    };

    const intersectHV = (hf, vf, hAngleDeg = 0, vAngleDeg = 0) => {
      const y0 = ay1 + ah * hf;
      const x0 = ax1 + aw * vf;
      const hTan = Math.tan((hAngleDeg * Math.PI) / 180);
      const vTan = Math.tan((vAngleDeg * Math.PI) / 180);
      // y = y0 + hTan*(x-cxMid)
      // x = x0 + vTan*(y-cyMid)
      const a1 = -hTan;
      const b1 = 1;
      const c1 = y0 - hTan * cxMid;
      const a2 = 1;
      const b2 = -vTan;
      const c2 = x0 - vTan * cyMid;
      const det = a1 * b2 - a2 * b1;
      if (Math.abs(det) < 1e-6) {
        return {
          x: ax1 + aw * vf,
          y: ay1 + ah * hf,
        };
      }
      const x = (c1 * b2 - c2 * b1) / det;
      const y = (a1 * c2 - a2 * c1) / det;
      return { x, y };
    };

    // ── Horizontal lines (cyan, thick, dashed) ──
    hLines.forEach((frac, idx) => {
      const p = drawHLine(frac, hLineAngles[idx] || 0);
      // Glow shadow
      ctx.save();
      ctx.strokeStyle = 'rgba(0, 212, 255, 0.5)';
      ctx.lineWidth = 6;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(p.x1, p.y1);
      ctx.lineTo(p.x2, p.y2);
      ctx.stroke();
      ctx.restore();
      // Main line
      ctx.strokeStyle = '#00D4FF';
      ctx.lineWidth = 3;
      ctx.setLineDash([12, 8]);
      ctx.beginPath();
      ctx.moveTo(p.x1, p.y1);
      ctx.lineTo(p.x2, p.y2);
      ctx.stroke();
    });

    // ── Vertical lines (purple, thick, dashed) ──
    vLines.forEach((frac, idx) => {
      const p = drawVLine(frac, vLineAngles[idx] || 0);
      // Glow shadow
      ctx.save();
      ctx.strokeStyle = 'rgba(168, 85, 247, 0.5)';
      ctx.lineWidth = 6;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(p.x1, p.y1);
      ctx.lineTo(p.x2, p.y2);
      ctx.stroke();
      ctx.restore();
      // Main line
      ctx.strokeStyle = '#A855F7';
      ctx.lineWidth = 3;
      ctx.setLineDash([12, 8]);
      ctx.beginPath();
      ctx.moveTo(p.x1, p.y1);
      ctx.lineTo(p.x2, p.y2);
      ctx.stroke();
    });
    ctx.setLineDash([]);

    // Zone labels
    const rows = hLines.length + 1;
    const cols = vLines.length + 1;
    const rowLabels = _rowLabels(rows);
    const colLabels = _colLabels(cols);

    const hBounds = [0, ...hLines, 1];
    const vBounds = [0, ...vLines, 1];

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const hCenter = (hBounds[r] + hBounds[r + 1]) / 2;
        const vCenter = (vBounds[c] + vBounds[c + 1]) / 2;
        const hAngTop = r > 0 ? (hLineAngles[r - 1] || 0) : 0;
        const hAngBottom = r < hLines.length ? (hLineAngles[r] || 0) : 0;
        const vAngLeft = c > 0 ? (vLineAngles[c - 1] || 0) : 0;
        const vAngRight = c < vLines.length ? (vLineAngles[c] || 0) : 0;
        const hAngCenter = (hAngTop + hAngBottom) / 2;
        const vAngCenter = (vAngLeft + vAngRight) / 2;
        const ip = intersectHV(hCenter, vCenter, hAngCenter, vAngCenter);
        const label = `${rowLabels[r]}${colLabels[c]}`;
        // Background pill
        ctx.font = 'bold 16px Inter, sans-serif';
        const tm = ctx.measureText(label);
        const pw = tm.width + 16;
        const ph = 26;
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.beginPath();
        ctx.roundRect(ip.x - pw / 2, ip.y - ph / 2, pw, ph, 6);
        ctx.fill();
        // Text
        ctx.fillStyle = '#fff';
        ctx.fillText(label, ip.x, ip.y);
      }
    }

    // Border outline (magenta, solid, thick)
    ctx.strokeStyle = '#FF4D6A';
    ctx.lineWidth = 4;
    ctx.setLineDash([]);
    ctx.shadowColor = 'rgba(255, 77, 106, 0.6)';
    ctx.shadowBlur = 8;
    ctx.strokeRect(ax1, ay1, ax2 - ax1, ay2 - ay1);
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;

    // Exclusion regions overlay (semi-transparent red)
    const drawPoly = (region, isDraft = false) => {
      if (!region || region.length < 2) return;
      const pts = region.map(([fx, fy]) => [ax1 + aw * fx, ay1 + ah * fy]);
      if (pts.length >= 3) {
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
        ctx.closePath();
        ctx.fillStyle = isDraft ? 'rgba(255, 77, 106, 0.20)' : 'rgba(255, 77, 106, 0.28)';
        ctx.fill();
        ctx.strokeStyle = '#FF4D6A';
        ctx.lineWidth = isDraft ? 2 : 3;
        ctx.setLineDash(isDraft ? [8, 6] : []);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
        ctx.strokeStyle = '#FF4D6A';
        ctx.lineWidth = 2;
        ctx.setLineDash([8, 6]);
        ctx.stroke();
      }

      ctx.setLineDash([]);
      for (const [px, py] of pts) {
        ctx.beginPath();
        ctx.arc(px, py, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#FF4D6A';
        ctx.fill();
      }
    };

    excludeRegions.forEach((region) => drawPoly(region, false));
    drawPoly(draftExcludeRegion, true);
  }, [
    frameB64,
    hLines,
    vLines,
    hLineAngles,
    vLineAngles,
    border,
    excludeRegions,
    draftExcludeRegion,
  ]);

  // Redraw whenever step enters grid view, or any grid state changes
  useEffect(() => {
    if (step === 2) {
      // Small delay to ensure canvas is mounted after AnimatePresence transition
      const id = requestAnimationFrame(() => drawGrid());
      return () => cancelAnimationFrame(id);
    }
  }, [step, drawGrid]);

  // Canvas mouse interaction for dragging lines
  const handleCanvasMouseDown = (e) => {
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleX = canvasRef.current.width / rect.width;
    const scaleY = canvasRef.current.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;

    const w = canvasRef.current.width;
    const h = canvasRef.current.height;
    const bt = h * border.top / 100;
    const br_ = w * border.right / 100;
    const bb = h * border.bottom / 100;
    const bl = w * border.left / 100;
    const ax1 = bl, ay1 = bt, ax2 = w - br_, ay2 = h - bb;
    const aw = ax2 - ax1;
    const ah = ay2 - ay1;
    const cxMid = (ax1 + ax2) / 2;
    const cyMid = (ay1 + ay2) / 2;

    if (gridTool === 'exclude') {
      if (mx < ax1 || mx > ax2 || my < ay1 || my > ay2) return;
      const fx = Math.min(1, Math.max(0, (mx - ax1) / Math.max(1, aw)));
      const fy = Math.min(1, Math.max(0, (my - ay1) / Math.max(1, ah)));
      setDraftExcludeRegion(prev => [...prev, [fx, fy]]);
      return;
    }

    const distToSegment = (px, py, x1, y1, x2, y2) => {
      const vx = x2 - x1;
      const vy = y2 - y1;
      const wx = px - x1;
      const wy = py - y1;
      const len2 = vx * vx + vy * vy;
      const t = len2 > 0 ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / len2)) : 0;
      const qx = x1 + t * vx;
      const qy = y1 + t * vy;
      return Math.hypot(px - qx, py - qy);
    };

    const SNAP = 20;
    for (let i = 0; i < hLines.length; i++) {
      const hTan = Math.tan(((hLineAngles[i] || 0) * Math.PI) / 180);
      const y0 = ay1 + ah * hLines[i];
      const yL = y0 + hTan * (ax1 - cxMid);
      const yR = y0 + hTan * (ax2 - cxMid);
      if (distToSegment(mx, my, ax1, yL, ax2, yR) < SNAP) {
        setDragging({ axis: 'h', index: i });
        return;
      }
    }
    for (let i = 0; i < vLines.length; i++) {
      const vTan = Math.tan(((vLineAngles[i] || 0) * Math.PI) / 180);
      const x0 = ax1 + aw * vLines[i];
      const xT = x0 + vTan * (ay1 - cyMid);
      const xB = x0 + vTan * (ay2 - cyMid);
      if (distToSegment(mx, my, xT, ay1, xB, ay2) < SNAP) {
        setDragging({ axis: 'v', index: i });
        return;
      }
    }
  };

  const handleCanvasMouseMove = (e) => {
    if (!dragging || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleX = canvasRef.current.width / rect.width;
    const scaleY = canvasRef.current.height / rect.height;

    const w = canvasRef.current.width;
    const h = canvasRef.current.height;
    const bt = h * border.top / 100;
    const br_ = w * border.right / 100;
    const bb = h * border.bottom / 100;
    const bl = w * border.left / 100;
    const ax1 = bl, ay1 = bt, ax2 = w - br_, ay2 = h - bb;
    const aw = ax2 - ax1;
    const ah = ay2 - ay1;
    const cxMid = (ax1 + ax2) / 2;
    const cyMid = (ay1 + ay2) / 2;

    if (dragging.axis === 'h') {
      const my = (e.clientY - rect.top) * scaleY;
      const mx = (e.clientX - rect.left) * scaleX;
      const hTan = Math.tan(((hLineAngles[dragging.index] || 0) * Math.PI) / 180);
      const yAdj = my - hTan * (mx - cxMid);
      const frac = Math.min(0.95, Math.max(0.05, (yAdj - ay1) / ah));
      setHLines(prev => {
        const n = [...prev];
        n[dragging.index] = frac;
        const sorted = sortLineAnglePairs(n, hLineAngles);
        setHLineAngles(sorted.angles);
        return sorted.lines;
      });
    } else {
      const mx = (e.clientX - rect.left) * scaleX;
      const my = (e.clientY - rect.top) * scaleY;
      const vTan = Math.tan(((vLineAngles[dragging.index] || 0) * Math.PI) / 180);
      const xAdj = mx - vTan * (my - cyMid);
      const frac = Math.min(0.95, Math.max(0.05, (xAdj - ax1) / aw));
      setVLines(prev => {
        const n = [...prev];
        n[dragging.index] = frac;
        const sorted = sortLineAnglePairs(n, vLineAngles);
        setVLineAngles(sorted.angles);
        return sorted.lines;
      });
    }
  };

  const handleCanvasMouseUp = () => setDragging(null);

  const finishExcludeRegion = () => {
    if (draftExcludeRegion.length < 3) return;
    setExcludeRegions(prev => [...prev, draftExcludeRegion]);
    setDraftExcludeRegion([]);
  };

  const cancelExcludeRegion = () => {
    setDraftExcludeRegion([]);
  };

  const removeLastExcludeRegion = () => {
    setExcludeRegions(prev => prev.slice(0, -1));
  };

  const clearExcludeRegions = () => {
    setExcludeRegions([]);
    setDraftExcludeRegion([]);
  };

  // ── Step 4: GPT Estimation ──────────────────────────────────────
  const handleEstimate = async () => {
    setEstimating(true);
    setError('');
    try {
      const res = await estimateSpots(
        {
          horizontal_lines: hLines,
          vertical_lines: vLines,
          horizontal_line_angles: hLineAngles,
          vertical_line_angles: vLineAngles,
          exclude_regions: excludeRegions,
          horizontal_angle_deg: hLineAngles.length ? (hLineAngles.reduce((a, b) => a + b, 0) / hLineAngles.length) : 0,
          vertical_angle_deg: vLineAngles.length ? (vLineAngles.reduce((a, b) => a + b, 0) / vLineAngles.length) : 0,
          border,
        },
        frameB64,
      );
      setZones(res.zones);
      setTotalSpots(res.total_spots);
    } catch (e) {
      setError(e.message || 'Estimation failed');
    } finally {
      setEstimating(false);
    }
  };

  const updateZoneSpots = (idx, val) => {
    const n = [...zones];
    n[idx] = { ...n[idx], user_spots: Math.max(0, parseInt(val) || 0) };
    setZones(n);
    setTotalSpots(n.reduce((s, z) => s + z.user_spots, 0));
  };

  // ── Step 5: Preview ─────────────────────────────────────────────
  const handlePreview = async () => {
    if (!selectedModelPath) {
      setError('Select a model before running preview');
      return;
    }
    setPreviewing(true);
    setError('');
    try {
      const previewFrameB64 = await downscalePreviewFrame(frameB64);
      const safeParams = {
        confidence_threshold: clamp(params.confidence_threshold, 0.01, 1.0, DEFAULT_PARAMS.confidence_threshold),
        nms_iou_threshold: clamp(params.nms_iou_threshold, 0.1, 1.0, DEFAULT_PARAMS.nms_iou_threshold),
        segment_overlap: clamp(params.segment_overlap, 0.0, 0.5, DEFAULT_PARAMS.segment_overlap),
        min_vehicle_area: Math.max(100, toNonNegativeInt(params.min_vehicle_area, DEFAULT_PARAMS.min_vehicle_area)),
        max_vehicle_area: Math.max(1000, toNonNegativeInt(params.max_vehicle_area, DEFAULT_PARAMS.max_vehicle_area)),
        user_prompt: (params.user_prompt || '').toString().slice(0, 1000),
        model_path: selectedModelPath,
      };
      const gridConfig = {
        horizontal_lines: hLines,
        vertical_lines: vLines,
        horizontal_line_angles: hLineAngles,
        vertical_line_angles: vLineAngles,
        exclude_regions: excludeRegions,
        horizontal_angle_deg: hLineAngles.length ? (hLineAngles.reduce((a, b) => a + b, 0) / hLineAngles.length) : 0,
        vertical_angle_deg: vLineAngles.length ? (vLineAngles.reduce((a, b) => a + b, 0) / vLineAngles.length) : 0,
        border,
      };
      const res = await previewDetection(previewFrameB64, safeParams, gridConfig);
      setPreviewB64(res.annotated_frame_base64);
      setVehicleCount(res.vehicle_count);
    } catch (e) {
      setError(e.message || 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  };

  // ── Step 6: Publish ─────────────────────────────────────────────
  const handlePublish = async () => {
    if (!locationName.trim()) { setError('Location name is required'); return; }
    setPublishing(true);
    setError('');
    try {
      const activeModel = trainedModels.find(m => m.path === selectedModelPath);
      const safeParams = {
        confidence_threshold: clamp(params.confidence_threshold, 0.01, 1.0, DEFAULT_PARAMS.confidence_threshold),
        nms_iou_threshold: clamp(params.nms_iou_threshold, 0.1, 1.0, DEFAULT_PARAMS.nms_iou_threshold),
        segment_overlap: clamp(params.segment_overlap, 0.0, 0.5, DEFAULT_PARAMS.segment_overlap),
        min_vehicle_area: Math.max(100, toNonNegativeInt(params.min_vehicle_area, DEFAULT_PARAMS.min_vehicle_area)),
        max_vehicle_area: Math.max(1000, toNonNegativeInt(params.max_vehicle_area, DEFAULT_PARAMS.max_vehicle_area)),
        user_prompt: (params.user_prompt || '').toString().slice(0, 1000),
        model_path: selectedModelPath || '',   // empty = no explicit model chosen; backend keeps current
      };

      const safeZones = zones.map(z => ({
        zone_id: String(z.zone_id || ''),
        estimated_spots: toNonNegativeInt(z.estimated_spots),
        user_spots: toNonNegativeInt(z.user_spots),
      }));

      const safeTotal = toNonNegativeInt(totalSpots, safeZones.reduce((acc, z) => acc + z.user_spots, 0));

      const res = await publishLocation({
        name: locationName.trim().slice(0, 200),
        google_maps_url: (googleMapsUrl || '').toString().slice(0, 500),
        video_url: sourceType === 'upload' ? `uploads/${uploadedFilename}` : videoUrl,
        grid_config: {
          horizontal_lines: hLines,
          vertical_lines: vLines,
          horizontal_line_angles: hLineAngles,
          vertical_line_angles: vLineAngles,
          exclude_regions: excludeRegions,
          horizontal_angle_deg: hLineAngles.length ? (hLineAngles.reduce((a, b) => a + b, 0) / hLineAngles.length) : 0,
          vertical_angle_deg: vLineAngles.length ? (vLineAngles.reduce((a, b) => a + b, 0) / vLineAngles.length) : 0,
          border,
        },
        zones: safeZones,
        total_spots: safeTotal,
        parameters: safeParams,
      });
      setPublished(res);
      setSelectedModelPath(''); // reset so next location starts with no model pre-selected
    } catch (e) {
      setError(e.message || 'Publish failed');
    } finally {
      setPublishing(false);
    }
  };

  // ── Render ──────────────────────────────────────────────────────
  return (
    <div className="lp dev-setup-page">
      <AuroraBackground />

      {/* Nav */}
      <nav className="lp-nav">
        <Link to="/" className="lp-brand" style={{ textDecoration: 'none' }}>
          <AppIcon size={38} className="lp-brand-icon" />
          <div>
            <div className="lp-brand-name">Smart Parking Solution</div>
            <div className="lp-brand-sub">Developer Setup</div>
          </div>
        </Link>
        <div className="lp-nav-right">
          <ThemeToggle />
          <Link to="/get-started" className="lp-nav-cta">
            <i className="fas fa-arrow-left" /> Choose Persona
          </Link>
        </div>
      </nav>

      {/* ── Developer Tab Bar ── */}
      <div className="dev-tab-bar">
        <button
          className={`dev-tab ${devTab === 'setup' ? 'dev-tab--active' : ''}`}
          onClick={() => setDevTab('setup')}
        >
          <i className="fas fa-cogs" /> Detector Setup
        </button>
        <button
          className={`dev-tab ${devTab === 'yolo' ? 'dev-tab--active' : ''}`}
          onClick={() => setDevTab('yolo')}
        >
          <i className="fas fa-tag" /> RF-DETR Dataset Tool
        </button>
      </div>

      {/* ── RF-DETR Dataset Tool tab ── */}
      {devTab === 'yolo' && (
        <div className="dev-tab-content">
          <YoloDatasetTool />
        </div>
      )}

      {/* ── Detector Setup Wizard tab ── */}
      {devTab === 'setup' && (<>

      {/* Stepper */}
      <div className="wiz-stepper">
        {STEPS.map((s, i) => (
          <div key={s.id} className={`wiz-step ${i === step ? 'wiz-step--active' : ''} ${i < step ? 'wiz-step--done' : ''}`}>
            <span className="wiz-step__num">
              {i < step ? <i className="fas fa-check" /> : <i className={s.icon} />}
            </span>
            <span className="wiz-step__label">{s.label}</span>
            {i < STEPS.length - 1 && <span className="wiz-step__line" />}
          </div>
        ))}
      </div>

      {/* Step content */}
      <div className="wiz-body">
        {error && (
          <div className="wiz-error">
            <i className="fas fa-exclamation-triangle" /> {error}
          </div>
        )}

        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -40 }}
            transition={{ duration: 0.25 }}
            className="wiz-step-content"
          >
            {/* ── STEP 0: Video Source ── */}
            {step === 0 && (
              <div className="wiz-panel">
                <h2 className="wiz-panel__title">Choose Video Source</h2>
                <p className="wiz-panel__desc">Upload a parking lot video or provide a live IP camera URL. Uploaded videos become the live feed (looped).</p>

                <div className="wiz-radio-group">
                  <label className={`wiz-radio ${sourceType === 'upload' ? 'wiz-radio--active' : ''}`}>
                    <input type="radio" name="src" checked={sourceType === 'upload'} onChange={() => setSourceType('upload')} />
                    <i className="fas fa-cloud-upload-alt" />
                    <div>
                      <strong>Upload Video</strong>
                      <span>Upload an MP4 / AVI / MKV video — it becomes the looped live feed</span>
                    </div>
                  </label>
                  <label className={`wiz-radio ${sourceType === 'url' ? 'wiz-radio--active' : ''}`}>
                    <input type="radio" name="src" checked={sourceType === 'url'} onChange={() => setSourceType('url')} />
                    <i className="fas fa-link" />
                    <div>
                      <strong>IP Camera / Stream URL</strong>
                      <span>HTTP, HTTPS, or RTSP stream URL</span>
                    </div>
                  </label>
                </div>

                {sourceType === 'upload' && (
                  <div className="wiz-upload-area">
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="video/mp4,video/avi,video/x-matroska,video/webm,video/quicktime"
                      className="wiz-upload-input"
                      onChange={e => {
                        setVideoFile(e.target.files?.[0] || null);
                        setUploaded(false);
                        setFrameB64(null);
                        setCapturedAt(0);
                      }}
                    />
                    {!videoFile ? (
                      <div className="wiz-upload-drop" onClick={() => fileInputRef.current?.click()}>
                        <i className="fas fa-film" />
                        <span>Click to select a video file</span>
                        <small>MP4, AVI, MKV, WebM — max recommended 500 MB</small>
                      </div>
                    ) : (
                      <div className="wiz-upload-selected">
                        <div className="wiz-upload-file-info">
                          <i className="fas fa-file-video" />
                          <div>
                            <strong>{videoFile.name}</strong>
                            <span>{(videoFile.size / (1024 * 1024)).toFixed(1)} MB</span>
                          </div>
                          <button className="wiz-btn wiz-btn--sm wiz-btn--ghost" onClick={() => {
                            setVideoFile(null);
                            setUploaded(false);
                            setCaptureDuration(null);
                            setCaptureTimestamp(0);
                            setCapturedAt(0);
                            if (fileInputRef.current) fileInputRef.current.value = '';
                          }}>
                            <i className="fas fa-times" />
                          </button>
                        </div>
                        {!uploaded ? (
                          <button className="wiz-btn wiz-btn--primary" onClick={handleUpload} disabled={uploading}>
                            {uploading ? <><i className="fas fa-spinner fa-spin" /> Uploading…</> : <><i className="fas fa-cloud-upload-alt" /> Upload & Set as Live Feed</>}
                          </button>
                        ) : (
                          <div className="wiz-upload-success">
                            <i className="fas fa-check-circle" /> Video uploaded — live feed is now using this video (looped)
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {sourceType === 'url' && (
                  <input
                    className="wiz-input"
                    type="text"
                    placeholder="https://example.com/stream.mp4 or rtsp://..."
                    value={videoUrl}
                    onChange={e => setVideoUrl(e.target.value)}
                  />
                )}
              </div>
            )}

            {/* ── STEP 1: Capture Frame ── */}
            {step === 1 && (
              <div className="wiz-panel">
                <h2 className="wiz-panel__title">Capture a Frame</h2>
                <p className="wiz-panel__desc">Choose a timestamp and capture the exact frame you want to use for grid segmentation.</p>

                <div className="wiz-border-inputs" style={{ marginBottom: 16 }}>
                  <label>
                    <span>Timestamp (seconds)</span>
                    <input
                      type="number"
                      min={0}
                      max={captureDuration ?? undefined}
                      step={0.1}
                      value={captureTimestamp}
                      onChange={e => {
                        const nextTs = Math.max(0, Number(e.target.value) || 0);
                        setCaptureTimestamp(captureDuration != null ? Math.min(nextTs, captureDuration) : nextTs);
                      }}
                    />
                  </label>
                  {captureDuration != null && (
                    <label>
                      <span>Video Duration</span>
                      <input type="text" value={`${formatDuration(captureDuration)} (${captureDuration.toFixed(1)}s)`} readOnly />
                    </label>
                  )}
                </div>

                {captureDuration != null && (
                  <div style={{ marginBottom: 16 }}>
                    <input
                      className="wiz-range"
                      type="range"
                      min={0}
                      max={captureDuration}
                      step={0.1}
                      value={Math.min(captureTimestamp, captureDuration)}
                      onChange={e => setCaptureTimestamp(Number(e.target.value) || 0)}
                      style={{ width: '100%' }}
                    />
                    <p className="wiz-ctrl-hint" style={{ marginTop: 6 }}>
                      <i className="fas fa-clock" /> Selected timestamp: {formatDuration(captureTimestamp)} ({Number(captureTimestamp || 0).toFixed(1)}s)
                    </p>
                  </div>
                )}

                {frameB64 ? (
                  <div className="wiz-frame-preview">
                    <img src={`data:image/jpeg;base64,${frameB64}`} alt="Captured frame" />
                    <p className="wiz-frame-info">{frameDims.w} × {frameDims.h} px</p>
                    <p className="wiz-frame-info">Captured at {formatDuration(capturedAt)} ({Number(capturedAt || 0).toFixed(1)}s)</p>
                  </div>
                ) : (
                  <div className="wiz-capture-placeholder">
                    <i className="fas fa-camera" />
                    <span>No frame captured yet</span>
                  </div>
                )}

                <button className="wiz-btn wiz-btn--primary" onClick={handleCapture} disabled={capturing}>
                  {capturing ? <><i className="fas fa-spinner fa-spin" /> Capturing…</> : <><i className="fas fa-camera" /> Capture Frame at Timestamp</>}
                </button>
              </div>
            )}

            {/* ── STEP 2: Grid Segmentation ── */}
            {step === 2 && (
              <div className="wiz-panel wiz-panel--wide">
                <h2 className="wiz-panel__title">Segment the Frame</h2>
                <p className="wiz-panel__desc">
                  Drag the lines to adjust grid zones. Add/remove lines, set border insets, and draw exclusion polygons for regions YOLO should ignore.
                </p>

                <div className="wiz-grid-layout">
                  <div className="wiz-canvas-wrap">
                    <canvas
                      ref={canvasRef}
                      className="wiz-canvas"
                      onMouseDown={handleCanvasMouseDown}
                      onMouseMove={handleCanvasMouseMove}
                      onMouseUp={handleCanvasMouseUp}
                      onMouseLeave={handleCanvasMouseUp}
                    />
                  </div>

                  <div className="wiz-grid-controls">
                    <div className="wiz-ctrl-section">
                      <h4><i className="fas fa-grip-lines" /> Horizontal Lines ({hLines.length})</h4>
                      <div className="wiz-ctrl-btns">
                        <button
                          className="wiz-btn wiz-btn--sm"
                          onClick={() => {
                            const sorted = sortLineAnglePairs([...hLines, 0.5], [...hLineAngles, 0]);
                            setHLines(sorted.lines);
                            setHLineAngles(sorted.angles);
                          }}
                        >
                          <i className="fas fa-plus" /> Add
                        </button>
                        <button className="wiz-btn wiz-btn--sm wiz-btn--ghost" disabled={hLines.length === 0}
                          onClick={() => {
                            setHLines(p => p.slice(0, -1));
                            setHLineAngles(p => p.slice(0, -1));
                          }}>
                          <i className="fas fa-minus" /> Remove
                        </button>
                      </div>
                      <div className="wiz-border-inputs">
                        {hLines.map((_, idx) => (
                          <label key={`h-ang-${idx}`}>
                            <span>H{idx + 1} angle</span>
                            <input
                              type="number"
                              min={-30}
                              max={30}
                              step={0.5}
                              value={hLineAngles[idx] ?? 0}
                              onChange={e => {
                                const val = clampAngle(e.target.value, -30, 30, 0);
                                setHLineAngles(prev => {
                                  const n = [...prev];
                                  n[idx] = val;
                                  return n;
                                });
                              }}
                            />
                          </label>
                        ))}
                      </div>
                    </div>

                    <div className="wiz-ctrl-section">
                      <h4><i className="fas fa-grip-lines-vertical" /> Vertical Lines ({vLines.length})</h4>
                      <div className="wiz-ctrl-btns">
                        <button
                          className="wiz-btn wiz-btn--sm"
                          onClick={() => {
                            const sorted = sortLineAnglePairs([...vLines, 0.5], [...vLineAngles, 0]);
                            setVLines(sorted.lines);
                            setVLineAngles(sorted.angles);
                          }}
                        >
                          <i className="fas fa-plus" /> Add
                        </button>
                        <button className="wiz-btn wiz-btn--sm wiz-btn--ghost" disabled={vLines.length === 0}
                          onClick={() => {
                            setVLines(p => p.slice(0, -1));
                            setVLineAngles(p => p.slice(0, -1));
                          }}>
                          <i className="fas fa-minus" /> Remove
                        </button>
                      </div>
                      <div className="wiz-border-inputs">
                        {vLines.map((_, idx) => (
                          <label key={`v-ang-${idx}`}>
                            <span>V{idx + 1} angle</span>
                            <input
                              type="number"
                              min={-30}
                              max={30}
                              step={0.5}
                              value={vLineAngles[idx] ?? 0}
                              onChange={e => {
                                const val = clampAngle(e.target.value, -30, 30, 0);
                                setVLineAngles(prev => {
                                  const n = [...prev];
                                  n[idx] = val;
                                  return n;
                                });
                              }}
                            />
                          </label>
                        ))}
                      </div>
                    </div>

                    <div className="wiz-ctrl-section">
                      <h4><i className="fas fa-vector-square" /> Exclusion Regions</h4>
                      <div className="wiz-ctrl-btns" style={{ marginBottom: 8 }}>
                        <button
                          className={`wiz-btn wiz-btn--sm ${gridTool === 'lines' ? '' : 'wiz-btn--ghost'}`}
                          onClick={() => setGridTool('lines')}
                        >
                          <i className="fas fa-grip-lines" /> Line Mode
                        </button>
                        <button
                          className={`wiz-btn wiz-btn--sm ${gridTool === 'exclude' ? '' : 'wiz-btn--ghost'}`}
                          onClick={() => setGridTool('exclude')}
                        >
                          <i className="fas fa-draw-polygon" /> Draw Mode
                        </button>
                      </div>
                      <div className="wiz-ctrl-btns">
                        <button
                          className="wiz-btn wiz-btn--sm"
                          onClick={finishExcludeRegion}
                          disabled={draftExcludeRegion.length < 3}
                        >
                          <i className="fas fa-check" /> Finish Region
                        </button>
                        <button
                          className="wiz-btn wiz-btn--sm wiz-btn--ghost"
                          onClick={cancelExcludeRegion}
                          disabled={draftExcludeRegion.length === 0}
                        >
                          <i className="fas fa-times" /> Cancel Draft
                        </button>
                      </div>
                      <div className="wiz-ctrl-btns" style={{ marginTop: 8 }}>
                        <button
                          className="wiz-btn wiz-btn--sm wiz-btn--ghost"
                          onClick={removeLastExcludeRegion}
                          disabled={excludeRegions.length === 0}
                        >
                          <i className="fas fa-undo" /> Remove Last
                        </button>
                        <button
                          className="wiz-btn wiz-btn--sm wiz-btn--ghost"
                          onClick={clearExcludeRegions}
                          disabled={excludeRegions.length === 0 && draftExcludeRegion.length === 0}
                        >
                          <i className="fas fa-trash" /> Clear All
                        </button>
                      </div>
                      <p className="wiz-ctrl-hint" style={{ marginTop: 8 }}>
                        <i className="fas fa-info-circle" /> In Draw Mode: click points on canvas, then Finish Region. Regions: {excludeRegions.length}, Draft points: {draftExcludeRegion.length}.
                      </p>
                    </div>

                    <div className="wiz-ctrl-section">
                      <h4><i className="fas fa-border-all" /> Border Inset (%)</h4>
                      <div className="wiz-border-inputs">
                        {['top', 'right', 'bottom', 'left'].map(side => (
                          <label key={side}>
                            <span>{side.charAt(0).toUpperCase() + side.slice(1)}</span>
                            <input type="number" min={0} max={40} value={border[side]}
                              onChange={e => setBorder(p => ({ ...p, [side]: Math.max(0, Math.min(40, Number(e.target.value))) }))}
                            />
                          </label>
                        ))}
                      </div>
                    </div>

                    

                    <p className="wiz-ctrl-hint">
                      <i className="fas fa-info-circle" /> Use Line Mode to drag dashed lines. Use Draw Mode to define ignored regions for YOLO.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* ── STEP 3: GPT Spot Estimation ── */}
            {step === 3 && (
              <div className="wiz-panel">
                <h2 className="wiz-panel__title">Detect Parking Spots</h2>
                <p className="wiz-panel__desc">
                  GPT Vision analyzes each zone image from your segmentation and estimates parking spaces. You can edit each value.
                </p>

                {zones.length === 0 ? (
                  <button className="wiz-btn wiz-btn--primary" onClick={handleEstimate} disabled={estimating}>
                    {estimating ? <><i className="fas fa-spinner fa-spin" /> Analyzing zones…</> : <><i className="fas fa-brain" /> Estimate with GPT Vision</>}
                  </button>
                ) : (
                  <>
                    <div className="wiz-zones-grid">
                      {zones.map((z, i) => (
                        <div key={z.zone_id} className="wiz-zone-card wiz-zone-card--with-img">
                          {/* Info button */}
                          <button
                            className="wiz-zone-card__info-btn"
                            title={`View zone ${z.zone_id} details`}
                            onClick={() => setZoneModal(z)}
                          >
                            <i className="fas fa-info-circle" />
                          </button>

                          {/* Zone image */}
                          {z.zone_image_base64 && (
                            <div className="wiz-zone-card__img-wrap">
                              <img
                                src={`data:image/jpeg;base64,${z.zone_image_base64}`}
                                alt={`Zone ${z.zone_id}`}
                              />
                              <span className="wiz-zone-card__id-overlay">{z.zone_id}</span>
                            </div>
                          )}
                          {!z.zone_image_base64 && (
                            <span className="wiz-zone-card__id">{z.zone_id}</span>
                          )}

                          <div className="wiz-zone-card__fields">
                            <label>
                              <span>GPT estimate</span>
                              <span className="wiz-zone-card__est">{z.estimated_spots}</span>
                            </label>
                            <label>
                              <span>Your value</span>
                              <input type="number" min={0} value={z.user_spots}
                                onChange={e => updateZoneSpots(i, e.target.value)} />
                            </label>
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="wiz-total-bar">
                      <span>Total Parking Spots</span>
                      <input type="number" className="wiz-total-input" min={0} value={totalSpots}
                        onChange={e => setTotalSpots(Math.max(0, parseInt(e.target.value) || 0))} />
                    </div>
                    <button className="wiz-btn wiz-btn--sm wiz-btn--ghost" onClick={handleEstimate} disabled={estimating}>
                      <i className="fas fa-redo" /> Re-estimate
                    </button>
                  </>
                )}

                {/* Zone Detail Modal */}
                <AnimatePresence>
                  {zoneModal && (
                    <motion.div
                      className="wiz-zone-modal-overlay"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      onClick={() => setZoneModal(null)}
                    >
                      <motion.div
                        className="wiz-zone-modal"
                        initial={{ opacity: 0, scale: 0.85, y: 30 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.85, y: 30 }}
                        transition={{ type: 'spring', damping: 22, stiffness: 300 }}
                        onClick={e => e.stopPropagation()}
                      >
                        <button className="wiz-zone-modal__close" onClick={() => setZoneModal(null)}>
                          <i className="fas fa-times" />
                        </button>
                        <h3>Zone {zoneModal.zone_id}</h3>
                        {zoneModal.zone_image_base64 && (
                          <img
                            className="wiz-zone-modal__img"
                            src={`data:image/jpeg;base64,${zoneModal.zone_image_base64}`}
                            alt={`Zone ${zoneModal.zone_id}`}
                          />
                        )}
                        <div className="wiz-zone-modal__stats">
                          <div className="wiz-zone-modal__stat">
                            <span className="wiz-zone-modal__stat-label">GPT Estimate</span>
                            <span className="wiz-zone-modal__stat-value">{zoneModal.estimated_spots} spots</span>
                          </div>
                          <div className="wiz-zone-modal__stat">
                            <span className="wiz-zone-modal__stat-label">Your Value</span>
                            <span className="wiz-zone-modal__stat-value">{zoneModal.user_spots} spots</span>
                          </div>
                          <div className="wiz-zone-modal__stat">
                            <span className="wiz-zone-modal__stat-label">Difference</span>
                            <span className={`wiz-zone-modal__stat-value ${zoneModal.user_spots !== zoneModal.estimated_spots ? 'wiz-zone-modal__stat-value--diff' : ''}`}>
                              {zoneModal.user_spots - zoneModal.estimated_spots >= 0 ? '+' : ''}{zoneModal.user_spots - zoneModal.estimated_spots}
                            </span>
                          </div>
                        </div>
                      </motion.div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}

            {/* ── STEP 4: Preview & Tune ── */}
            {step === 4 && (
              <div className="wiz-panel wiz-panel--wide">
                <h2 className="wiz-panel__title">Preview & Fine-Tune</h2>
                <p className="wiz-panel__desc">
                  Adjust hyperparameters and run a detection preview. Use the text input to describe your use-case for fine-tuning.
                </p>

                {/* Model picker — local selection only, never mutates the live detection service */}
                {trainedModels.filter(m => m.weight_file === 'best.pt' || m.weight_file === 'checkpoint_best_total.pth' || m.project === '').length > 0 && (
                  <div className="wiz-model-picker">
                    <div className="wiz-model-picker__label">
                      <i className="fas fa-brain" /> Detection Model
                      {!selectedModelPath && <span className="wiz-model-picker__hint"> — choose one to use for this project</span>}
                    </div>
                    <div className="wiz-model-picker__list">
                      {trainedModels
                        .filter(m => m.weight_file === 'best.pt' || m.weight_file === 'checkpoint_best_total.pth' || m.project === '')
                        .map(m => {
                          const isSelected = m.path === selectedModelPath;
                          return (
                            <button
                              key={m.path}
                              className={`wiz-model-btn ${isSelected ? 'wiz-model-btn--active' : ''}`}
                              onClick={() => setSelectedModelPath(m.path)}
                            >
                              {isSelected
                                ? <i className="fas fa-check-circle" style={{ color: '#10B981' }} />
                                : <i className="fas fa-circle" style={{ opacity: 0.35 }} />}
                              <span className="wiz-model-btn__label">{m.label}</span>
                              {isSelected && <span className="wiz-model-btn__badge">Selected</span>}
                            </button>
                          );
                        })}
                    </div>
                  </div>
                )}

                <div className="wiz-tune-layout">
                  <div className="wiz-tune-preview">
                    {previewB64 ? (
                      <>
                        <img src={`data:image/jpeg;base64,${previewB64}`} alt="Detection preview" />
                        <span className="wiz-tune-count"><i className="fas fa-car" /> {vehicleCount} vehicles detected</span>
                      </>
                    ) : (
                      <div className="wiz-capture-placeholder">
                        <i className="fas fa-search" />
                        <span>Run preview to see detection results</span>
                      </div>
                    )}
                  </div>

                  <div className="wiz-tune-params">
                    <label>
                      <span>Confidence Threshold <span className="wiz-hint" title="Minimum score for a detection to count. Lower = more detections but more false positives. Try 0.10–0.20 for aerial parking.">?</span></span>
                      <input type="range" min={0.01} max={1} step={0.01} value={params.confidence_threshold}
                        onChange={e => setParams(p => ({ ...p, confidence_threshold: +e.target.value }))} />
                      <span className="wiz-tune-val">{params.confidence_threshold.toFixed(2)}</span>
                    </label>
                    <label>
                      <span>NMS IoU Threshold <span className="wiz-hint" title="Controls how much two boxes must overlap before one is dropped as a duplicate. Keep at 0.4+ for dense parking lots — too low (e.g. 0.1) will suppress real adjacent cars.">?</span></span>
                      <input type="range" min={0.3} max={0.8} step={0.05} value={params.nms_iou_threshold}
                        onChange={e => setParams(p => ({ ...p, nms_iou_threshold: +e.target.value }))} />
                      <span className="wiz-tune-val">{params.nms_iou_threshold.toFixed(2)}</span>
                    </label>
                    <label>
                      <span>Segment Overlap <span className="wiz-hint" title="How much neighbouring grid segments overlap when running YOLO. Increase if vehicles near grid boundaries are missed.">?</span></span>
                      <input type="range" min={0} max={0.5} step={0.01} value={params.segment_overlap}
                        onChange={e => setParams(p => ({ ...p, segment_overlap: +e.target.value }))} />
                      <span className="wiz-tune-val">{(params.segment_overlap * 100).toFixed(0)}%</span>
                    </label>
                    <label>
                      <span>Min Vehicle Area (px²) <span className="wiz-hint" title="Detections smaller than this are treated as noise. Increase if you're seeing false positives on tiny objects.">?</span></span>
                      <input type="number" min={100} value={params.min_vehicle_area}
                        onChange={e => setParams(p => ({ ...p, min_vehicle_area: +e.target.value }))} />
                    </label>
                    <label>
                      <span>Max Vehicle Area (px²) <span className="wiz-hint" title="Detections larger than this are ignored (e.g. road segments, buildings). Lower-resolution videos may need a smaller value.">?</span></span>
                      <input type="number" min={1000} value={params.max_vehicle_area}
                        onChange={e => setParams(p => ({ ...p, max_vehicle_area: +e.target.value }))} />
                    </label>

                    <label className="wiz-tune-prompt">
                      <span>Fine-Tune Prompt</span>
                      <textarea
                        placeholder="Describe your use-case, camera angle, vehicle types expected…"
                        rows={3}
                        value={params.user_prompt}
                        onChange={e => setParams(p => ({ ...p, user_prompt: e.target.value }))}
                      />
                    </label>

                    <div className="wiz-tune-actions">
                      <button className="wiz-btn wiz-btn--ghost wiz-btn--sm" onClick={() => setParams({ ...DEFAULT_PARAMS })}>
                        <i className="fas fa-undo" /> Reset Defaults
                      </button>
                      <button className="wiz-btn wiz-btn--primary" onClick={handlePreview} disabled={previewing}>
                        {previewing ? <><i className="fas fa-spinner fa-spin" /> Running…</> : <><i className="fas fa-play" /> Run Preview</>}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* ── STEP 5: Publish ── */}
            {step === 5 && (
              <div className="wiz-panel">
                <h2 className="wiz-panel__title">Publish Location</h2>
                <p className="wiz-panel__desc">
                  Name your location, paste a Google Maps link, and publish it to the live dashboard.
                </p>

                {published ? (
                  <div className="wiz-published">
                    <i className="fas fa-check-circle" />
                    <h3>Published Successfully!</h3>
                    <p><strong>{published.name}</strong> — {published.total_spots} spots, {published.zone_count} zones</p>
                    <p className="wiz-published__id">ID: {published.id}</p>
                    <button className="wiz-btn wiz-btn--primary" onClick={() => navigate('/locations')}>
                      <i className="fas fa-map-marked-alt" /> Go to Locations
                    </button>
                  </div>
                ) : (
                  <div className="wiz-publish-form">
                    <label>
                      <span>Location Name *</span>
                      <input className="wiz-input" type="text" placeholder="e.g. Walmart Supercenter — Mechanicsburg"
                        value={locationName} onChange={e => setLocationName(e.target.value)} />
                    </label>
                    <label>
                      <span>Google Maps Link</span>
                      <input className="wiz-input" type="text"
                        placeholder="https://maps.google.com/..."
                        value={googleMapsUrl} onChange={e => setGoogleMapsUrl(e.target.value)} />
                    </label>

                    <div className="wiz-summary">
                      <h4>Summary</h4>
                      <ul>
                        <li><i className="fas fa-video" /> Source: {sourceType === 'upload' ? `Uploaded: ${videoFile?.name || 'video'}` : videoUrl}</li>
                        <li><i className="fas fa-th" /> Grid: {hLines.length + 1} rows × {vLines.length + 1} cols = {zones.length} zones</li>
                        <li><i className="fas fa-parking" /> Total Spots: {totalSpots}</li>
                        <li><i className="fas fa-sliders-h" /> Confidence: {params.confidence_threshold}</li>
                        <li><i className="fas fa-brain" /> Model: {trainedModels.find(m => m.path === selectedModelPath)?.label || selectedModelPath || 'none selected'}</li>
                      </ul>
                    </div>

                    <button className="wiz-btn wiz-btn--primary" onClick={handlePublish} disabled={publishing}>
                      {publishing ? <><i className="fas fa-spinner fa-spin" /> Publishing…</> : <><i className="fas fa-rocket" /> Publish Location</>}
                    </button>
                  </div>
                )}
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Bottom nav */}
      <div className="wiz-nav-bar">
        <button className="wiz-btn wiz-btn--ghost" onClick={prev} disabled={step === 0}>
          <i className="fas fa-arrow-left" /> Back
        </button>
        <span className="wiz-nav-bar__step">Step {step + 1} of {STEPS.length}</span>
        {step < STEPS.length - 1 && (
          <button className="wiz-btn wiz-btn--primary" onClick={next} disabled={!canGoNext()}>
            Next <i className="fas fa-arrow-right" />
          </button>
        )}
      </div>
      </>)}
    </div>
  );
}

// Helper functions mirroring the backend labels
function _rowLabels(count) {
  if (count === 1) return [''];
  if (count === 2) return ['T', 'B'];
  if (count === 3) return ['T', 'M', 'B'];
  const l = ['T'];
  for (let i = 1; i < count - 1; i++) l.push(`M${i}`);
  l.push('B');
  return l;
}

function _colLabels(count) {
  if (count === 1) return [''];
  if (count === 2) return ['L', 'R'];
  if (count === 3) return ['L', 'C', 'R'];
  const l = ['L'];
  for (let i = 1; i < count - 1; i++) l.push(`C${i}`);
  l.push('R');
  return l;
}
