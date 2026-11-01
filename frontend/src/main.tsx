import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './ClearClaimApp';
import './styles/legacy.css';
import './styles/clearclaim.css';
import './styles/theme.css';
import './styles/controlroom.css';
import './styles/scene3d.css';
import './styles/one.css';
import './styles/space.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
