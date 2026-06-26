import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

export default function ParkingMap() {
  const mapRef = useRef(null);
  const instanceRef = useRef(null);

  useEffect(() => {
    if (instanceRef.current) return;

    const map = L.map(mapRef.current).setView([40.248386, -77.0239493], 17);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
    }).addTo(map);
    L.marker([40.248386, -77.0239493])
      .addTo(map)
      .bindPopup('<b>Walmart Supercenter</b><br>Mechanicsburg, PA')
      .openPopup();
    instanceRef.current = map;

    return () => {
      instanceRef.current.remove();
      instanceRef.current = null;
    };
  }, []);

  return (
    <section className="map-section">
      <div className="map-header">
        <h2><i className="fas fa-map-marked-alt"></i> Parking Location</h2>
      </div>
      <div ref={mapRef} id="map" />
    </section>
  );
}
