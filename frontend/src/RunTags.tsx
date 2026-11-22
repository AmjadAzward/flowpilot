import { useState } from 'react';
import { Tag, X } from 'lucide-react';
import { request } from './api';

type Notify = (msg: string, kind?: 'ok' | 'error') => void;

/** Short labels on a run, such as "billing" or "needs-review". Saved with the run; the history list can filter by them. */
export function RunTags({ runId, tags, canEdit, notify, onSaved }: { runId: string; tags: string[] | undefined; canEdit: boolean; notify: Notify; onSaved: () => Promise<void> | void }) {
  const current = tags ?? [];
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async (next: string[]) => {
    setBusy(true);
    try { await request(`/api/executions/${runId}/tags`, { method: 'PUT', body: JSON.stringify({ tags: next }) }); await onSaved(); setText(''); }
    catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const add = () => { if (text.trim() && !busy) void save([...current, text]); };
  return (
    <div className="run-tags" data-testid="run-tags">
      <span className="muted"><Tag size={14} /> Tags</span>
      {current.map(t => (
        <span key={t} className="run-tag-chip" data-testid="run-tag">{t}{canEdit && <button className="icon-button" aria-label={`Remove tag ${t}`} disabled={busy} onClick={() => void save(current.filter(x => x !== t))}><X size={11} /></button>}</span>
      ))}
      {!current.length && !canEdit && <span className="muted">No tags</span>}
      {canEdit && <input aria-label="Add a tag to this run" data-testid="tag-input" maxLength={24} value={text} placeholder="Add a tag" onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') add(); }} />}
      {canEdit && <button className="ghost" data-testid="tag-add" disabled={!text.trim() || busy} onClick={add}>Add</button>}
    </div>
  );
}
