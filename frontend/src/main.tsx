import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

function App() {
  const [status, setStatus] = useState('Checking API connection…');

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/health', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('API unavailable');
        const result = await response.json();
        if (result.status !== 'ok' || result.service !== 'codehound') {
          throw new Error('Unexpected API response');
        }
        setStatus('API connected');
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus('API unavailable — start the backend to connect.');
      });
    return () => controller.abort();
  }, []);

  return (
    <main>
      <p className="eyebrow">CODEHOUND / DEVELOPMENT</p>
      <h1>Verify what coding agents actually ship.</h1>
      <p>Independent verification for AI-generated code, grounded in execution evidence.</p>
      <section aria-labelledby="workspace-heading">
        <h2 id="workspace-heading">Workspace initialized</h2>
        <p role="status">{status}</p>
        <p>Repository intake, patch evaluation, and verification reports are planned next.</p>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>,
);
