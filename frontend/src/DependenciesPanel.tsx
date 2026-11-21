import { useEffect, useState } from 'react';
import { Network, X } from 'lucide-react';
import { request } from './api';

type Ref = { id: string; name: string; via: string };
export type Dependencies = { id: string; name: string; calls: Ref[]; called_by: Ref[]; missing_workflows: string[]; hidden_references: number; credentials: string[]; missing_credentials: string[]; is_default_error_handler: boolean };

const via: Record<string, string> = { subflow: 'Run Workflow node', agent: 'AI Agent tool', error_handler: 'error handler' };

/** Words for a warning before deleting workflows others still use. Empty when nothing depends on them. */
export function deleteWarning(map: Dependencies[], ids: string[]): string {
  const going = new Set(ids);
  const lines: string[] = [];
  for (const w of map) {
    if (!going.has(w.id)) continue;
    const users = w.called_by.filter(c => !going.has(c.id));
    if (users.length) lines.push(`"${w.name}" is used by ${users.map(u => `"${u.name}" (${via[u.via] ?? u.via})`).join(', ')}`);
    if (w.is_default_error_handler) lines.push(`"${w.name}" is the shared error workflow in Settings`);
  }
  return lines.length ? `Careful: ${lines.join('; ')}. Those will stop working.\n\n` : '';
}

/** What this workflow runs, what runs it, and which credentials it needs. */
export function DependenciesPanel({ workflowId, onClose, onOpen }: { workflowId: string; onClose: () => void; onOpen: (id: string) => void }) {
  const [info, setInfo] = useState<Dependencies | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stale = false;
    request<Dependencies>(`/api/workflows/${workflowId}/dependencies`).then(d => { if (!stale) setInfo(d); }).catch(e => { if (!stale) setError((e as Error).message); });
    return () => { stale = true; };
  }, [workflowId]);

  const list = (refs: Ref[], empty: string, testid: string) => refs.length
    ? <ul data-testid={testid}>{refs.map((r, i) => <li key={i}><button className="link-button" style={{ marginLeft: 0, padding: 0 }} onClick={() => onOpen(r.id)}>{r.name}</button> <small className="muted">{via[r.via] ?? r.via}</small></li>)}</ul>
    : <p className="hint">{empty}</p>;

  return (
    <aside className="history-drawer" data-testid="deps-panel" aria-label="Workflow dependencies">
      <div className="mini-heading"><span><Network size={14} /> Connections</span><button className="icon-button" aria-label="Close connections" onClick={onClose}><X size={15} /></button></div>
      {error && <p className="error">{error}</p>}
      {!info && !error && <p className="hint">Loading…</p>}
      {info && (
        <div style={{ display: 'grid', gap: 12 }}>
          <section><strong>Runs these workflows</strong>{list(info.calls, 'It does not run any other workflow.', 'deps-calls')}</section>
          <section><strong>Run by these workflows</strong>{list(info.called_by, 'No other workflow runs it.', 'deps-called-by')}
            {info.is_default_error_handler && <p className="hint" data-testid="deps-default-handler">It is the shared error workflow chosen in Settings.</p>}</section>
          <section><strong>Credentials it uses</strong>
            {info.credentials.length ? <ul data-testid="deps-creds">{info.credentials.map(c => <li key={c}><code>{c}</code>{info.missing_credentials.includes(c) && <em className="iter" data-testid="deps-missing-cred"> not saved yet</em>}</li>)}</ul> : <p className="hint">It does not use any credential.</p>}</section>
          {info.missing_workflows.length > 0 && <p className="error" data-testid="deps-missing-wf">It points at {info.missing_workflows.length} workflow{info.missing_workflows.length > 1 ? 's' : ''} that no longer exist{info.missing_workflows.length > 1 ? '' : 's'}.</p>}
          {info.hidden_references > 0 && <p className="hint">It also uses {info.hidden_references} workflow{info.hidden_references > 1 ? 's' : ''} you do not have access to.</p>}
        </div>
      )}
    </aside>
  );
}
