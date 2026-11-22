import { useEffect, useState } from 'react';
import { Timer, X } from 'lucide-react';
import { request } from './api';

export type StepTiming = { node_id: string; label: string; type: string | null; runs: number; failed: number; timed: number; median_ms: number | null; max_ms: number | null; total_ms: number };
type Timings = { runs_used: number; steps: StepTiming[] };

/** "850 ms", "2.4 s", "1 min 5 s": times people can read at a glance. */
export function readableMs(ms: number | null): string {
  if (ms == null) return 'no time recorded';
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${Math.round(ms / 100) / 10} s`;
  return `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
}

/** Which steps of a workflow are usually the slowest, taken from its latest finished runs. */
export function StepTimingsPanel({ workflowId, onClose }: { workflowId: string; onClose: () => void }) {
  const [info, setInfo] = useState<Timings | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stale = false;
    request<Timings>(`/api/workflows/${workflowId}/step-timings`).then(d => { if (!stale) setInfo(d); }).catch(e => { if (!stale) setError((e as Error).message); });
    return () => { stale = true; };
  }, [workflowId]);

  const top = info?.steps[0]?.median_ms || 0;
  return (
    <aside className="history-drawer drawer-pad" data-testid="timings-panel" aria-label="Slowest steps">
      <div className="mini-heading"><span><Timer size={14} /> Slowest steps</span><button className="icon-button" aria-label="Close slowest steps" onClick={onClose}><X size={15} /></button></div>
      {error && <p className="error">{error}</p>}
      {!info && !error && <p className="hint">Loading…</p>}
      {info && info.runs_used === 0 && <p className="hint" data-testid="timings-empty">No finished runs yet. Run the workflow and the slowest steps will show up here.</p>}
      {info && info.runs_used > 0 && (
        <div style={{ display: 'grid', gap: 8 }}>
          <p className="hint">From the last {info.runs_used} finished run{info.runs_used > 1 ? 's' : ''}. Test runs are left out.</p>
          <ol style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 8 }} data-testid="timings-list">
            {info.steps.map(s => (
              <li key={s.node_id} data-testid="timing-row">
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}><strong>{s.label}</strong><span>{readableMs(s.median_ms)}</span></div>
                <div aria-hidden style={{ height: 4, borderRadius: 2, background: 'var(--border, #ddd)', marginTop: 3 }}><div style={{ height: 4, borderRadius: 2, background: 'var(--accent, #4f46e5)', width: `${top ? Math.max(2, Math.round(((s.median_ms || 0) / top) * 100)) : 0}%` }} /></div>
                <small className="muted">worst {readableMs(s.max_ms)} · ran {s.runs}×{s.failed > 0 && <em className="iter" data-testid="timing-failed"> · failed {s.failed}×</em>}</small>
              </li>
            ))}
          </ol>
        </div>
      )}
    </aside>
  );
}
