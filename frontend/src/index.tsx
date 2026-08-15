import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';
import { UserProvider } from './UserContext';
import { ToastProvider } from './contexts/ToastContext';
import * as serviceWorkerRegistration from "./serviceWorkerRegistration"; // Import service worker

// Automated sign-in for local testing. A dynamic import behind a literal-false
// condition, so the module is not in the production bundle at all rather than
// being shipped and merely unreachable. See devSignIn.ts.
//
// Removed by 269acb7, "Keep the sign-in harness on develop, not master", and
// then carried onto develop by the next merge from master, which is the one
// direction that arrangement cannot survive. devSignIn.ts stayed on disk the
// whole time with nothing importing it, so the harness looked present and did
// nothing. Restored 2026-08-15; see the overview on why the file itself is
// safe.
if (import.meta.env.DEV) {
    import('./devSignIn');
}

const root = ReactDOM.createRoot(document.getElementById('root')!);
root.render(
  <ToastProvider>
    <UserProvider>
      <App />
    </UserProvider>
  </ToastProvider>
);


// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
