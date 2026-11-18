import { useEffect, useState } from 'react';
import { History, RotateCcw, X } from 'lucide-react';
import { request } from './api';

type Version = { id: number; saved_at: string; saved_by: string | null; name: string; nodes: number };

type Props = {
  workflowId: string;
  canRestore: boolean;
  dirty: boolean;
  onClose: () => void;
  onRestored: () => void;
  notify: (msg: string, kind?: 'ok' | 'error') => void;
};

/** Slide-over list of earlier saved versions of a workflow, with one-click restore. */
export function HistoryPanel({ workflowId, canRestore, dirty, onClose, onRestored, notify }: Props) {
  const [versions, setVersions] = useState<Version[] | null>(null);

  useEffect(() => {
    let live = true;
    request<Version[]>(`/api/workflows/${workflowId}/versions`)
      .then(v => { if (live) setVersions(v); })
      .catch(e => { if (live) { setVersions([]); notify((e as Error).message, 'error'); } });
    return () => { live = false; };
  }, [workflowId, notify]);

  const restore = async (v: Version) => {
    const warn = dirty ? ' You have unsaved changes that will be lost.' : '';
    if (!window.confirm(`Restore the version saved ${new Date(v.saved_at).toLocaleString()}? The current version is kept in this list.${warn}`)) return;
    try {
      await request(`/api/workflows/${workflowId}/versions/${v.id}/restore`, { method: 'POST' });
      notify('Version restored');
      onRestored();
    } catch (e) { notify((e as Error).message, 'error'); }
  };

  return (
    <aside className="history-drawer" data-testid="history-panel" aria-label="Version history">
      <div className="mini-heading"><span><History size={14} /> Version history</span><button className="icon-button" aria-label="Close history" onClick={onClose}><X size={15} /></button></div>
      <p className="hint pad">Every save that changes the workflow keeps the previous version. The last 30 are kept.</p>
      {versions === null && <div className="empty">Loading…</div>}
      {versions?.length === 0 && <div className="empty">No earlier versions yet. Save a change to start the history.</div>}
      {versions?.map(v => (
        <div className="version-row" key={v.id} data-testid="history-row">
          <div>
            <strong>{new Date(v.saved_at).toLocaleString()}</strong>
            <span className="muted">{v.name} · {v.nodes} nodes{v.saved_by ? ` · replaced by ${v.saved_by}` : ''}</span>
          </div>
          <button className="ghost" disabled={!canRestore} onClick={() => void restore(v)}><RotateCcw size={14} />Restore</button>
        </div>
      ))}
    </aside>
  );
}
