import React from 'react';
import { Routes, Route } from 'react-router-dom';
import LandingPage from './pages/LandingPage';
import PersonaSelection from './pages/PersonaSelection';
import UserLocations from './pages/UserLocations';
import DeveloperSetup from './pages/DeveloperSetup';
import Dashboard from './pages/Dashboard';
import DataFlow from './pages/DataFlow';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/get-started" element={<PersonaSelection />} />
      <Route path="/locations" element={<UserLocations />} />
      <Route path="/developer-setup" element={<DeveloperSetup />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/data-flow" element={<DataFlow />} />
    </Routes>
  );
}
