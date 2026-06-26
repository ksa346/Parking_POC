// Normalize API base so env values like "/api" still target versioned endpoints.
const RAW_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';
const BASE = RAW_BASE.endsWith('/v1') ? RAW_BASE : `${RAW_BASE.replace(/\/$/, '')}/v1`;

async function request(endpoint, options = {}) {
  const url = `${BASE}${endpoint}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const d = body.detail[0];
      const path = Array.isArray(d.loc) ? d.loc.join('.') : 'request';
      throw new Error(`${path}: ${d.msg}`);
    }
    throw new Error(body.error || body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export function health() {
  return request('/health');
}

export function occupancy() {
  return request('/occupancy');
}

export function history(hours = 24) {
  return request(`/history?hours=${hours}`);
}

export function forecast(targetHour) {
  return request('/forecast', {
    method: 'POST',
    body: JSON.stringify({ target_hour: targetHour }),
  });
}

export function forecasts() {
  return request('/forecasts');
}

export function stats() {
  return request('/stats');
}

export function chat(message, history = []) {
  return request('/chat', {
    method: 'POST',
    body: JSON.stringify({ message, history }),
  });
}

// ── Developer Wizard API ────────────────────────────────────────────
export function uploadVideo(file) {
  const formData = new FormData();
  formData.append('file', file);
  const url = `${BASE}/developer/upload-video`;
  return fetch(url, { method: 'POST', body: formData }).then(async res => {
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (Array.isArray(body.detail) && body.detail.length > 0) {
        const d = body.detail[0];
        const path = Array.isArray(d.loc) ? d.loc.join('.') : 'request';
        throw new Error(`${path}: ${d.msg}`);
      }
      throw new Error(body.error || body.detail || `HTTP ${res.status}`);
    }
    return body;
  });
}

export function captureFrame(videoUrl, useExisting = false, timestampSeconds = 0) {
  return request('/developer/capture-frame', {
    method: 'POST',
    body: JSON.stringify({
      video_url: videoUrl,
      use_existing: useExisting,
      timestamp_seconds: timestampSeconds,
    }),
  });
}

export function estimateSpots(gridConfig, frameBase64) {
  return request('/developer/estimate-spots', {
    method: 'POST',
    body: JSON.stringify({ grid_config: gridConfig, frame_base64: frameBase64 }),
  });
}

export function previewDetection(frameBase64, parameters, gridConfig) {
  const payload = { frame_base64: frameBase64, parameters, grid_config: gridConfig };
  return request('/developer/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).catch(async (err) => {
    // Transient network failures can happen on large POST payloads through proxies.
    if (String(err?.message || '').toLowerCase().includes('failed to fetch')) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      return request('/developer/preview', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    }
    throw err;
  });
}

export function publishLocation(data) {
  return request('/developer/publish', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listPublishedLocations() {
  return request('/developer/locations');
}

export function deletePublishedLocation(locationId) {
  return request(`/developer/locations/${encodeURIComponent(locationId)}`, {
    method: 'DELETE',
  });
}

export function activateLocation(locationId) {
  return request(`/location/activate/${encodeURIComponent(locationId)}`, {
    method: 'POST',
  });
}

// Restore live feed to the default video + default zone config (for demo locations)
export function restoreDefaultLocation() {
  return request('/location/restore-default', { method: 'POST' });
}

export function listTrainedModels(wizardContext = false) {
  return request(`/developer/models${wizardContext ? '?wizard=1' : ''}`);
}

// Saves the developer's model preference for wizard previews only.
// Does NOT affect the live User Persona detection feed.
export function activateModel(modelPath) {
  return request('/developer/models/activate', {
    method: 'POST',
    body: JSON.stringify({ path: modelPath }),
  });
}

// Explicitly promotes a model to the live detection pipeline (User Persona feed).
// Separate from activateModel — must be a deliberate developer action.
export function activateLiveModel(modelPath) {
  return request('/live/model/activate', {
    method: 'POST',
    body: JSON.stringify({ path: modelPath }),
  });
}
