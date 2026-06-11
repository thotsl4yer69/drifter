// DRIFTER cockpit v4 — production entry (Vite + React 18).
// Fonts bundled locally via @fontsource (offline-first, brief §2.2 — NO CDN).
import '@fontsource-variable/bricolage-grotesque/wght.css';
import '@fontsource/jetbrains-mono/300.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import '@fontsource/jetbrains-mono/700.css';
import '@fontsource/major-mono-display/400.css';

import './styles/drifter-dna.css';

import React from 'react';
import { createRoot } from 'react-dom/client';
import { CockpitApp } from './app/shell.jsx';

createRoot(document.getElementById('root')).render(<CockpitApp />);
