import { useState } from 'react';
import { Lightbulb, Pencil } from 'lucide-react';
import { request } from './api';
import type { Run } from './catalog';

type Notify = (msg: string, kind?: 'ok' | 'error') => void;

/** Extra help on a failed run: ask an AI to explain it, or replay it from the failed step with edited input. */
export function FailedRunTools({ run, credentials, canEdit, notify, onReplayed }: { run: Run; credentials: string[]; canEdit: boolean; notify: Notify; onReplayed: () => Promise<void> | void }) {
  const failed = [...run.steps].reverse().find(s => s.status === 'error' && !(s as { continued?: boolean }).continued);
  const [cred, setCred] = useState('');
  const [busy, setBusy] = useState(false);
  const [explanation, setExplanation] = useState('');
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const chosen = cred || credentials[0] || '';

  const explain = async () => {
    setBusy(true);
    try {
      const res = await request<{ explanation: string }>(`/api/executions/${run.id}/explain`, { method: 'POST', body: JSON.stringify({ credential: chosen }) });
      setExplanation(res.explanation);
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const replay = async () => {
    let value: unknown;
    try { value = JSON.parse(text); } catch { notify('The input is not valid JSON', 'error'); return; }
    setBusy(true);
    try {
      const res = await request<{ status: string; error: string | null }>(`/api/executions/${run.id}/replay`, { method: 'POST', body: JSON.stringify({ input: value }) });
      await onReplayed();
      setEditing(false);
      if (res.status === 'success') notify('Replay with edited input finished successfully'); else notify(`Replay ${res.status}${res.error ? ': ' + res.error : ''}`, 'error');
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };

  return (
    <div className="failed-tools" data-testid="failed-tools">
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <button className="ghost" data-testid="edit-replay" disabled={!canEdit || !failed || busy} onClick={() => { setText(JSON.stringify(failed?.input ?? null, null, 2)); setEditing(e => !e); }}><Pencil size={14} />Edit input and replay</button>
        <select aria-label="AI credential to explain with" value={chosen} onChange={e => setCred(e.target.value)} disabled={!canEdit || busy}>
          {credentials.length === 0 && <option value="">No AI credential</option>}
          {credentials.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="ghost" data-testid="explain-run" disabled={!canEdit || busy || !chosen} onClick={() => void explain()}><Lightbulb size={14} />{busy && !editing ? 'Thinking…' : 'Explain this failure'}</button>
      </div>
      {editing && (
        <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
          <label>Data the failed step receives (JSON)
            <textarea aria-label="Edited input" data-testid="edit-input" rows={6} value={text} onChange={e => setText(e.target.value)} spellCheck={false} />
          </label>
          <div><button className="primary" data-testid="replay-edited" disabled={busy} onClick={() => void replay()}>Replay with this input</button></div>
        </div>
      )}
      {explanation && <p className="explain-box" data-testid="explanation"><strong>Possible cause (AI, may be wrong):</strong> {explanation}</p>}
    </div>
  );
}
