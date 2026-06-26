/**
 * WebSocket service — singleton that manages a persistent connection
 * and exposes subscribe/connect/disconnect for React hooks.
 */

let socket = null;
let reconnectTimer = null;
let reconnectDelay = 1000;
const MAX_DELAY = 30000;
const listeners = new Set();
const statusListeners = new Set();
let endpointIndex = 0;

function _notifyData(data) {
  listeners.forEach((fn) => {
    try { fn(data); } catch (e) { console.error('[WS] listener error', e); }
  });
}

function _notifyStatus(status) {
  statusListeners.forEach((fn) => {
    try { fn(status); } catch (e) { console.error('[WS] status listener error', e); }
  });
}

function _wsCandidates() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const host = window.location.host;

  // Always use same-origin WS URLs from the browser.
  // Container hostnames like "parking-backend" are not resolvable in user browsers.
  return [
    `${proto}://${host}/api/ws/occupancy`,
    `${proto}://${host}/ws/occupancy`,
  ];
}

function _currentWsUrl() {
  const urls = _wsCandidates();
  return urls[endpointIndex % urls.length];
}

function _advanceEndpoint() {
  endpointIndex += 1;
}

export function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const url = _currentWsUrl();
  console.info('[WS] connecting to', url);
  try {
    socket = new WebSocket(url);
  } catch (e) {
    console.warn('[WS] failed to create socket', e);
    _notifyStatus('disconnected');
    _advanceEndpoint();
    _scheduleReconnect();
    return;
  }

  socket.addEventListener('open', () => {
    console.info('[WS] connected');
    reconnectDelay = 1000;
    _notifyStatus('connected');
  });

  socket.addEventListener('message', (event) => {
    try {
      const data = JSON.parse(event.data);
      _notifyData(data);
    } catch (e) {
      console.warn('[WS] bad message', e);
    }
  });

  socket.addEventListener('close', () => {
    _notifyStatus('disconnected');
    _advanceEndpoint();
    _scheduleReconnect();
  });

  socket.addEventListener('error', () => {
    socket.close();
  });
}

function _scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_DELAY);
    connect();
  }, reconnectDelay);
}

export function disconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (socket) {
    socket.close();
    socket = null;
  }
}

export function subscribe(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function subscribeStatus(callback) {
  statusListeners.add(callback);
  return () => statusListeners.delete(callback);
}

export function isConnected() {
  return socket && socket.readyState === WebSocket.OPEN;
}
