import { useState } from 'react';
import { StickyNote } from 'lucide-react';
import { request } from './api';

type Notify = (msg: string, kind?: 'ok' | 'error') => void;

/** A short note on a run, for example "customer confirmed fixed". Saved with the run and searchable. */
export function RunNote({ runId, note, canEdit, notify, onSaved }: { runId: string; note: string | null | undefined; canEdit: boolean; notify: Notify; onSaved: () => Promise<void> | void }) {
  const [text, setText] = useState(note ?? '');
  const [busy, setBusy] = useState(false);
  const changed = text.trim() !== (note ?? '');
  const save = async () => {
    setBusy(true);
    try { await request(`/api/executions/${runId}/note`, { method: 'PUT', body: JSON.stringify({ note: text }) }); await onSaved(); notify(text.trim() ? 'Note saved' : 'Note removed'); }
    catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  return (
    <div className="run-note" data-testid="run-note">
      <label><StickyNote size={14} /> Note
        <input aria-label="Note on this run" data-testid="note-input" maxLength={500} value={text} disabled={!canEdit} placeholder={canEdit ? 'Add a note, for example “customer confirmed fixed”' : 'No note'} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && changed && !busy) void save(); }} />
      </label>
      {canEdit && <button className="ghost" data-testid="note-save" disabled={!changed || busy} onClick={() => void save()}>Save note</button>}
    </div>
  );
}
