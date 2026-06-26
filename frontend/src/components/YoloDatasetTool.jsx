/**
 * YoloDatasetTool — fully self-contained RF-DETR annotation tool embedded as a
 * tab inside the Developer Setup page.
 *
 * Features:
 *  • Project management (create / delete)
 *  • Bulk image upload with drag & drop
 *  • Class manager (add / edit color & name / delete)
 *  • Bounding-box annotation on a native HTML5 Canvas
 *    - Draw  : click-drag to create a new box
 *    - Select: click existing box to highlight it
 *    - Move  : drag selected box
 *    - Resize: drag the 8 handle squares on the selected box
 *    - Delete: press Delete / Backspace key
 *    - Escape : deselect
 *  • Per-image annotation list (right panel)
 *  • Image status badge (unannotated → in_progress → annotated)
 *  • Import existing labels
 *  • Export dataset zip (detection or segmentation, configurable split)
 */
import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

const API = '/api/v1/yolo';

// ─── API helpers ──────────────────────────────────────────────────────────────

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body?.detail || body?.error || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ─── Colour helpers ──────────────────────────────────────────────────────────

function hexToRgba(hex, alpha = 1) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// ─── Preset labels (fixed, not editable by user) ────────────────────────────

const PRESET_LABELS = [
  { name: 'car',            color: '#3B82F6' },
  { name: 'truck',          color: '#F59E0B' },
  { name: 'bus',            color: '#8B5CF6' },
  { name: 'parking space',  color: '#10B981' },
];

const PRESET_NAMES = new Set(PRESET_LABELS.map(l => l.name));

// ─── Segment grid helpers ────────────────────────────────────────────────────
function getGridCell(cx, cy, hLines, vLines, hLineAngles = [], vLineAngles = []) {
  const hBoundaries = [0, ...hLines, 1];
  const vBoundaries = [0, ...vLines, 1];
  const hBoundaryY = (idx, x) => {
    if (idx <= 0) return 0;
    if (idx >= hBoundaries.length - 1) return 1;
    const frac = hBoundaries[idx];
    const angle = clampAngle(hLineAngles[idx - 1] ?? 0);
    const tanA = Math.tan((angle * Math.PI) / 180);
    return frac + tanA * (x - 0.5);
  };
  const vBoundaryX = (idx, y) => {
    if (idx <= 0) return 0;
    if (idx >= vBoundaries.length - 1) return 1;
    const frac = vBoundaries[idx];
    const angle = clampAngle(vLineAngles[idx - 1] ?? 0);
    const tanA = Math.tan((angle * Math.PI) / 180);
    return frac + tanA * (y - 0.5);
  };

  let row = 0;
  for (let r = 1; r < hBoundaries.length - 1; r++) {
    if (cy > hBoundaryY(r, cx)) row = r;
    else break;
  }

  let col = 0;
  for (let c = 1; c < vBoundaries.length - 1; c++) {
    if (cx > vBoundaryX(c, cy)) col = c;
    else break;
  }

  const rows = hLines.length + 1;
  const cols = vLines.length + 1;
  const rowLabel = rows === 3 ? ['T','M','B'][row] : rows === 2 ? ['T','B'][row] : `R${row+1}`;
  const colLabel = cols === 2 ? ['L','R'][col] : cols === 3 ? ['L','C','R'][col] : `C${col+1}`;
  return `${rowLabel}${colLabel}`;
}

function clampAngle(value, min = -30, max = 30, fallback = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function getHLineSegment(frac, angle, W, H) {
  const angRad = (clampAngle(angle) * Math.PI) / 180;
  const tanA = Math.tan(angRad);
  const y0 = H * frac;
  const y1 = y0 + tanA * (0 - W / 2);
  const y2 = y0 + tanA * (W - W / 2);
  return { x1: 0, y1, x2: W, y2 };
}

function getVLineSegment(frac, angle, W, H) {
  const angRad = (clampAngle(angle) * Math.PI) / 180;
  const tanA = Math.tan(angRad);
  const x0 = W * frac;
  const x1 = x0 + tanA * (0 - H / 2);
  const x2 = x0 + tanA * (H - H / 2);
  return { x1, y1: 0, x2, y2: H };
}

function _rowLabels(n) {
  return n === 3 ? ['T','M','B'] : n === 2 ? ['T','B'] : Array.from({length:n},(_,i)=>`R${i+1}`);
}
function _colLabels(n) {
  return n === 2 ? ['L','R'] : n === 3 ? ['L','C','R'] : Array.from({length:n},(_,i)=>`C${i+1}`);
}

// ─── Canvas annotation logic ─────────────────────────────────────────────────

const HANDLE_SIZE = 8;

/**
 * Convert a YOLO normalised bbox {cx,cy,bw,bh} → pixel rect {x,y,w,h} with
 * respect to canvas display dimensions.
 */
function yoloToRect(ann, cw, ch) {
  const { cx, cy, bw, bh } = ann.bbox;
  return {
    x: (cx - bw / 2) * cw,
    y: (cy - bh / 2) * ch,
    w: bw * cw,
    h: bh * ch,
  };
}

function rectToYolo(x, y, w, h, cw, ch) {
  const cx = (x + w / 2) / cw;
  const cy = (y + h / 2) / ch;
  const bw = Math.abs(w) / cw;
  const bh = Math.abs(h) / ch;
  return { cx, cy, bw, bh };
}

/**
 * Returns which resize handle (0-7 clockwise from TL) the cursor is on, or -1.
 * Handles are: 0=TL 1=TC 2=TR 3=MR 4=BR 5=BC 6=BL 7=ML
 */
function hitHandle(mx, my, rect) {
  const { x, y, w, h } = rect;
  const handles = [
    [x, y], [x + w / 2, y], [x + w, y],
    [x + w, y + h / 2],
    [x + w, y + h], [x + w / 2, y + h],
    [x, y + h], [x, y + h / 2],
  ];
  for (let i = 0; i < handles.length; i++) {
    const [hx, hy] = handles[i];
    if (Math.abs(mx - hx) <= HANDLE_SIZE && Math.abs(my - hy) <= HANDLE_SIZE) return i;
  }
  return -1;
}

function hitBox(mx, my, rect) {
  return mx >= rect.x && mx <= rect.x + rect.w && my >= rect.y && my <= rect.y + rect.h;
}

const HANDLE_CURSORS = [
  'nw-resize', 'n-resize', 'ne-resize',
  'e-resize',
  'se-resize', 's-resize', 'sw-resize',
  'w-resize',
];

// ─── Statuses ────────────────────────────────────────────────────────────────

const STATUS_COLORS = {
  unannotated: '#6B7280',
  in_progress: '#F59E0B',
  annotated: '#10B981',
  reviewed: '#3B82F6',
  approved: '#8B5CF6',
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function YoloDatasetTool() {
  const [view, setView] = useState('projects'); // 'projects' | 'annotator' | 'export'
  const [projects, setProjects] = useState([]);
  const [activeProject, setActiveProject] = useState(null);
  const [classes, setClasses] = useState([]);
  const [images, setImages] = useState([]);
  const [activeImage, setActiveImage] = useState(null);
  const [annotations, setAnnotations] = useState([]);
  const [selectedAnnId, setSelectedAnnId] = useState(null);
  const [activeClassId, setActiveClassId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [toastMsg, setToastMsg] = useState('');
  const firstLoadRef = useRef(true);

  // Canvas drawing state (kept in refs to avoid re-renders mid-drag)
  const canvasRef = useRef(null);
  const imgElRef = useRef(null);
  const drawStateRef = useRef({
    drag: null, // null | { mode:'draw'|'move'|'resize', ...}
  });

  // Export config
  const [exportCfg, setExportCfg] = useState({ fmt: 'detection', train_ratio: 0.7, val_ratio: 0.2, test_ratio: 0.1, shuffle: true });
  const [exporting, setExporting] = useState(false);

  // Training config & job state
  const [trainCfg, setTrainCfg] = useState({
    model_name: 'base', epochs: 50, batch_size: 4,
    img_size: 640, lr0: 0.0001, patience: 10, freeze: 0, device: 'cpu', run_name: 'run1',
  });
  const [training, setTraining] = useState(false);
  const [trainJob, setTrainJob] = useState(null); // { job_id, status, logs, run_name, ... }
  const trainPollRef = useRef(null);
  const logBoxRef = useRef(null);

  // New-project form
  const [newProjName, setNewProjName] = useState('');
  const [newProjDesc, setNewProjDesc] = useState('');
  const [creating, setCreating] = useState(false);

  // New-class form
  const [newClassName, setNewClassName] = useState('');
  const [newClassColor, setNewClassColor] = useState('#3B82F6');
  const [addingClass, setAddingClass] = useState(false);
  const [showCustomLabelForm, setShowCustomLabelForm] = useState(false);
  const [sidebarStep, setSidebarStep] = useState(1); // 1=Upload  2=Grid  3=Auto-Annotate

  // Segment grid overlay (annotation canvas)
  const [showGrid, setShowGrid] = useState(true);
  const [hLines, setHLines] = useState([0.333, 0.667]);
  const [vLines, setVLines] = useState([0.5]);
  const [hLineAngles, setHLineAngles] = useState([0, 0]);
  const [vLineAngles, setVLineAngles] = useState([0]);

  // Grid editor
  const [showGridEditor, setShowGridEditor] = useState(false);
  const [gridDragging, setGridDragging] = useState(null);
  const gridEditorCanvasRef = useRef(null);
  const gridEditorImgRef = useRef(null);
  const [savingGrid, setSavingGrid] = useState(false);

  // Batch-grid modal (shown after each upload / frame-extract)
  const [batchGridModal, setBatchGridModal] = useState({ show: false, imageIds: [], groupName: '' });
  const [savingBatchGrid, setSavingBatchGrid] = useState(false);
  const pendingBatchImageIdsRef = useRef([]);

  // Video-to-frames
  const [uploadMode, setUploadMode] = useState('images'); // 'images' | 'video'
  const [videoFile, setVideoFile] = useState(null);
  const videoInputRef = useRef(null);
  const [extractMode, setExtractMode] = useState('interval'); // 'interval' | 'count'
  const [extractValue, setExtractValue] = useState(2);
  const [extracting, setExtracting] = useState(false);

  // ── Toast ──────────────────────────────────────────────────────────────────
  const toast = useCallback((msg) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(''), 3000);
  }, []);

  // ── Load projects ──────────────────────────────────────────────────────────
  const loadProjects = useCallback(async () => {
    const isFirst = firstLoadRef.current;
    if (isFirst) setLoading(true);
    try {
      setProjects(await apiFetch(`${API}/projects`));
    } catch (e) {
      toast(e.message);
    } finally {
      if (isFirst) { setLoading(false); firstLoadRef.current = false; }
    }
  }, [toast]);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  // Auto-scroll log box to bottom whenever logs update
  useEffect(() => {
    if (logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
    }
  }, [trainJob?.logs]);

  // Auto-advance sidebar step when first images are uploaded to a new project
  useEffect(() => {
    if (images.length > 0 && sidebarStep === 1) setSidebarStep(2);
  }, [images.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Open project ───────────────────────────────────────────────────────────
  const openProject = useCallback(async (proj) => {
    setActiveProject(proj);
    setActiveImage(null);
    setAnnotations([]);
    setSelectedAnnId(null);
    // Apply the project's saved grid lines.
    // Important: preserve empty arrays ([]) — user may intentionally remove all lines.
    const projHLines = Array.isArray(proj.grid_h_lines) ? proj.grid_h_lines : [0.333, 0.667];
    const projVLines = Array.isArray(proj.grid_v_lines) ? proj.grid_v_lines : [0.5];
    setHLines(projHLines);
    setVLines(projVLines);
    setHLineAngles(
      proj.grid_h_line_angles?.length
        ? proj.grid_h_line_angles.map((a) => clampAngle(a))
        : Array(projHLines.length).fill(0)
    );
    setVLineAngles(
      proj.grid_v_line_angles?.length
        ? proj.grid_v_line_angles.map((a) => clampAngle(a))
        : Array(projVLines.length).fill(0)
    );
    setShowGrid(true);
    setLoading(true);
    try {
      let [cls, imgs] = await Promise.all([
        apiFetch(`${API}/projects/${proj.id}/classes`),
        apiFetch(`${API}/projects/${proj.id}/images`),
      ]);
      // Auto-seed preset labels on first open
      if (cls.length === 0) {
        cls = await Promise.all(
          PRESET_LABELS.map(preset =>
            apiFetch(`${API}/projects/${proj.id}/classes`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name: preset.name, color: preset.color }),
            })
          )
        );
      }
      setClasses(cls);
      setImages(imgs);
      setActiveClassId(cls[0]?.id ?? null);
      setView('annotator');
      setSidebarStep((proj.grid_h_lines?.length || proj.grid_v_lines?.length) ? 3 : imgs.length > 0 ? 2 : 1);
    } catch (e) {
      toast(e.message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  // ── Create project ─────────────────────────────────────────────────────────
  const createProject = useCallback(async (e) => {
    e.preventDefault();
    if (!newProjName.trim()) return;
    setCreating(true);
    try {
      await apiFetch(`${API}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newProjName.trim(),
          description: newProjDesc.trim(),
        }),
      });
      setNewProjName('');
      setNewProjDesc('');
      await loadProjects();
    } catch (e) {
      toast(e.message);
    } finally {
      setCreating(false);
    }
  }, [newProjName, newProjDesc, loadProjects, toast]);

  // ── Delete project ─────────────────────────────────────────────────────────
  const deleteProject = useCallback(async (id) => {
    if (!window.confirm('Delete this project and all its images/annotations?')) return;
    try {
      await apiFetch(`${API}/projects/${id}`, { method: 'DELETE' });
      await loadProjects();
    } catch (e) { toast(e.message); }
  }, [loadProjects, toast]);

  // ── Load annotations for active image ─────────────────────────────────────
  const loadAnnotations = useCallback(async (imageId) => {
    try {
      const anns = await apiFetch(`${API}/images/${imageId}/annotations`);
      setAnnotations(anns);
      setSelectedAnnId(null);
    } catch (e) { toast(e.message); }
  }, [toast]);

  const selectImage = useCallback(async (img) => {
    setActiveImage(img);
    setAnnotations([]);
    setSelectedAnnId(null);
    await loadAnnotations(img.id);
  }, [loadAnnotations]);

  // ── Add class ──────────────────────────────────────────────────────────────
  const addClass = useCallback(async (e) => {
    e.preventDefault();
    if (!newClassName.trim() || !activeProject) return;
    setAddingClass(true);
    try {
      const cls = await apiFetch(`${API}/projects/${activeProject.id}/classes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newClassName.trim(), color: newClassColor }),
      });
      setClasses(prev => [...prev, cls]);
      setNewClassName('');
      if (!activeClassId) setActiveClassId(cls.id);
    } catch (e) { toast(e.message); }
    finally { setAddingClass(false); }
  }, [newClassName, newClassColor, activeProject, activeClassId, toast]);

  // ── Delete class ───────────────────────────────────────────────────────────
  const deleteClass = useCallback(async (classId) => {
    try {
      await apiFetch(`${API}/classes/${classId}`, { method: 'DELETE' });
      setClasses(prev => prev.filter(c => c.id !== classId));
      if (activeClassId === classId) setActiveClassId(classes.find(c => c.id !== classId)?.id ?? null);
    } catch (e) { toast(e.message); }
  }, [activeClassId, classes, toast]);

  // ── Upload images ──────────────────────────────────────────────────────────
  const uploadImages = useCallback(async (fileList) => {
    if (!activeProject || !fileList?.length) return;
    const fd = new FormData();
    for (const f of fileList) fd.append('files', f);
    setLoading(true);
    try {
      const newImgs = await apiFetch(`${API}/projects/${activeProject.id}/images`, { method: 'POST', body: fd });
      setImages(prev => [...prev, ...newImgs]);
      toast(`Uploaded ${newImgs.length} image(s)`);
      if (newImgs.length > 0) {
        const ids = newImgs.map(i => i.id);
        pendingBatchImageIdsRef.current = ids;
        const label = fileList?.length === 1 ? fileList[0].name : `${fileList?.length ?? newImgs.length} files`;
        setBatchGridModal({ show: true, imageIds: ids, groupName: label });
      }
    } catch (e) { toast(e.message); }
    finally { setLoading(false); }
  }, [activeProject, toast]);

  // ── Extract frames from video ──────────────────────────────────────────────
  const extractFrames = useCallback(async () => {
    if (!activeProject || !videoFile) return;
    const fd = new FormData();
    fd.append('video', videoFile);
    setExtracting(true);
    try {
      const params = new URLSearchParams({ mode: extractMode, value: extractValue });
      const newImgs = await apiFetch(
        `${API}/projects/${activeProject.id}/extract-frames?${params}`,
        { method: 'POST', body: fd },
      );
      setImages(prev => [...prev, ...newImgs]);
      setVideoFile(null);
      if (videoInputRef.current) videoInputRef.current.value = '';
      toast(`Extracted ${newImgs.length} frame(s) from video`);
      if (newImgs.length > 0) {
        const ids = newImgs.map(i => i.id);
        pendingBatchImageIdsRef.current = ids;
        setBatchGridModal({ show: true, imageIds: ids, groupName: videoFile?.name ?? 'video' });
      }
    } catch (e) { toast(e.message); }
    finally { setExtracting(false); }
  }, [activeProject, videoFile, extractMode, extractValue, toast]);

  // ── Delete image ───────────────────────────────────────────────────────────
  const deleteImage = useCallback(async (imgId) => {
    try {
      await apiFetch(`${API}/images/${imgId}`, { method: 'DELETE' });
      setImages(prev => prev.filter(i => i.id !== imgId));
      if (activeImage?.id === imgId) {
        setActiveImage(null);
        setAnnotations([]);
      }
    } catch (e) { toast(e.message); }
  }, [activeImage, toast]);

  // ── Save annotation to backend ─────────────────────────────────────────────
  const saveAnnotation = useCallback(async (classId, bbox) => {
    if (!activeImage || !classId) return null;
    try {
      const ann = await apiFetch(`${API}/images/${activeImage.id}/annotations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ class_id: classId, type: 'bbox', bbox }),
      });
      setAnnotations(prev => [...prev, ann]);
      // Refresh image status in list
      setImages(prev => prev.map(i => i.id === activeImage.id ? { ...i, status: 'in_progress', annotation_count: i.annotation_count + 1 } : i));
      return ann;
    } catch (e) { toast(e.message); return null; }
  }, [activeImage, toast]);

  // ── Update annotation bbox in backend ─────────────────────────────────────
  const updateAnnotation = useCallback(async (annId, bbox, classId) => {
    try {
      const updated = await apiFetch(`${API}/annotations/${annId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bbox, class_id: classId }),
      });
      setAnnotations(prev => prev.map(a => a.id === annId ? updated : a));
    } catch (e) { toast(e.message); }
  }, [toast]);

  // ── Delete annotation ──────────────────────────────────────────────────────
  const deleteAnnotation = useCallback(async (annId) => {
    try {
      await apiFetch(`${API}/annotations/${annId}`, { method: 'DELETE' });
      setAnnotations(prev => {
        const next = prev.filter(a => a.id !== annId);
        // Update image status
        setImages(imgs => imgs.map(i => i.id === activeImage?.id
          ? { ...i, status: next.length === 0 ? 'unannotated' : 'in_progress', annotation_count: next.length }
          : i));
        return next;
      });
      setSelectedAnnId(null);
    } catch (e) { toast(e.message); }
  }, [activeImage, toast]);

  // ── Mark image as annotated ────────────────────────────────────────────────
  const markAnnotated = useCallback(async () => {
    if (!activeImage) return;
    try {
      const updated = await apiFetch(`${API}/images/${activeImage.id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'annotated' }),
      });
      setImages(prev => prev.map(i => i.id === activeImage.id ? updated : i));
      setActiveImage(updated);
      toast('Marked as annotated ✓');
    } catch (e) { toast(e.message); }
  }, [activeImage, toast]);

  // ── Save batch grid (creates group, patches grid, assigns images) ──────────
  const saveBatchGrid = useCallback(async () => {
    if (!activeProject) return;
    setSavingBatchGrid(true);
    try {
      const imageIds = pendingBatchImageIdsRef.current.length
        ? pendingBatchImageIdsRef.current
        : (batchGridModal.imageIds || []);
      if (!imageIds.length) {
        throw new Error('No batch images found to assign. Please re-upload/re-extract and try again.');
      }

      const group = await apiFetch(`${API}/projects/${activeProject.id}/groups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: batchGridModal.groupName }),
      });
      await apiFetch(`${API}/groups/${group.id}/grid`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          grid_h_lines: hLines,
          grid_v_lines: vLines,
          grid_h_line_angles: hLineAngles,
          grid_v_line_angles: vLineAngles,
        }),
      });
      const updatedImgs = await apiFetch(`${API}/groups/${group.id}/assign-images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: imageIds }),
      });

      const imgs = await apiFetch(`${API}/projects/${activeProject.id}/images`);
      setImages(imgs);

      if (!updatedImgs.length) {
        throw new Error('Batch grid saved but no images were assigned to this batch.');
      }

      pendingBatchImageIdsRef.current = [];
      setBatchGridModal({ show: false, imageIds: [], groupName: '' });
      toast(`Batch grid saved for ${updatedImgs.length} image(s) ✓`);
    } catch (e) { toast(e.message); }
    finally { setSavingBatchGrid(false); }
  }, [activeProject, batchGridModal, hLines, vLines, hLineAngles, vLineAngles, toast]);

  // ── Save grid to backend ───────────────────────────────────────────────────
  const saveGrid = useCallback(async () => {
    if (!activeProject) return;
    setSavingGrid(true);
    try {
      const updated = await apiFetch(`${API}/projects/${activeProject.id}/grid`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          grid_h_lines: hLines,
          grid_v_lines: vLines,
          grid_h_line_angles: hLineAngles,
          grid_v_line_angles: vLineAngles,
        }),
      });
      setActiveProject(updated);
      setProjects(prev => prev.map(p => (p.id === updated.id ? updated : p)));
      setShowGridEditor(false);
      setSidebarStep(3);
      toast('Grid saved ✓');
    } catch (e) { toast(e.message); }
    finally { setSavingGrid(false); }
  }, [activeProject, hLines, vLines, hLineAngles, vLineAngles, toast]);

  // ── Grid editor canvas ─────────────────────────────────────────────────────
  const drawGridEditor = useCallback(() => {
    const canvas = gridEditorCanvasRef.current;
    const img = gridEditorImgRef.current;
    if (!canvas || !img) return;

    const DISPLAY_W = 760;
    const scale = DISPLAY_W / img.width;
    const DISPLAY_H = Math.round(img.height * scale);
    canvas.width  = DISPLAY_W;
    canvas.height = DISPLAY_H;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, DISPLAY_W, DISPLAY_H);
    const W = DISPLAY_W, H = DISPLAY_H;

    // Horizontal lines — cyan glow + dashed (tilted per-line)
    hLines.forEach((frac, idx) => {
      const angle = hLineAngles[idx] ?? 0;
      const p = getHLineSegment(frac, angle, W, H);
      ctx.save();
      ctx.strokeStyle = 'rgba(0,212,255,0.45)'; ctx.lineWidth = 7; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = '#00D4FF'; ctx.lineWidth = 2.5; ctx.setLineDash([12, 8]);
      ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
      ctx.setLineDash([]);
    });

    // Vertical lines — purple glow + dashed (tilted per-line)
    vLines.forEach((frac, idx) => {
      const angle = vLineAngles[idx] ?? 0;
      const p = getVLineSegment(frac, angle, W, H);
      ctx.save();
      ctx.strokeStyle = 'rgba(168,85,247,0.45)'; ctx.lineWidth = 7; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = '#A855F7'; ctx.lineWidth = 2.5; ctx.setLineDash([12, 8]);
      ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
      ctx.setLineDash([]);
    });

    // Zone labels
    const rows = hLines.length + 1, cols = vLines.length + 1;
    const rowLbls = _rowLabels(rows), colLbls = _colLabels(cols);
    const hB = [0, ...hLines, 1], vB = [0, ...vLines, 1];
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const cx = W * (vB[c] + vB[c + 1]) / 2;
        const cy = H * (hB[r] + hB[r + 1]) / 2;
        const label = `${rowLbls[r]}${colLbls[c]}`;
        ctx.font = 'bold 15px Inter, sans-serif';
        const { width: tw } = ctx.measureText(label);
        const pw = tw + 16, ph = 26;
        ctx.fillStyle = 'rgba(0,0,0,0.65)';
        const rr = canvas.getContext('2d');
        rr.beginPath();
        if (rr.roundRect) rr.roundRect(cx - pw / 2, cy - ph / 2, pw, ph, 6);
        else rr.rect(cx - pw / 2, cy - ph / 2, pw, ph);
        rr.fill();
        ctx.fillStyle = '#fff';
        ctx.fillText(label, cx, cy);
      }
    }
  }, [hLines, vLines]);

  useEffect(() => {
    if (showGridEditor || batchGridModal.show) drawGridEditor();
  }, [showGridEditor, batchGridModal.show, drawGridEditor]);

  // Pre-load grid editor image from first uploaded image when editor opens
  useEffect(() => {
    if ((!showGridEditor && !batchGridModal.show) || images.length === 0) return;
    // Prefer the most-recently-added image (end of list) for the batch modal
    const bg = batchGridModal.show
      ? (images.find(i => batchGridModal.imageIds.includes(i.id)) ?? images[images.length - 1])
      : images[0];
    const img = new Image();
    img.onload = () => { gridEditorImgRef.current = img; drawGridEditor(); };
    img.src = `${API}/images/${bg.id}/file`;
  }, [showGridEditor, batchGridModal.show, images, drawGridEditor]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Grid editor mouse handlers ────────────────────────────────────────────
  const handleGridMouseDown = useCallback((e) => {
    const canvas = gridEditorCanvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (canvas.width  / rect.width);
    const my = (e.clientY - rect.top)  * (canvas.height / rect.height);
    const W = canvas.width, H = canvas.height;
    const SNAP = 18;
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
    for (let i = 0; i < hLines.length; i++) {
      const p = getHLineSegment(hLines[i], hLineAngles[i] ?? 0, W, H);
      if (distToSegment(mx, my, p.x1, p.y1, p.x2, p.y2) < SNAP) {
        setGridDragging({ axis: 'h', index: i });
        return;
      }
    }
    for (let i = 0; i < vLines.length; i++) {
      const p = getVLineSegment(vLines[i], vLineAngles[i] ?? 0, W, H);
      if (distToSegment(mx, my, p.x1, p.y1, p.x2, p.y2) < SNAP) {
        setGridDragging({ axis: 'v', index: i });
        return;
      }
    }
  }, [hLines, vLines, hLineAngles, vLineAngles]);

  const handleGridMouseMove = useCallback((e) => {
    if (!gridDragging || !gridEditorCanvasRef.current) return;
    const canvas = gridEditorCanvasRef.current;
    const rect = canvas.getBoundingClientRect();
    if (gridDragging.axis === 'h') {
      const my = (e.clientY - rect.top) * (canvas.height / rect.height);
      const mx = (e.clientX - rect.left) * (canvas.width / rect.width);
      const hTan = Math.tan(((hLineAngles[gridDragging.index] ?? 0) * Math.PI) / 180);
      const yAdj = my - hTan * (mx - canvas.width / 2);
      const rawFrac = yAdj / canvas.height;
      setHLines(prev => {
        const minGap = 0.01;
        const idx = gridDragging.index;
        const lower = idx > 0 ? prev[idx - 1] + minGap : 0.05;
        const upper = idx < prev.length - 1 ? prev[idx + 1] - minGap : 0.95;
        const frac = Math.min(upper, Math.max(lower, rawFrac));
        const n = [...prev];
        n[idx] = frac;
        return n;
      });
    } else {
      const mx = (e.clientX - rect.left) * (canvas.width / rect.width);
      const my = (e.clientY - rect.top) * (canvas.height / rect.height);
      const vTan = Math.tan(((vLineAngles[gridDragging.index] ?? 0) * Math.PI) / 180);
      const xAdj = mx - vTan * (my - canvas.height / 2);
      const rawFrac = xAdj / canvas.width;
      setVLines(prev => {
        const minGap = 0.01;
        const idx = gridDragging.index;
        const lower = idx > 0 ? prev[idx - 1] + minGap : 0.05;
        const upper = idx < prev.length - 1 ? prev[idx + 1] - minGap : 0.95;
        const frac = Math.min(upper, Math.max(lower, rawFrac));
        const n = [...prev];
        n[idx] = frac;
        return n;
      });
    }
  }, [gridDragging, hLineAngles, vLineAngles]);

  const handleGridMouseUp = useCallback(() => setGridDragging(null), []);

  // ── Canvas rendering ─────────────────────────────────────────────────────

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const imgEl = imgElRef.current;
    if (!canvas || !imgEl || !imgEl.complete) return;

    const CW = canvas.width;
    const CH = canvas.height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, CW, CH);
    ctx.drawImage(imgEl, 0, 0, CW, CH);

    // Draw all annotations
    for (const ann of annotations) {
      if (ann.type !== 'bbox' || !ann.bbox) continue;
      const rect = yoloToRect(ann, CW, CH);
      const color = ann.class_color || '#3B82F6';
      const isSelected = ann.id === selectedAnnId;

      ctx.strokeStyle = color;
      ctx.lineWidth = isSelected ? 3 : 2;
      ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
      ctx.fillStyle = hexToRgba(color, 0.12);
      ctx.fillRect(rect.x, rect.y, rect.w, rect.h);

      // Label background
      const label = ann.class_name || '';
      ctx.font = 'bold 12px Inter, sans-serif';
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = color;
      ctx.fillRect(rect.x, rect.y - 18, tw + 10, 18);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, rect.x + 5, rect.y - 4);

      // Resize handles for selected box
      if (isSelected) {
        const handles = [
          [rect.x, rect.y], [rect.x + rect.w / 2, rect.y], [rect.x + rect.w, rect.y],
          [rect.x + rect.w, rect.y + rect.h / 2],
          [rect.x + rect.w, rect.y + rect.h], [rect.x + rect.w / 2, rect.y + rect.h],
          [rect.x, rect.y + rect.h], [rect.x, rect.y + rect.h / 2],
        ];
        ctx.fillStyle = '#fff';
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        for (const [hx, hy] of handles) {
          ctx.fillRect(hx - HANDLE_SIZE / 2, hy - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
          ctx.strokeRect(hx - HANDLE_SIZE / 2, hy - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
        }
      }
    }

    // ── Segment grid overlay ─────────────────────────────────────────────────
    if (showGrid) {
      // Use group-level grid for this image if available, otherwise fall back to project grid
      const effHL  = activeImage?.group_grid_h_lines        ?? hLines;
      const effVL  = activeImage?.group_grid_v_lines        ?? vLines;
      const effHLA = activeImage?.group_grid_h_line_angles  ?? hLineAngles;
      const effVLA = activeImage?.group_grid_v_line_angles  ?? vLineAngles;
      ctx.save();
      // Horizontal lines — cyan (tilted per-line)
      effHL.forEach((frac, idx) => {
        const angle = effHLA[idx] ?? 0;
        const p = getHLineSegment(frac, angle, CW, CH);
        ctx.strokeStyle = 'rgba(0,212,255,0.35)'; ctx.lineWidth = 5; ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
        ctx.strokeStyle = '#00D4FF'; ctx.lineWidth = 1.5; ctx.setLineDash([8, 6]);
        ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
        ctx.setLineDash([]);
      });
      // Vertical lines — purple (tilted per-line)
      effVL.forEach((frac, idx) => {
        const angle = effVLA[idx] ?? 0;
        const p = getVLineSegment(frac, angle, CW, CH);
        ctx.strokeStyle = 'rgba(168,85,247,0.35)'; ctx.lineWidth = 5; ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
        ctx.strokeStyle = '#A855F7'; ctx.lineWidth = 1.5; ctx.setLineDash([8, 6]);
        ctx.beginPath(); ctx.moveTo(p.x1, p.y1); ctx.lineTo(p.x2, p.y2); ctx.stroke();
        ctx.setLineDash([]);
      });
      // Zone labels (top-left corner of each cell)
      const gRows = effHL.length + 1, gCols = effVL.length + 1;
      const rowLbls = _rowLabels(gRows), colLbls = _colLabels(gCols);
      const hB = [0, ...effHL, 1], vB = [0, ...effVL, 1];
      ctx.font = 'bold 11px Inter, sans-serif';
      ctx.textAlign = 'left'; ctx.textBaseline = 'top';
      for (let r = 0; r < gRows; r++) {
        for (let c = 0; c < gCols; c++) {
          const lx = CW * vB[c] + 4;
          const ly = CH * hB[r] + 3;
          const label = `${rowLbls[r]}${colLbls[c]}`;
          const { width: tw } = ctx.measureText(label);
          ctx.fillStyle = 'rgba(0,0,0,0.55)';
          ctx.fillRect(lx - 2, ly - 1, tw + 6, 14);
          ctx.fillStyle = 'rgba(255,255,255,0.85)';
          ctx.fillText(label, lx + 1, ly);
        }
      }
      ctx.restore();
    }
  }, [annotations, selectedAnnId, showGrid, hLines, vLines, hLineAngles, vLineAngles, activeImage]);

  // Re-draw whenever annotations or selection change
  useEffect(() => { drawCanvas(); }, [drawCanvas]);

  // ── Load image into canvas when activeImage changes ─────────────────────

  useEffect(() => {
    if (!activeImage) return;
    const img = new Image();
    img.onload = () => {
      imgElRef.current = img;
      const canvas = canvasRef.current;
      if (!canvas) return;
      // Fit image in 800px width
      const MAX_W = 800;
      const scale = Math.min(1, MAX_W / img.width);
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      drawCanvas();
    };
    img.src = `${API}/images/${activeImage.id}/file`;
  }, [activeImage, drawCanvas]);

  // ── Canvas mouse events ──────────────────────────────────────────────────

  const getCanvasPos = (e) => {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const sx = canvas.width / rect.width;
    const sy = canvas.height / rect.height;
    return [(e.clientX - rect.left) * sx, (e.clientY - rect.top) * sy];
  };

  const handleMouseDown = useCallback((e) => {
    e.preventDefault();
    if (!activeImage || !activeClassId) return;
    const [mx, my] = getCanvasPos(e);
    const CW = canvasRef.current.width;
    const CH = canvasRef.current.height;
    const ds = drawStateRef.current;

    // Check resize handle on selected annotation first
    if (selectedAnnId !== null) {
      const selAnn = annotations.find(a => a.id === selectedAnnId);
      if (selAnn?.bbox) {
        const rect = yoloToRect(selAnn, CW, CH);
        const hIdx = hitHandle(mx, my, rect);
        if (hIdx !== -1) {
          ds.drag = { mode: 'resize', annId: selectedAnnId, handleIdx: hIdx, rect: { ...rect }, startMx: mx, startMy: my };
          return;
        }
        // Check move (inside box but not handle)
        if (hitBox(mx, my, rect)) {
          ds.drag = { mode: 'move', annId: selectedAnnId, rect: { ...rect }, startMx: mx, startMy: my };
          return;
        }
      }
    }

    // Check click on any annotation to select
    for (let i = annotations.length - 1; i >= 0; i--) {
      const ann = annotations[i];
      if (ann.type !== 'bbox' || !ann.bbox) continue;
      const rect = yoloToRect(ann, CW, CH);
      if (hitHandle(mx, my, rect) !== -1 || hitBox(mx, my, rect)) {
        setSelectedAnnId(ann.id);
        ds.drag = { mode: 'move', annId: ann.id, rect: { ...rect }, startMx: mx, startMy: my };
        drawCanvas();
        return;
      }
    }

    // Start drawing new box
    setSelectedAnnId(null);
    ds.drag = { mode: 'draw', x0: mx, y0: my, x1: mx, y1: my };
  }, [activeImage, activeClassId, annotations, selectedAnnId, drawCanvas]);

  const handleMouseMove = useCallback((e) => {
    e.preventDefault();
    const ds = drawStateRef.current;
    if (!ds.drag) return;
    const [mx, my] = getCanvasPos(e);
    const CW = canvasRef.current.width;
    const CH = canvasRef.current.height;
    const { drag } = ds;

    if (drag.mode === 'draw') {
      drag.x1 = mx;
      drag.y1 = my;
      // Redraw + preview rect
      drawCanvas();
      const ctx = canvasRef.current.getContext('2d');
      const x = Math.min(drag.x0, drag.x1);
      const y = Math.min(drag.y0, drag.y1);
      const w = Math.abs(drag.x1 - drag.x0);
      const h = Math.abs(drag.y1 - drag.y0);
      const cls = classes.find(c => c.id === activeClassId);
      const color = cls?.color || '#3B82F6';
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      return;
    }

    if (drag.mode === 'move') {
      const dx = mx - drag.startMx;
      const dy = drag.startMy !== undefined ? my - drag.startMy : 0;
      const nr = {
        x: drag.rect.x + dx,
        y: drag.rect.y + dy,
        w: drag.rect.w,
        h: drag.rect.h,
      };
      // Clamp inside canvas
      nr.x = Math.max(0, Math.min(CW - nr.w, nr.x));
      nr.y = Math.max(0, Math.min(CH - nr.h, nr.y));
      drag._currentRect = nr;
      const ann = annotations.find(a => a.id === drag.annId);
      if (ann) {
        // Temporarily mutate for redraw
        const savedBbox = { ...ann.bbox };
        ann.bbox = rectToYolo(nr.x, nr.y, nr.w, nr.h, CW, CH);
        drawCanvas();
        ann.bbox = savedBbox;
      }
      return;
    }

    if (drag.mode === 'resize') {
      const { handleIdx, rect } = drag;
      let { x, y, w, h } = rect;
      const dx = mx - drag.startMx;
      const dy = my - drag.startMy;
      // Adjust according to handle
      if (handleIdx === 0) { x += dx; y += dy; w -= dx; h -= dy; }
      else if (handleIdx === 1) { y += dy; h -= dy; }
      else if (handleIdx === 2) { y += dy; w += dx; h -= dy; }
      else if (handleIdx === 3) { w += dx; }
      else if (handleIdx === 4) { w += dx; h += dy; }
      else if (handleIdx === 5) { h += dy; }
      else if (handleIdx === 6) { x += dx; w -= dx; h += dy; }
      else if (handleIdx === 7) { x += dx; w -= dx; }
      if (w < 5 || h < 5) return;
      drag._currentRect = { x, y, w, h };
      const ann = annotations.find(a => a.id === drag.annId);
      if (ann) {
        const savedBbox = { ...ann.bbox };
        ann.bbox = rectToYolo(x, y, w, h, CW, CH);
        drawCanvas();
        ann.bbox = savedBbox;
      }
    }
  }, [annotations, activeClassId, classes, drawCanvas]);

  const handleMouseUp = useCallback(async (e) => {
    e.preventDefault();
    const ds = drawStateRef.current;
    if (!ds.drag) return;
    const { drag } = ds;
    ds.drag = null;
    const CW = canvasRef.current.width;
    const CH = canvasRef.current.height;

    if (drag.mode === 'draw') {
      const x = Math.min(drag.x0, drag.x1);
      const y = Math.min(drag.y0, drag.y1);
      const w = Math.abs(drag.x1 - drag.x0);
      const h = Math.abs(drag.y1 - drag.y0);
      if (w < 5 || h < 5) { drawCanvas(); return; }
      const bbox = rectToYolo(x, y, w, h, CW, CH);
      const ann = await saveAnnotation(activeClassId, bbox);
      if (ann) setSelectedAnnId(ann.id);
      return;
    }

    if ((drag.mode === 'move' || drag.mode === 'resize') && drag._currentRect) {
      const { x, y, w, h } = drag._currentRect;
      if (w < 5 || h < 5) { drawCanvas(); return; }
      const bbox = rectToYolo(x, y, w, h, CW, CH);
      await updateAnnotation(drag.annId, bbox, null);
    }
  }, [activeClassId, saveAnnotation, updateAnnotation, drawCanvas]);

  // ── Keyboard shortcuts ──────────────────────────────────────────────────

  useEffect(() => {
    if (view !== 'annotator') return;
    const handler = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedAnnId !== null) {
        deleteAnnotation(selectedAnnId);
      }
      if (e.key === 'Escape') setSelectedAnnId(null);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [view, selectedAnnId, deleteAnnotation]);

  // ── Import YOLO labels ──────────────────────────────────────────────────

  const importLabels = useCallback(async (e) => {
    const file = e.target.files?.[0];
    if (!file || !activeImage) return;
    const fd = new FormData();
    fd.append('label_file', file);
    setLoading(true);
    try {
      const result = await apiFetch(`${API}/images/${activeImage.id}/import-labels`, { method: 'POST', body: fd });
      await loadAnnotations(activeImage.id);
      toast(`Imported ${result.length} annotation(s)`);
    } catch (err) { toast(err.message); }
    finally { setLoading(false); e.target.value = ''; }
  }, [activeImage, loadAnnotations, toast]);

  // Available models (base + trained)
  const [availableModels, setAvailableModels] = useState([]); // { label, path, project?, run?, weight_file }
  const loadAvailableModels = useCallback(async () => {
    try {
      const models = await apiFetch('/api/v1/developer/models?wizard=1');
      setAvailableModels(models || []);
    } catch (e) {
      console.warn('Failed to load available models:', e);
      setAvailableModels([]);
    }
  }, []);
  useEffect(() => {
    loadAvailableModels();
  }, [loadAvailableModels]);

  // Auto-annotate
  const [autoAnnCfg, setAutoAnnCfg] = useState({ conf: 0.25, model_path: 'base', overwrite: false });
  const [autoAnnotating, setAutoAnnotating] = useState(false);
  const [autoAnnResult, setAutoAnnResult] = useState(null); // null | AutoAnnotateResult
  const [showAutoAnnPanel, setShowAutoAnnPanel] = useState(false);

  // ── Workflow step progress (derived) ─────────────────────────────────────
  const step1Done = images.length > 0;
  const step2Done = step1Done && !!(activeProject?.grid_h_lines?.length || activeProject?.grid_v_lines?.length);
  const step3Done = step1Done && !!autoAnnResult;

  const runAutoAnnotate = useCallback(async () => {
    if (!activeProject) return;
    setAutoAnnotating(true);
    setAutoAnnResult(null);
    try {
      const result = await apiFetch(`${API}/projects/${activeProject.id}/auto-annotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(autoAnnCfg),
      });
      setAutoAnnResult(result);
      toast(`Auto-annotated: ${result.created} boxes across ${result.processed} image(s) ✓`);
      // Reload images list so annotation counts update
      const imgs = await apiFetch(`${API}/projects/${activeProject.id}/images`);
      setImages(imgs);
      // Reload annotations for the currently open image
      if (activeImage) await loadAnnotations(activeImage.id);
    } catch (e) {
      toast(e.message);
    } finally {
      setAutoAnnotating(false);
    }
  }, [activeProject, activeImage, autoAnnCfg, loadAnnotations, toast]);

  // ── Export ──────────────────────────────────────────────────────────────

  const runExport = useCallback(async () => {
    if (!activeProject) return;
    const total = exportCfg.train_ratio + exportCfg.val_ratio + exportCfg.test_ratio;
    if (Math.abs(total - 1.0) > 0.01) { toast('Split ratios must sum to 1.0'); return; }
    setExporting(true);
    try {
      const res = await fetch(`${API}/projects/${activeProject.id}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(exportCfg),
      });
      if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail || 'Export failed'); }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${activeProject.name.replace(/ /g, '_')}_dataset.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast('Export downloaded ✓');
    } catch (e) { toast(e.message); }
    finally { setExporting(false); }
  }, [activeProject, exportCfg, toast]);

  // ── Train ────────────────────────────────────────────────────────────────
  const startTraining = useCallback(async () => {
    if (!activeProject) return;
    const total = exportCfg.train_ratio + exportCfg.val_ratio + exportCfg.test_ratio;
    if (Math.abs(total - 1.0) > 0.01) { toast('Split ratios must sum to 1.0'); return; }

    // Count images eligible for training (annotated / reviewed / approved with annotations)
    const readyImages = images.filter(img =>
      ['annotated', 'reviewed', 'approved'].includes(img.status) && img.annotation_count > 0
    );
    const nTrain = Math.max(1, Math.round(readyImages.length * exportCfg.train_ratio));
    const nVal   = Math.max(0, Math.round(readyImages.length * exportCfg.val_ratio));
    const nTest  = readyImages.length - nTrain - nVal;

    const confirmed = window.confirm(
      `Ready to train on ${readyImages.length} image${readyImages.length !== 1 ? 's' : ''}:\n` +
      `  • Train: ${nTrain}\n` +
      `  • Val:   ${nVal}\n` +
      `  • Test:  ${nTest}\n\n` +
      (readyImages.length === 0
        ? '⚠️  No images are marked as annotated — training will fail.\nContinue anyway?'
        : 'Start training?')
    );
    if (!confirmed) return;

    setTraining(true);
    setTrainJob(null);
    if (trainPollRef.current) { clearInterval(trainPollRef.current); trainPollRef.current = null; }
    try {
      const payload = {
        ...trainCfg,
        fmt: exportCfg.fmt,
        train_ratio: exportCfg.train_ratio,
        val_ratio: exportCfg.val_ratio,
        test_ratio: exportCfg.test_ratio,
        shuffle: exportCfg.shuffle,
      };
      const res = await fetch(`${API}/projects/${activeProject.id}/train`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail || 'Failed to start training'); }
      const job = await res.json();
      setTrainJob(job);
      toast('Training job started ✓');
      // Poll for status every 5 seconds
      trainPollRef.current = setInterval(async () => {
        try {
          const s = await apiFetch(`${API}/projects/${activeProject.id}/train/${job.job_id}`);
          setTrainJob(s);
          if (s.status === 'completed' || s.status === 'failed') {
            clearInterval(trainPollRef.current);
            trainPollRef.current = null;
            setTraining(false);
            toast(s.status === 'completed' ? 'Training complete ✓' : 'Training failed ✗');
          }
        } catch (_) {}
      }, 2000);
    } catch (e) {
      toast(e.message);
      setTraining(false);
    }
  }, [activeProject, exportCfg, trainCfg, images, toast]);

  // Drag-over / drop for image upload
  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
    if (files.length) uploadImages(files);
  }, [uploadImages]);

  // ── Render: Project list ─────────────────────────────────────────────────

  if (view === 'projects') {
    return (
      <div className="ydt-root">
        <Toast msg={toastMsg} />
        <div className="ydt-projects-page">
          <h2 className="ydt-heading"><i className="fas fa-database" /> RF-DETR Dataset Projects</h2>

          {/* Create project form */}
          <form className="ydt-create-form" onSubmit={createProject}>
            <input
              className="wiz-input"
              placeholder="Project name"
              value={newProjName}
              onChange={e => setNewProjName(e.target.value)}
              required
            />
            <input
              className="wiz-input"
              placeholder="Description (optional)"
              value={newProjDesc}
              onChange={e => setNewProjDesc(e.target.value)}
            />
            <button className="wiz-btn wiz-btn--primary" type="submit" disabled={creating || !newProjName.trim()}>
              {creating ? <i className="fas fa-spinner fa-spin" /> : <i className="fas fa-plus" />} Create Project
            </button>
          </form>

          {!loading && projects.length === 0 && <p className="ydt-empty">No projects yet — create one above.</p>}
          <div className="ydt-project-grid" style={{ position: 'relative', minHeight: '80px' }}>
            {loading && (
              <div className="ydt-projects-loading">
                <i className="fas fa-spinner fa-spin" /> Loading…
              </div>
            )}
            {projects.map(p => (
              <div key={p.id} className="ydt-project-card">
                <div className="ydt-project-card__header">
                  <span className="ydt-project-card__name">{p.name}</span>
                  <button className="ydt-icon-btn ydt-icon-btn--danger" title="Delete project" onClick={() => deleteProject(p.id)}>
                    <i className="fas fa-trash" />
                  </button>
                </div>
                {p.description && <p className="ydt-project-card__desc">{p.description}</p>}
                <div className="ydt-project-card__stats">
                  <span><i className="fas fa-images" /> {p.image_count} images</span>
                  <span><i className="fas fa-tag" /> {p.annotation_count} annotations</span>
                  <span><i className="fas fa-th" /> {(p.grid_v_lines?.length ?? 0) + 1}×{(p.grid_h_lines?.length ?? 0) + 1} grid</span>
                </div>
                <button className="wiz-btn wiz-btn--primary ydt-open-btn" onClick={() => openProject(p)}>
                  <i className="fas fa-folder-open" /> Open
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Render: Annotator ────────────────────────────────────────────────────

  return (
    <div className="ydt-root">
      <Toast msg={toastMsg} />

      {/* Toolbar */}
      <div className="ydt-toolbar">
        <button className="wiz-btn wiz-btn--ghost wiz-btn--sm" onClick={() => { setView('projects'); setActiveProject(null); }}>
          <i className="fas fa-arrow-left" /> Projects
        </button>
        <span className="ydt-toolbar__title">
          <i className="fas fa-folder" /> {activeProject?.name}
        </span>

        {images.length > 0 && (
          <div className="ydt-toolbar__stats">
            <span className="ydt-stat-chip ydt-stat-chip--annotated">
              <i className="fas fa-check-circle" />
              {images.filter(i => i.status === 'annotated').length} annotated
            </span>
            <span className="ydt-stat-chip ydt-stat-chip--progress">
              <i className="fas fa-pen" />
              {images.filter(i => i.status === 'in_progress').length} in progress
            </span>
            <span className="ydt-stat-chip ydt-stat-chip--unannotated">
              <i className="fas fa-circle" />
              {images.filter(i => i.status === 'unannotated').length} unannotated
            </span>
          </div>
        )}

        <div className="ydt-toolbar__actions">
          <button
            className="wiz-btn wiz-btn--primary wiz-btn--sm"
            onClick={() => setView(view === 'export' ? 'annotator' : 'export')}
          >
            <i className="fas fa-file-export" /> {view === 'export' ? 'Back to Annotator' : 'Export'}
          </button>
          {activeImage && annotations.length > 0 && !['annotated','reviewed','approved'].includes(activeImage.status) && (
            <button className="wiz-btn wiz-btn--sm" style={{ background: '#10B981' }} onClick={markAnnotated}>
              <i className="fas fa-check" /> Mark Annotated
            </button>
          )}
        </div>
      </div>

      {/* Export panel */}
      {view === 'export' && (
        <div className="ydt-export-panel">
          {/* ── Dataset summary ── */}
          <div className="ydt-export-summary">
            <span className="ydt-export-stat ydt-export-stat--ready">
              <i className="fas fa-check-circle" />
              {images.filter(i => ['annotated','reviewed','approved'].includes(i.status) && i.annotation_count > 0).length} ready for training
            </span>
            <span className="ydt-export-stat ydt-export-stat--pend">
              <i className="fas fa-circle" />
              {images.filter(i => !['annotated','reviewed','approved'].includes(i.status) || i.annotation_count === 0).length} not ready
            </span>
          </div>

          {/* ── Export section ── */}
          <h3><i className="fas fa-file-export" /> Export Dataset</h3>
          <div className="ydt-export-grid">
            <label>
              Format
              <select className="wiz-input" value={exportCfg.fmt} onChange={e => setExportCfg(c => ({ ...c, fmt: e.target.value }))}>
                <option value="detection">Detection (bbox .txt)</option>
                <option value="segmentation">Segmentation (polygon .txt)</option>
              </select>
            </label>
            <label>
              Train ratio
              <input type="number" className="wiz-input" min={0} max={1} step={0.05}
                value={exportCfg.train_ratio} onChange={e => setExportCfg(c => ({ ...c, train_ratio: +e.target.value }))} />
            </label>
            <label>
              Val ratio
              <input type="number" className="wiz-input" min={0} max={1} step={0.05}
                value={exportCfg.val_ratio} onChange={e => setExportCfg(c => ({ ...c, val_ratio: +e.target.value }))} />
            </label>
            <label>
              Test ratio
              <input type="number" className="wiz-input" min={0} max={1} step={0.05}
                value={exportCfg.test_ratio} onChange={e => setExportCfg(c => ({ ...c, test_ratio: +e.target.value }))} />
            </label>
            <label className="ydt-export-shuffle">
              <input type="checkbox" checked={exportCfg.shuffle} onChange={e => setExportCfg(c => ({ ...c, shuffle: e.target.checked }))} />
              Shuffle images
            </label>
          </div>
          <p className="ydt-export-hint">
            Only images marked <strong>annotated</strong>, <strong>reviewed</strong>, or <strong>approved</strong> with at least one bounding-box are included.
          </p>
          <button className="wiz-btn wiz-btn--primary" onClick={runExport} disabled={exporting}>
            {exporting ? <><i className="fas fa-spinner fa-spin" /> Exporting…</> : <><i className="fas fa-download" /> Download Dataset Zip</>}
          </button>

          {/* ── Auto-Train section ── */}
          <h3 style={{ marginTop: '2rem' }}><i className="fas fa-robot" /> Auto-Train on Docker</h3>
          <p className="ydt-export-hint">
            Exports the ready images, converts labels to COCO, and runs RF-DETR training. Results land in <code>training/runs/</code>.
          </p>
          <div className="ydt-export-grid">
            <label>
              Base model
              <select className="wiz-input" value={trainCfg.model_name} onChange={e => setTrainCfg(c => ({ ...c, model_name: e.target.value }))}>
                <optgroup label="Base Models">
                  {availableModels.filter(m => !m.project).map(m => (
                    <option key={m.path} value={m.weight_file}>{m.weight_file}</option>
                  ))}
                </optgroup>
                {availableModels.some(m => m.project) && (
                  <optgroup label="Trained Models">
                    {availableModels.filter(m => m.project).map(m => (
                      <option key={m.path} value={m.path}>{m.label}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </label>
            <label>
              Epochs
              <input type="number" className="wiz-input" min={1} max={1000}
                value={trainCfg.epochs} onChange={e => setTrainCfg(c => ({ ...c, epochs: +e.target.value }))} />
            </label>
            <label>
              Batch size
              <input type="number" className="wiz-input" min={1} max={512}
                value={trainCfg.batch_size} onChange={e => setTrainCfg(c => ({ ...c, batch_size: +e.target.value }))} />
            </label>
            <label>
              Image size
              <input type="number" className="wiz-input" min={32} max={4096} step={32}
                value={trainCfg.img_size} onChange={e => setTrainCfg(c => ({ ...c, img_size: +e.target.value }))} />
            </label>
            <label>
              Learning rate
              <input type="number" className="wiz-input" min={0.0001} max={1} step={0.001}
                value={trainCfg.lr0} onChange={e => setTrainCfg(c => ({ ...c, lr0: +e.target.value }))} />
            </label>
            <label>
              Patience
              <input type="number" className="wiz-input" min={0} max={300}
                value={trainCfg.patience} onChange={e => setTrainCfg(c => ({ ...c, patience: +e.target.value }))} />
            </label>
            <label>
              Device
              <select className="wiz-input" value={trainCfg.device} onChange={e => setTrainCfg(c => ({ ...c, device: e.target.value }))}>
                <option value="cpu">cpu</option>
                <option value="0">GPU 0</option>
                <option value="0,1">GPU 0,1</option>
              </select>
            </label>
            <label>
              Freeze layers
              <select className="wiz-input" value={trainCfg.freeze} onChange={e => setTrainCfg(c => ({ ...c, freeze: +e.target.value }))}>
                <option value={0}>0 — off (full retrain)</option>
                <option value={10}>10 — head only (recommended)</option>
                <option value={21}>21 — full backbone</option>
              </select>
            </label>
            <label>
              Run name
              <input type="text" className="wiz-input" placeholder="run1"
                value={trainCfg.run_name} onChange={e => setTrainCfg(c => ({ ...c, run_name: e.target.value }))} />
            </label>
          </div>
          <button
            className="wiz-btn wiz-btn--primary"
            style={{ background: '#8B5CF6', marginTop: '0.75rem' }}
            onClick={startTraining}
            disabled={training || exporting}
          >
            {training
              ? <><i className="fas fa-spinner fa-spin" /> Training{trainJob ? ` (${trainJob.status})` : '…'}</>
              : <><i className="fas fa-play-circle" /> Export &amp; Train</>}
          </button>

          {/* ── Job status card ── */}
          {trainJob && (
            <div className={`ydt-train-job ydt-train-job--${trainJob.status}`}>
              <div className="ydt-train-job__header">
                <span className="ydt-train-job__title">
                  <i className={
                    trainJob.status === 'completed' ? 'fas fa-check-circle' :
                    trainJob.status === 'failed'    ? 'fas fa-times-circle' :
                    'fas fa-spinner fa-spin'
                  } />
                  {' '}{trainJob.project_name} / {trainJob.run_name}
                </span>
                <span className={`ydt-train-job__badge ydt-train-job__badge--${trainJob.status}`}>
                  {trainJob.status}
                </span>
              </div>
              {trainJob.status === 'completed' && (
                <p className="ydt-train-job__hint">
                  Weights saved to <code>training/runs/{trainJob.project_name}/{trainJob.run_name}/weights/best.pt</code>
                </p>
              )}
              {trainJob.logs && (
                <pre className="ydt-train-job__logs" ref={logBoxRef}>{trainJob.logs}</pre>
              )}
            </div>
          )}
        </div>
      )}

      {/* Main annotator layout */}
      {view === 'annotator' && (
        <div className="ydt-annotator">
          {/* Left: Image list */}
          <aside className="ydt-image-list">
            <div className="ydt-panel-title">
              Images
              <span className="ydt-pill">{images.length}</span>
            </div>

            {/* ── Workflow steps ─────────────────────────────────────────── */}
            <div className="ydt-steps">

              {/* Step 1 — Upload Images */}
              <div className={`ydt-step${step1Done ? ' ydt-step--done' : ''}${sidebarStep === 1 ? ' ydt-step--open' : ''}`}>
                <button className="ydt-step__header" onClick={() => setSidebarStep(v => v === 1 ? 0 : 1)}>
                  <span className={`ydt-step__num${step1Done ? ' ydt-step__num--done' : sidebarStep === 1 ? ' ydt-step__num--active' : ''}`}>
                    {step1Done ? <i className="fas fa-check" /> : '1'}
                  </span>
                  <span className="ydt-step__label">Upload Images</span>
                  {step1Done && <span className="ydt-step__badge">{images.length} img{images.length !== 1 ? 's' : ''}</span>}
                  <i className={`fas fa-chevron-${sidebarStep === 1 ? 'up' : 'down'} ydt-step__chevron`} />
                </button>
                {sidebarStep === 1 && (
                  <div className="ydt-step__content">
                    <div className="ydt-upload-tabs">
                      <button
                        className={`ydt-upload-tab ${uploadMode === 'images' ? 'ydt-upload-tab--active' : ''}`}
                        onClick={() => setUploadMode('images')}
                      >
                        <i className="fas fa-image" /> Photos
                      </button>
                      <button
                        className={`ydt-upload-tab ${uploadMode === 'video' ? 'ydt-upload-tab--active' : ''}`}
                        onClick={() => setUploadMode('video')}
                      >
                        <i className="fas fa-film" /> From Video
                      </button>
                    </div>
                    {uploadMode === 'images' && (
                      <label
                        className="ydt-dropzone"
                        onDragOver={e => e.preventDefault()}
                        onDrop={handleDrop}
                      >
                        <i className="fas fa-cloud-upload-alt" />
                        <span>Drop images or click to upload</span>
                        <input
                          type="file"
                          accept="image/*"
                          multiple
                          style={{ display: 'none' }}
                          onChange={e => uploadImages(Array.from(e.target.files || []))}
                        />
                      </label>
                    )}
                    {uploadMode === 'video' && (
                      <div className="ydt-video-form">
                        <label className="ydt-video-pick">
                          <i className="fas fa-video" />
                          <span>{videoFile ? videoFile.name : 'Choose video file…'}</span>
                          <input
                            ref={videoInputRef}
                            type="file"
                            accept="video/*"
                            style={{ display: 'none' }}
                            onClick={e => { e.currentTarget.value = ''; }}
                            onChange={e => setVideoFile(e.target.files?.[0] || null)}
                          />
                        </label>
                        <div className="ydt-video-mode">
                          <button
                            className={`ydt-vmode-btn ${extractMode === 'interval' ? 'ydt-vmode-btn--active' : ''}`}
                            onClick={() => { setExtractMode('interval'); setExtractValue(2); }}
                          >
                            Every N secs
                          </button>
                          <button
                            className={`ydt-vmode-btn ${extractMode === 'count' ? 'ydt-vmode-btn--active' : ''}`}
                            onClick={() => { setExtractMode('count'); setExtractValue(30); }}
                          >
                            N total frames
                          </button>
                        </div>
                        <div className="ydt-video-value">
                          <input
                            type="number"
                            min="1"
                            step={extractMode === 'interval' ? 0.5 : 1}
                            value={extractValue}
                            onChange={e => setExtractValue(Math.max(1, Number(e.target.value)))}
                            className="ydt-input"
                            style={{ width: 70 }}
                          />
                          <span className="ydt-video-unit">
                            {extractMode === 'interval' ? 'second(s) interval' : 'frames total'}
                          </span>
                        </div>
                        <button
                          className="wiz-btn wiz-btn--primary wiz-btn--sm"
                          style={{ width: '100%' }}
                          onClick={extractFrames}
                          disabled={!videoFile || extracting}
                        >
                          {extracting
                            ? <><i className="fas fa-spinner fa-spin" /> Extracting…</>
                            : <><i className="fas fa-cut" /> Extract Frames</>}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className={`ydt-step__connector${step1Done ? ' ydt-step__connector--done' : ''}`} />

              {/* Step 2 — Setup Grid */}
              <div className={`ydt-step${step2Done ? ' ydt-step--done' : ''}${sidebarStep === 2 ? ' ydt-step--open' : ''}${!step1Done ? ' ydt-step--disabled' : ''}`}>
                <button className="ydt-step__header" onClick={() => step1Done && setSidebarStep(v => v === 2 ? 0 : 2)} disabled={!step1Done}>
                  <span className={`ydt-step__num${step2Done ? ' ydt-step__num--done' : sidebarStep === 2 ? ' ydt-step__num--active' : ''}`}>
                    {step2Done ? <i className="fas fa-check" /> : '2'}
                  </span>
                  <span className="ydt-step__label">Setup Grid</span>
                  {step2Done && <span className="ydt-step__badge">{vLines.length + 1}×{hLines.length + 1}</span>}
                  <i className={`fas fa-chevron-${sidebarStep === 2 ? 'up' : 'down'} ydt-step__chevron`} />
                </button>
                {sidebarStep === 2 && step1Done && (
                  <div className="ydt-step__content">
                    <p className="ydt-step__hint">Draw grid lines to divide your lot into detection zones for live analysis.</p>
                    <button
                      className="wiz-btn wiz-btn--primary wiz-btn--sm"
                      style={{ width: '100%' }}
                      onClick={() => setShowGridEditor(true)}
                    >
                      <i className="fas fa-th" /> {step2Done ? 'Edit Grid' : 'Open Grid Editor'}
                    </button>
                    {step2Done && (
                      <p className="ydt-step__done-hint">
                        <i className="fas fa-check-circle" style={{ color: '#10B981' }} /> {vLines.length + 1} col{vLines.length + 1 !== 1 ? 's' : ''} × {hLines.length + 1} row{hLines.length + 1 !== 1 ? 's' : ''}
                      </p>
                    )}
                  </div>
                )}
              </div>

              <div className={`ydt-step__connector${step2Done ? ' ydt-step__connector--done' : ''}`} />

              {/* Step 3 — Auto-Annotate */}
              <div className={`ydt-step${step3Done ? ' ydt-step--done' : ''}${sidebarStep === 3 ? ' ydt-step--open' : ''}${!step1Done ? ' ydt-step--disabled' : ''}`}>
                <button className="ydt-step__header" onClick={() => step1Done && setSidebarStep(v => v === 3 ? 0 : 3)} disabled={!step1Done}>
                  <span className={`ydt-step__num${step3Done ? ' ydt-step__num--done' : (sidebarStep === 3 || autoAnnotating) ? ' ydt-step__num--active' : ''}`}>
                    {autoAnnotating ? <i className="fas fa-spinner fa-spin" /> : step3Done ? <i className="fas fa-check" /> : '3'}
                  </span>
                  <span className="ydt-step__label">{autoAnnotating ? 'Auto-annotating…' : 'Auto-Annotate'}</span>
                  {step3Done && <span className="ydt-step__badge">{autoAnnResult?.created ?? 0} boxes</span>}
                  <i className={`fas fa-chevron-${sidebarStep === 3 ? 'up' : 'down'} ydt-step__chevron`} />
                </button>
                {sidebarStep === 3 && step1Done && (
                  <div className="ydt-step__content">
                    <p className="ydt-auto-ann__desc">
                      Runs <strong>RF-DETR inference</strong> on every image and saves detected vehicles as bounding-box annotations. Review and adjust afterward.
                    </p>
                    <label className="ydt-auto-ann__field">
                      <span>Model</span>
                      <select
                        className="wiz-input wiz-input--sm"
                        value={autoAnnCfg.model_path}
                        onChange={e => setAutoAnnCfg(c => ({ ...c, model_path: e.target.value }))}
                      >
                        <optgroup label="Base Models">
                          {availableModels.filter(m => !m.project).map(m => (
                            <option key={m.path} value={m.weight_file}>{m.weight_file}</option>
                          ))}
                        </optgroup>
                        {availableModels.some(m => m.project) && (
                          <optgroup label="Trained Models">
                            {availableModels.filter(m => m.project).map(m => (
                              <option key={m.path} value={m.path}>{m.label}</option>
                            ))}
                          </optgroup>
                        )}
                      </select>
                    </label>
                    <label className="ydt-auto-ann__field">
                      <span>Confidence threshold</span>
                      <div className="ydt-auto-ann__slider-row">
                        <input
                          type="range" min={0.05} max={0.9} step={0.05}
                          value={autoAnnCfg.conf}
                          onChange={e => setAutoAnnCfg(c => ({ ...c, conf: +e.target.value }))}
                        />
                        <span className="ydt-auto-ann__slider-val">{autoAnnCfg.conf.toFixed(2)}</span>
                      </div>
                      <small>Lower = more detections. Recommended: 0.20–0.35</small>
                    </label>
                    <label className="ydt-auto-ann__field ydt-auto-ann__field--row">
                      <input
                        type="checkbox"
                        checked={autoAnnCfg.overwrite}
                        onChange={e => setAutoAnnCfg(c => ({ ...c, overwrite: e.target.checked }))}
                      />
                      <span>Overwrite existing annotations</span>
                    </label>
                    {autoAnnResult && (
                      <div className="ydt-auto-ann__result">
                        <span className="ydt-auto-ann__result-chip ydt-auto-ann__result-chip--ok">
                          <i className="fas fa-check" /> {autoAnnResult.created} boxes created
                        </span>
                        <span className="ydt-auto-ann__result-chip">
                          <i className="fas fa-images" /> {autoAnnResult.processed} processed
                        </span>
                        {autoAnnResult.skipped > 0 && (
                          <span className="ydt-auto-ann__result-chip ydt-auto-ann__result-chip--skip">
                            <i className="fas fa-forward" /> {autoAnnResult.skipped} skipped
                          </span>
                        )}
                        {autoAnnResult.errors > 0 && (
                          <span className="ydt-auto-ann__result-chip ydt-auto-ann__result-chip--err">
                            <i className="fas fa-exclamation-triangle" /> {autoAnnResult.errors} errors
                          </span>
                        )}
                      </div>
                    )}
                    <button
                      className="wiz-btn wiz-btn--primary wiz-btn--sm"
                      style={{ width: '100%', marginTop: '0.5rem', background: '#8B5CF6' }}
                      onClick={runAutoAnnotate}
                      disabled={autoAnnotating}
                    >
                      {autoAnnotating
                        ? <><i className="fas fa-spinner fa-spin" /> Running RF-DETR…</>
                        : <><i className="fas fa-magic" /> Run Auto-Annotate</>}
                    </button>
                  </div>
                )}
              </div>

            </div>

            <div className="ydt-img-items">
              {images.map(img => (
                <div
                  key={img.id}
                  className={`ydt-img-item ${activeImage?.id === img.id ? 'ydt-img-item--active' : ''}`}
                  onClick={() => selectImage(img)}
                >
                  <span
                    className="ydt-status-dot"
                    style={{ background: STATUS_COLORS[img.status] || '#6B7280' }}
                    title={img.status}
                  />
                  <span className="ydt-img-item__name" title={img.original_filename}>
                    {img.original_filename.length > 22 ? img.original_filename.slice(0, 20) + '…' : img.original_filename}
                  </span>
                  <span className="ydt-img-item__count">{img.annotation_count}</span>
                  <button
                    className="ydt-icon-btn ydt-icon-btn--danger ydt-img-del"
                    onClick={e => { e.stopPropagation(); deleteImage(img.id); }}
                    title="Delete image"
                  >
                    <i className="fas fa-times" />
                  </button>
                </div>
              ))}
            </div>
          </aside>

          {/* Centre: Canvas */}
          <main className="ydt-canvas-area">
            {!activeImage ? (
              <div className="ydt-canvas-placeholder">
                <i className="fas fa-mouse-pointer" />
                <p>Select an image from the left panel to start annotating</p>
                {classes.length === 0 && <p className="ydt-hint-small">Add labels first (e.g. "empty", "occupied") using the quick-start buttons on the right.</p>}
              </div>
            ) : (
              <>
                <div className="ydt-canvas-meta">
                  <span>{activeImage.original_filename}</span>
                  <span>{activeImage.width} × {activeImage.height}</span>
                  <span style={{ color: STATUS_COLORS[activeImage.status] }}>{activeImage.status}</span>
                  {activeClassId && <span style={{ color: classes.find(c => c.id === activeClassId)?.color }}>
                  labeling as: <strong>{classes.find(c => c.id === activeClassId)?.name}</strong>
                  </span>}
                  {!activeClassId && classes.length > 0 && <span style={{ color: '#F59E0B' }}>⚠️ select a label on the right</span>}
                  <button
                    className={`wiz-btn wiz-btn--sm ${showGrid ? 'wiz-btn--primary' : 'wiz-btn--ghost'}`}
                    style={{ marginLeft: 'auto', fontSize: '0.72rem', padding: '3px 10px' }}
                    onClick={() => setShowGrid(v => !v)}
                    title="Toggle segment grid overlay"
                  >
                    <i className="fas fa-th" /> {showGrid ? 'Grid On' : 'Grid Off'}
                  </button>
                  <button
                    className="wiz-btn wiz-btn--ghost wiz-btn--sm"
                    style={{ fontSize: '0.72rem', padding: '3px 10px' }}
                    onClick={() => setShowGridEditor(true)}
                    title="Edit grid line positions"
                  >
                    <i className="fas fa-pencil-alt" /> Edit Grid
                  </button>
                </div>
                <div className="ydt-canvas-wrap">
                  <canvas
                    ref={canvasRef}
                    className="ydt-canvas"
                    style={{ cursor: selectedAnnId ? 'move' : 'crosshair' }}
                    onMouseDown={handleMouseDown}
                    onMouseMove={handleMouseMove}
                    onMouseUp={handleMouseUp}
                    onMouseLeave={handleMouseUp}
                  />
                </div>
                <div className="ydt-canvas-hints">
                  <span><kbd>click-drag</kbd> draw box</span>
                  <span><kbd>click</kbd> select</span>
                  <span><kbd>Del</kbd> delete</span>
                  <span><kbd>Esc</kbd> deselect</span>
                </div>
              </>
            )}

            {/* Grid editor overlay */}
            {/* ── Batch-grid modal ─────────────────────────────────────────────────── */}
            {batchGridModal.show && (
              <div className="ydt-grid-editor-overlay">
                <div className="ydt-grid-editor-panel">
                  <div className="ydt-grid-editor-header">
                    <h3><i className="fas fa-layer-group" /> Set Grid for this Batch</h3>
                    <p>
                      Adjust the grid for the <strong>{batchGridModal.imageIds.length} image(s)</strong> just added from
                      &nbsp;<em>{batchGridModal.groupName}</em>. These images will use this grid independently
                      of the project-wide grid. Click <strong>Skip</strong> to use the project grid instead.
                    </p>
                  </div>

                  <div className="wiz-grid-layout">
                    <div className="wiz-canvas-wrap">
                      <canvas
                        ref={gridEditorCanvasRef}
                        className="wiz-canvas"
                        style={{ cursor: gridDragging ? (gridDragging.axis === 'h' ? 'ns-resize' : 'ew-resize') : 'crosshair' }}
                        onMouseDown={handleGridMouseDown}
                        onMouseMove={handleGridMouseMove}
                        onMouseUp={handleGridMouseUp}
                        onMouseLeave={handleGridMouseUp}
                      />
                    </div>

                    <div className="wiz-grid-controls">
                      <div className="wiz-ctrl-section">
                        <h4><i className="fas fa-grip-lines" style={{color:'#00D4FF'}} /> Horizontal Lines ({hLines.length})</h4>
                        <div className="wiz-ctrl-btns">
                          <button className="wiz-btn wiz-btn--sm" onClick={() => { setHLines(p => [...p, 0.5].sort((a,b)=>a-b)); setHLineAngles(p => [...p, 0]); }}>
                            <i className="fas fa-plus" /> Add
                          </button>
                          <button className="wiz-btn wiz-btn--sm wiz-btn--ghost" disabled={hLines.length === 0}
                            onClick={() => { setHLines(p => p.slice(0, -1)); setHLineAngles(p => p.slice(0, -1)); }}>
                            <i className="fas fa-minus" /> Remove
                          </button>
                        </div>
                        <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
                          {hLines.map((_, idx) => (
                            <div key={`bh-angle-${idx}`} style={{ marginBottom: '0.6rem' }}>
                              <label style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)', display: 'block', marginBottom: '0.2rem' }}>
                                H{idx + 1} Angle: {Math.round(hLineAngles[idx] ?? 0)}°
                              </label>
                              <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                                <input type="range" min={-30} max={30} step={1} value={Math.round(hLineAngles[idx] ?? 0)}
                                  onChange={e => setHLineAngles(prev => { const n=[...prev]; n[idx]=clampAngle(e.target.value); return n; })}
                                  style={{ flex: 1, height: '20px' }} />
                                <input type="number" min={-30} max={30} step={1} value={Math.round(hLineAngles[idx] ?? 0)}
                                  onChange={e => setHLineAngles(prev => { const n=[...prev]; n[idx]=clampAngle(e.target.value); return n; })}
                                  style={{ width: '60px', fontSize: '0.75rem', padding: '4px 6px' }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="wiz-ctrl-section">
                        <h4><i className="fas fa-grip-lines-vertical" style={{color:'#A855F7'}} /> Vertical Lines ({vLines.length})</h4>
                        <div className="wiz-ctrl-btns">
                          <button className="wiz-btn wiz-btn--sm" onClick={() => { setVLines(p => [...p, 0.5].sort((a,b)=>a-b)); setVLineAngles(p => [...p, 0]); }}>
                            <i className="fas fa-plus" /> Add
                          </button>
                          <button className="wiz-btn wiz-btn--sm wiz-btn--ghost" disabled={vLines.length === 0}
                            onClick={() => { setVLines(p => p.slice(0, -1)); setVLineAngles(p => p.slice(0, -1)); }}>
                            <i className="fas fa-minus" /> Remove
                          </button>
                        </div>
                        <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
                          {vLines.map((_, idx) => (
                            <div key={`bv-angle-${idx}`} style={{ marginBottom: '0.6rem' }}>
                              <label style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)', display: 'block', marginBottom: '0.2rem' }}>
                                V{idx + 1} Angle: {Math.round(vLineAngles[idx] ?? 0)}°
                              </label>
                              <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                                <input type="range" min={-30} max={30} step={1} value={Math.round(vLineAngles[idx] ?? 0)}
                                  onChange={e => setVLineAngles(prev => { const n=[...prev]; n[idx]=clampAngle(e.target.value); return n; })}
                                  style={{ flex: 1, height: '20px' }} />
                                <input type="number" min={-30} max={30} step={1} value={Math.round(vLineAngles[idx] ?? 0)}
                                  onChange={e => setVLineAngles(prev => { const n=[...prev]; n[idx]=clampAngle(e.target.value); return n; })}
                                  style={{ width: '60px', fontSize: '0.75rem', padding: '4px 6px' }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <p className="wiz-ctrl-hint">
                        <i className="fas fa-info-circle" /> This grid applies only to the images in this batch. Other images keep their own grid.
                      </p>

                      <div className="ydt-grid-editor-actions">
                        <button className="wiz-btn wiz-btn--primary" onClick={saveBatchGrid} disabled={savingBatchGrid}>
                          {savingBatchGrid
                            ? <><i className="fas fa-spinner fa-spin" /> Saving…</>
                            : <><i className="fas fa-save" /> Save Grid for Batch ({batchGridModal.imageIds.length})</>}
                        </button>
                        <button className="wiz-btn wiz-btn--ghost"
                          onClick={() => setBatchGridModal({ show: false, imageIds: [], groupName: '' })}>
                          Skip (use project grid)
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {showGridEditor && (
              <div className="ydt-grid-editor-overlay">
                <div className="ydt-grid-editor-panel">
                  <div className="ydt-grid-editor-header">
                    <h3><i className="fas fa-th" /> Setup Detection Grid</h3>
                    <p>Drag the dashed lines to align zones with your parking lot layout. Zones auto-label (TL, TR, ML…). Lines are saved per project.</p>
                  </div>

                  <div className="wiz-grid-layout">
                    <div className="wiz-canvas-wrap">
                      <canvas
                        ref={gridEditorCanvasRef}
                        className="wiz-canvas"
                        style={{ cursor: gridDragging ? (gridDragging.axis === 'h' ? 'ns-resize' : 'ew-resize') : 'crosshair' }}
                        onMouseDown={handleGridMouseDown}
                        onMouseMove={handleGridMouseMove}
                        onMouseUp={handleGridMouseUp}
                        onMouseLeave={handleGridMouseUp}
                      />
                    </div>

                    <div className="wiz-grid-controls">
                      <div className="wiz-ctrl-section">
                        <h4><i className="fas fa-grip-lines" style={{color:'#00D4FF'}} /> Horizontal Lines ({hLines.length})</h4>
                        <div className="wiz-ctrl-btns">
                          <button className="wiz-btn wiz-btn--sm" onClick={() => {
                            setHLines(p => [...p, 0.5].sort((a,b)=>a-b));
                            setHLineAngles(p => [...p, 0]);
                          }}>
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
                        <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
                          {hLines.map((_, idx) => (
                            <div key={`h-angle-${idx}`} style={{ marginBottom: '0.6rem' }}>
                              <label style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)', display: 'block', marginBottom: '0.2rem' }}>
                                H{idx + 1} Angle: {Math.round(hLineAngles[idx] ?? 0)}°
                              </label>
                              <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                                <input
                                  type="range"
                                  min={-30}
                                  max={30}
                                  step={1}
                                  value={Math.round(hLineAngles[idx] ?? 0)}
                                  onChange={e => setHLineAngles(prev => {
                                    const n = [...prev];
                                    n[idx] = clampAngle(e.target.value);
                                    return n;
                                  })}
                                  style={{ flex: 1, height: '20px' }}
                                />
                                <input
                                  type="number"
                                  min={-30}
                                  max={30}
                                  step={1}
                                  value={Math.round(hLineAngles[idx] ?? 0)}
                                  onChange={e => setHLineAngles(prev => {
                                    const n = [...prev];
                                    n[idx] = clampAngle(e.target.value);
                                    return n;
                                  })}
                                  style={{ width: '60px', fontSize: '0.75rem', padding: '4px 6px' }}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="wiz-ctrl-section">
                        <h4><i className="fas fa-grip-lines-vertical" style={{color:'#A855F7'}} /> Vertical Lines ({vLines.length})</h4>
                        <div className="wiz-ctrl-btns">
                          <button className="wiz-btn wiz-btn--sm" onClick={() => {
                            setVLines(p => [...p, 0.5].sort((a,b)=>a-b));
                            setVLineAngles(p => [...p, 0]);
                          }}>
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
                        <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
                          {vLines.map((_, idx) => (
                            <div key={`v-angle-${idx}`} style={{ marginBottom: '0.6rem' }}>
                              <label style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)', display: 'block', marginBottom: '0.2rem' }}>
                                V{idx + 1} Angle: {Math.round(vLineAngles[idx] ?? 0)}°
                              </label>
                              <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                                <input
                                  type="range"
                                  min={-30}
                                  max={30}
                                  step={1}
                                  value={Math.round(vLineAngles[idx] ?? 0)}
                                  onChange={e => setVLineAngles(prev => {
                                    const n = [...prev];
                                    n[idx] = clampAngle(e.target.value);
                                    return n;
                                  })}
                                  style={{ flex: 1, height: '20px' }}
                                />
                                <input
                                  type="number"
                                  min={-30}
                                  max={30}
                                  step={1}
                                  value={Math.round(vLineAngles[idx] ?? 0)}
                                  onChange={e => setVLineAngles(prev => {
                                    const n = [...prev];
                                    n[idx] = clampAngle(e.target.value);
                                    return n;
                                  })}
                                  style={{ width: '60px', fontSize: '0.75rem', padding: '4px 6px' }}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <p className="wiz-ctrl-hint">
                        <i className="fas fa-info-circle" /> Drag the dashed lines to reposition and tune each line angle for tilted camera views.
                      </p>

                      <div className="ydt-grid-editor-actions">
                        <button className="wiz-btn wiz-btn--primary" onClick={saveGrid} disabled={savingGrid}>
                          {savingGrid ? <><i className="fas fa-spinner fa-spin" /> Saving…</> : <><i className="fas fa-save" /> Apply Grid</>}
                        </button>
                        <button className="wiz-btn wiz-btn--ghost" onClick={() => setShowGridEditor(false)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </main>

          {/* Right: Labels + Annotations */}
          <aside className="ydt-right-panel">
            {/* Label Manager */}
            <div className="ydt-panel-title">Object Labels</div>
            <p className="ydt-label-hint">
              Click a label to select it, then draw a box on the image.
            </p>
            <div className="ydt-class-list">
              {classes.map(cls => {
                const isPreset = PRESET_NAMES.has(cls.name);
                return (
                  <div
                    key={cls.id}
                    className={`ydt-class-item ${activeClassId === cls.id ? 'ydt-class-item--active' : ''}`}
                    onClick={() => setActiveClassId(cls.id)}
                    title={isPreset ? 'Preset label' : 'Custom label — limited training data'}
                  >
                    <span className="ydt-class-swatch" style={{ background: cls.color }} />
                    <span className="ydt-class-name">{cls.name}</span>
                    {isPreset
                      ? <span className="ydt-preset-badge" title="Preset">P</span>
                      : <button
                          className="ydt-icon-btn ydt-icon-btn--danger"
                          style={{ marginLeft: 'auto' }}
                          onClick={e => { e.stopPropagation(); deleteClass(cls.id); }}
                          title="Remove custom label"
                        ><i className="fas fa-times" /></button>
                    }
                  </div>
                );
              })}
            </div>

            {/* Add custom label */}
            <div className="ydt-custom-label-section">
              <div className="ydt-custom-label-header">
                <i className="fas fa-tag" /> Add Custom Label
              </div>
              <form className="ydt-custom-label-form" onSubmit={addClass}>
                <input
                  className="wiz-input"
                  placeholder="Label name (e.g. cyclist)"
                  value={newClassName}
                  onChange={e => setNewClassName(e.target.value)}
                  required
                  style={{ flex: 1, fontSize: '0.78rem' }}
                />
                <input
                  type="color"
                  value={newClassColor}
                  onChange={e => setNewClassColor(e.target.value)}
                  title="Pick label colour"
                  style={{ width: 30, height: 30, padding: 2, border: 'none', background: 'none', cursor: 'pointer' }}
                />
                <button
                  className="wiz-btn wiz-btn--primary wiz-btn--sm"
                  type="submit"
                  disabled={addingClass || !newClassName.trim()}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {addingClass ? <i className="fas fa-spinner fa-spin" /> : <i className="fas fa-plus" />} Add
                </button>
              </form>
              <p className="ydt-custom-label-hint">
                <i className="fas fa-info-circle" /> Custom labels have limited base-model coverage — use them to build a fine-tuned dataset.
              </p>
            </div>

            {/* Annotations list */}
            {activeImage && (
              <>
                <div className="ydt-panel-title" style={{ marginTop: 16 }}>
                  Annotations
                  <span className="ydt-pill">{annotations.length}</span>
                  <label className="ydt-import-btn" title="Import label .txt files">
                    <i className="fas fa-file-import" /> Import
                    <input type="file" accept=".txt" style={{ display: 'none' }} onChange={importLabels} />
                  </label>
                </div>
                <div className="ydt-ann-list">
                  {annotations.map((ann, idx) => (
                    <div
                      key={ann.id}
                      className={`ydt-ann-item ${selectedAnnId === ann.id ? 'ydt-ann-item--active' : ''}`}
                      onClick={() => { setSelectedAnnId(ann.id); drawCanvas(); }}
                    >
                      <span className="ydt-class-swatch" style={{ background: ann.class_color }} />
                      <span className="ydt-ann-name">{ann.class_name}</span>
                      {showGrid && ann.bbox && (
                        <span
                          className="ydt-ann-zone"
                          title={`Grid zone: ${getGridCell(ann.bbox.cx, ann.bbox.cy, activeImage?.group_grid_h_lines ?? hLines, activeImage?.group_grid_v_lines ?? vLines, activeImage?.group_grid_h_line_angles ?? hLineAngles, activeImage?.group_grid_v_line_angles ?? vLineAngles)}`}
                        >
                          {getGridCell(ann.bbox.cx, ann.bbox.cy, activeImage?.group_grid_h_lines ?? hLines, activeImage?.group_grid_v_lines ?? vLines, activeImage?.group_grid_h_line_angles ?? hLineAngles, activeImage?.group_grid_v_line_angles ?? vLineAngles)}
                        </span>
                      )}
                      <span className="ydt-ann-idx">#{idx + 1}</span>
                      <button
                        className="ydt-icon-btn ydt-icon-btn--danger"
                        onClick={e => { e.stopPropagation(); deleteAnnotation(ann.id); }}
                        title="Delete"
                      >
                        <i className="fas fa-times" />
                      </button>
                    </div>
                  ))}
                  {annotations.length === 0 && <p className="ydt-empty-sm">No annotations yet.</p>}
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

// ─── Toast ────────────────────────────────────────────────────────────────────

function Toast({ msg }) {
  if (!msg) return null;
  return (
    <div className="ydt-toast">
      {msg}
    </div>
  );
}
