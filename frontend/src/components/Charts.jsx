import React, { useRef, useEffect } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Legend,
  Tooltip,
  Title
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Legend, Tooltip, Title);

const COLORS = {
  primary: '#00D4FF',
  primaryLight: 'rgba(0, 212, 255, .15)',
  accent: '#86BC25',
  orange: '#F7C325',
  orangeLight: 'rgba(247, 195, 37, .15)',
  grid: 'rgba(255,255,255,.06)',
  text: '#8B949E',
};

const baseOptions = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 16, font: { family: 'Inter', size: 12 }, color: '#C9D1D9' } },
    tooltip: { backgroundColor: '#131B26', titleFont: { family: 'Inter' }, bodyFont: { family: 'Inter' }, padding: 10, cornerRadius: 8, borderColor: 'rgba(48,54,61,0.8)', borderWidth: 1 },
  },
  scales: {
    x: { grid: { color: COLORS.grid }, ticks: { font: { family: 'Inter', size: 11 }, color: COLORS.text, maxTicksLimit: 12 } },
    y: { grid: { color: COLORS.grid }, ticks: { font: { family: 'Inter', size: 11 }, color: COLORS.text }, beginAtZero: true },
  },
};

function HistoryChart({ entries }) {
  const sorted = [...entries].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  const labels = sorted.map((e) =>
    new Date(e.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  );

  const data = {
    labels,
    datasets: [
      {
        label: 'Occupied',
        data: sorted.map((e) => e.occupied),
        borderColor: COLORS.primary,
        backgroundColor: COLORS.primaryLight,
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        borderWidth: 2,
      },
      {
        label: 'Total Spots',
        data: sorted.map((e) => e.total),
        borderColor: COLORS.accent,
        borderDash: [6, 4],
        pointRadius: 0,
        borderWidth: 1.5,
        fill: false,
      },
    ],
  };

  const options = {
    ...baseOptions,
    scales: {
      ...baseOptions.scales,
      y: { ...baseOptions.scales.y, max: 140, title: { display: true, text: 'Vehicles', font: { family: 'Inter', size: 12 } } },
    },
  };

  return <Line data={data} options={options} />;
}

function ForecastChart({ forecasts }) {
  const sorted = [...forecasts].sort((a, b) => a.target_hour - b.target_hour);
  const nowHour = new Date().getHours();

  const labels = sorted.map((f) => {
    const h = f.target_hour;
    return h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h - 12} PM`;
  });

  const data = {
    labels,
    datasets: [
      {
        label: 'Predicted Occupancy %',
        data: sorted.map((f) => f.predicted_occupancy),
        backgroundColor: sorted.map((f) => (f.target_hour === nowHour ? COLORS.primary : COLORS.orangeLight)),
        borderColor: sorted.map((f) => (f.target_hour === nowHour ? COLORS.primary : COLORS.orange)),
        borderWidth: 1.5,
        borderRadius: 6,
      },
    ],
  };

  const options = {
    ...baseOptions,
    scales: {
      ...baseOptions.scales,
      y: { ...baseOptions.scales.y, max: 100, title: { display: true, text: 'Occupancy %', font: { family: 'Inter', size: 12 } } },
    },
  };

  return <Bar data={data} options={options} />;
}

export default function Charts({ historyEntries, forecasts }) {
  const [tab, setTab] = React.useState('history');

  return (
    <section className="charts-section">
      <div className="charts-header">
        <h2><i className="fas fa-chart-area"></i> Analytics</h2>
        <div className="chart-tabs">
          <button className={`chart-tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>History</button>
          <button className={`chart-tab ${tab === 'forecast' ? 'active' : ''}`} onClick={() => setTab('forecast')}>Forecast</button>
        </div>
      </div>
      <div className="charts-body">
        <div className={`chart-wrapper ${tab === 'history' ? 'active' : ''}`}>
          {historyEntries.length > 0 && <HistoryChart entries={historyEntries} />}
        </div>
        <div className={`chart-wrapper ${tab === 'forecast' ? 'active' : ''}`}>
          {forecasts.length > 0 && <ForecastChart forecasts={forecasts} />}
        </div>
      </div>
    </section>
  );
}
