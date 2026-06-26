import { useEffect, useState } from 'react';
import * as API from '../services/api';

export function useHistory(hours = 24) {
  const [entries, setEntries] = useState([]);

  useEffect(() => {
    API.history(hours).then(setEntries).catch(() => {});
  }, [hours]);

  return entries;
}

export function useForecast() {
  const [forecasts, setForecasts] = useState([]);

  useEffect(() => {
    const load = () => {
      API.forecasts()
        .then((data) => setForecasts(data || []))
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  return forecasts;
}

export function useStats() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    API.stats().then(setStats).catch(() => {});
  }, []);

  return stats;
}
