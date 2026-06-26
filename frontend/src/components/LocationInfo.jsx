import React, { useEffect, useState, useMemo } from 'react';
import { useActiveLocation } from '../contexts/ActiveLocationContext';

const DEFAULT_LOCATION = {
  name: 'Walmart Supercenter',
  address: 'Mechanicsburg, PA 17050',
  lat: 40.248386,
  lon: -77.0239493,
  timezone: 'America/New_York',
};

function buildWeatherUrl(lat, lon, tz) {
  return `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=${encodeURIComponent(tz)}`;
}

const WMO_ICONS = {
  0: { icon: 'fa-sun', label: 'Clear', color: '#F7C325' },
  1: { icon: 'fa-sun', label: 'Mostly Clear', color: '#F7C325' },
  2: { icon: 'fa-cloud-sun', label: 'Partly Cloudy', color: '#8B949E' },
  3: { icon: 'fa-cloud', label: 'Overcast', color: '#8B949E' },
  45: { icon: 'fa-smog', label: 'Foggy', color: '#8B949E' },
  48: { icon: 'fa-smog', label: 'Rime Fog', color: '#8B949E' },
  51: { icon: 'fa-cloud-rain', label: 'Light Drizzle', color: '#00D4FF' },
  53: { icon: 'fa-cloud-rain', label: 'Drizzle', color: '#00D4FF' },
  55: { icon: 'fa-cloud-rain', label: 'Heavy Drizzle', color: '#00D4FF' },
  61: { icon: 'fa-cloud-showers-heavy', label: 'Light Rain', color: '#00D4FF' },
  63: { icon: 'fa-cloud-showers-heavy', label: 'Rain', color: '#00D4FF' },
  65: { icon: 'fa-cloud-showers-heavy', label: 'Heavy Rain', color: '#00D4FF' },
  71: { icon: 'fa-snowflake', label: 'Light Snow', color: '#C9D1D9' },
  73: { icon: 'fa-snowflake', label: 'Snow', color: '#C9D1D9' },
  75: { icon: 'fa-snowflake', label: 'Heavy Snow', color: '#C9D1D9' },
  80: { icon: 'fa-cloud-showers-heavy', label: 'Rain Showers', color: '#00D4FF' },
  81: { icon: 'fa-cloud-showers-heavy', label: 'Heavy Showers', color: '#00D4FF' },
  95: { icon: 'fa-bolt', label: 'Thunderstorm', color: '#A855F7' },
  96: { icon: 'fa-bolt', label: 'Thunderstorm + Hail', color: '#A855F7' },
  99: { icon: 'fa-bolt', label: 'Severe Thunderstorm', color: '#A855F7' },
};

function getWeatherInfo(code) {
  return WMO_ICONS[code] || { icon: 'fa-cloud', label: 'Unknown', color: '#8B949E' };
}

/** Shared hook: weather + local time for the active (or default) location. */
export function useLocationData() {
  const activeLoc = useActiveLocation();
  const [weather, setWeather] = useState(null);
  const [localTime, setLocalTime] = useState('');

  const location = useMemo(() => {
    if (!activeLoc) return DEFAULT_LOCATION;
    return {
      name: activeLoc.title || DEFAULT_LOCATION.name,
      address: activeLoc.subtitle || DEFAULT_LOCATION.address,
      lat: activeLoc.lat ?? DEFAULT_LOCATION.lat,
      lon: activeLoc.lon ?? DEFAULT_LOCATION.lon,
      timezone: 'America/New_York',
    };
  }, [activeLoc]);

  useEffect(() => {
    if (!location.lat || !location.lon) return;
    const fetchWeather = () => {
      fetch(buildWeatherUrl(location.lat, location.lon, location.timezone))
        .then((r) => r.json())
        .then((data) => {
          if (data.current) setWeather(data.current);
        })
        .catch(() => {});
    };
    fetchWeather();
    const id = setInterval(fetchWeather, 600000);
    return () => clearInterval(id);
  }, [location.lat, location.lon, location.timezone]);

  useEffect(() => {
    const tick = () => {
      setLocalTime(
        new Date().toLocaleString('en-US', {
          timeZone: location.timezone,
          weekday: 'short',
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      );
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [location.timezone]);

  const wInfo = weather ? getWeatherInfo(weather.weather_code) : null;

  return { weather, wInfo, localTime, location };
}

export default function LocationInfo() {
  const { weather, wInfo, localTime, location } = useLocationData();

  return (
    <div className="location-info-card">
      <h3><i className="fas fa-map-marker-alt"></i> Location</h3>

      <div className="loc-name">{location.name}</div>
      <div className="loc-address">{location.address}</div>

      <div className="loc-divider" />

      <div className="loc-row">
        <i className="fas fa-clock" style={{ color: '#00D4FF' }}></i>
        <span className="loc-time">{localTime || '—'}</span>
      </div>

      <div className="loc-divider" />

      {weather ? (
        <div className="loc-weather">
          <div className="loc-weather-main">
            <i className={`fas ${wInfo.icon}`} style={{ color: wInfo.color, fontSize: '1.5rem' }}></i>
            <span className="loc-temp">{Math.round(weather.temperature_2m)}°F</span>
            <span className="loc-condition">{wInfo.label}</span>
          </div>
          <div className="loc-weather-details">
            <span><i className="fas fa-wind"></i> {Math.round(weather.wind_speed_10m)} mph</span>
            <span><i className="fas fa-droplet"></i> {weather.relative_humidity_2m}%</span>
          </div>
        </div>
      ) : (
        <div className="loc-weather-loading">
          <i className="fas fa-spinner fa-spin"></i> Loading weather…
        </div>
      )}
    </div>
  );
}
