import { useState } from 'react';
import { GitCompare } from 'lucide-react';
import { request } from './api';
import type { Run } from './catalog';

type Notify = (msg: string, kind?: 'ok' | 'error') => void;
type Side = { status: string | null; ms: number | null; output?: string } | null;
type Row = { node_id: string; label: string; type: string; a: Side; b: Side; change: null | 'status' | 'error' | 'output' | 'only_in_a' | 'only_in_b' };
type Result = { steps: Row[]; changed: number; first_change: string | null; output_changed: boolean; status_changed: boolean };

const words: Record<string, string> = { status: 'different result', error: 'different error', output: 'different output', only_in_a: 'only in this run', only_in_b: 'only in the other run' };

/** Pick another run and see, step by step, where the two behaved differently. */
export function RunCompare({ run, others, label, notify }: { run: Run; others: Run[]; label: (r: Run) => string; notify: Notify }) {
  const [other, setOther] = useState('');
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);

  const compare = async (id: string) => {
    setOther(id); setResult(null);
    if (!id) return;
    setBusy(true);
    try { setResult(await request<Result>(`/api/executions/compare?a=${encodeURIComponent(run.id)}&b=${encodeURIComponent(id)}`)); }
    catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };

  if (!others.length) return null;
  return (
    <div className="run-compare" data-testid="run-compare">
      <label><GitCompare size={14} /> Compare with{' '}
        <select aria-label="Compare with another run" value={other} disabled={busy} onChange={e => void compare(e.target.value)}>
          <option value="">Choose a run…</option>
          {others.map(r => <option key={r.id} value={r.id}>{label(r)}</option>)}
        </select>
      </label>
      {result && (
        <div className="compare-result" data-testid="compare-result">
          <p className="hint" data-testid="compare-summary">
            {result.changed === 0 ? 'These two runs behaved the same at every step.' : `${result.changed} step${result.changed > 1 ? 's' : ''} differ. The first difference is at “${result.steps.find(s => s.change)?.label}”.`}
          </p>
          {result.steps.map((s, i) => (
            <div key={i} className={'compare-row' + (s.change ? ' differs' : '')} data-testid="compare-row">
              <strong>{s.change ? '≠' : '='} {s.label}</strong>
              <span className="muted">{s.change ? words[s.change] : 'same'}</span>
              {s.change && s.a && s.b && (
                <div className="compare-sides">
                  <pre title="This run">{s.a.output}</pre>
                  <pre title="The other run">{s.b.output}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
