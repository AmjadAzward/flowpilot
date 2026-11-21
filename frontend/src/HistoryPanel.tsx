import { useEffect, useState } from 'react';
import { GitCompare, History, RotateCcw, X } from 'lucide-react';
import { request } from './api';

type Version = { id: number; saved_at: string; saved_by: string | null; name: string; nodes: number };

type Diff = {
  name: { before: string; after: string } | null;
  nodes_added: { id: string; type: string }[]; nodes_removed: { id: string; type: string }[];
  nodes_changed: { id: string; type: string; fields: { field: string; before: string; after: string }[]; type_before?: string }[];
  edges_added: { from: string; to: string }[]; edges_removed: { from: string; to: string }[]; settings_changed: boolean;
};

function DiffView({ d }: { d: Diff }) {
  const none = !d.name && !d.nodes_added.length && !d.nodes_removed.length && !d.nodes_changed.length && !d.edges_added.length && !d.edges_removed.length && !d.settings_changed;
  if (none) return <p className="hint pad" data-testid="diff-none">No differences from the current workflow.</p>;
  return (
    <div className="diff-box" data-testid="diff-view">
      <p className="hint">Changes from this version to the current workflow:</p>
      {d.name && <div>Name: “{d.name.before}” → “{d.name.after}”</div>}
      {d.nodes_added.map(n => <div key={'a' + n.id} className="diff-add">+ Step {n.id} ({n.type}) added</div>)}
      {d.nodes_removed.map(n => <div key={'r' + n.id} className="diff-del">− Step {n.id} ({n.type}) removed</div>)}
      {d.nodes_changed.map(n => (
        <div key={'c' + n.id}>~ Step {n.id} ({n.type}) changed
          {n.type_before && <div className="diff-sub">type: {n.type_before} → {n.type}</div>}
          {n.fields.map(f => <div key={f.field} className="diff-sub">{f.field}: <span className="diff-del">{f.before || '(empty)'}</span> → <span className="diff-add">{f.after || '(empty)'}</span></div>)}
        </div>
      ))}
      {d.edges_added.map((e, i) => <div key={'ea' + i} className="diff-add">+ Connection {e.from} → {e.to}</div>)}
      {d.edges_removed.map((e, i) => <div key={'er' + i} className="diff-del">− Connection {e.from} → {e.to}</div>)}
      {d.settings_changed && <div>~ Workflow settings changed</div>}
    </div>
  );
}

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
  const [open, setOpen] = useState<number | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const compare = async (v: Version) => {
    if (open === v.id) { setOpen(null); return; }
    setOpen(v.id); setDiff(null);
    try { setDiff(await request<Diff>(`/api/workflows/${workflowId}/versions/${v.id}/diff`)); }
    catch (e) { setOpen(null); notify((e as Error).message, 'error'); }
  };

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
        <div key={v.id}>
          <div className="version-row" data-testid="history-row">
            <div>
              <strong>{new Date(v.saved_at).toLocaleString()}</strong>
              <span className="muted">{v.name} · {v.nodes} nodes{v.saved_by ? ` · replaced by ${v.saved_by}` : ''}</span>
            </div>
            <span style={{ display: 'flex', gap: 6 }}>
              <button className="ghost" data-testid="history-compare" onClick={() => void compare(v)}><GitCompare size={14} />Compare</button>
              <button className="ghost" disabled={!canRestore} onClick={() => void restore(v)}><RotateCcw size={14} />Restore</button>
            </span>
          </div>
          {open === v.id && (diff ? <DiffView d={diff} /> : <div className="empty">Comparing…</div>)}
        </div>
      ))}
    </aside>
  );
}
