import { useCallback, useEffect, useState } from 'react';
import { ClipboardList, RefreshCcw } from 'lucide-react';
import { request } from './api';

type Entry = { id: number; at: string; username: string | null; role: string | null; method: string; path: string; status: number };

/** Owner-only list of who changed what (every successful write request). */
export function AuditPanel({ notify }: { notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const [rows, setRows] = useState<Entry[] | null>(null);
  const load = useCallback(() => {
    request<Entry[]>('/api/audit?limit=100').then(setRows).catch(e => { setRows([]); notify((e as Error).message, 'error'); });
  }, [notify]);
  useEffect(() => { load(); }, [load]);
  return (
    <div className="panel settings-card audit-card" data-testid="audit-panel">
      <ClipboardList size={22} />
      <h2>Activity log</h2>
      <p>Who changed what. The latest 100 of the last 5000 changes are shown.</p>
      <button className="ghost" onClick={load}><RefreshCcw size={14} />Refresh</button>
      <div className="audit-list">
        {rows === null && <span className="muted">Loading…</span>}
        {rows?.length === 0 && <span className="muted">Nothing recorded yet.</span>}
        {rows?.map(r => (
          <div className="audit-row" key={r.id} data-testid="audit-row">
            <span className="muted">{new Date(r.at).toLocaleString()}</span>
            <strong>{r.username ?? 'unknown'}</strong>
            <code>{r.method} {r.path}</code>
          </div>
        ))}
      </div>
    </div>
  );
}
