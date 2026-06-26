import { useEffect, useRef, useState } from 'react';
import * as WS from '../services/websocket';
import * as API from '../services/api';

/**
 * Provides live occupancy data via WebSocket with REST polling fallback.
 */
export function useOccupancy() {
  const [data, setData] = useState(null);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const pollRef = useRef(null);
  const lastFallbackSuccessRef = useRef(0);

  useEffect(() => {
    const fetchFallback = async () => {
      try {
        const latest = await API.occupancy();
        setData(latest);
        lastFallbackSuccessRef.current = Date.now();
        // If HTTP polling works but WS is blocked by proxy, keep UI in connected state.
        if (!WS.isConnected()) {
          setWsStatus('connected');
        }
      } catch {
        const freshFallback = Date.now() - lastFallbackSuccessRef.current < 20_000;
        if (!WS.isConnected() && !freshFallback) {
          setWsStatus('disconnected');
        }
      }
    };

    const handleStatus = (status) => {
      if (status === 'disconnected') {
        const freshFallback = Date.now() - lastFallbackSuccessRef.current < 20_000;
        if (freshFallback) {
          setWsStatus('connected');
          return;
        }
      }
      setWsStatus(status);
    };

    // Subscribe to WS data + status
    const unsubData = WS.subscribe(setData);
    const unsubStatus = WS.subscribeStatus(handleStatus);
    WS.connect();

    // Initial REST fetch
    fetchFallback();

    // Fallback polling every 10s when WS is down
    pollRef.current = setInterval(() => {
      if (!WS.isConnected()) {
        fetchFallback();
      }
    }, 10000);

    return () => {
      unsubData();
      unsubStatus();
      clearInterval(pollRef.current);
    };
  }, []);

  return { data, wsStatus };
}
