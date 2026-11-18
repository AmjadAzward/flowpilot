import { useCallback, useEffect, useState } from 'react';
import { Braces, Trash2 } from 'lucide-react';
import { request } from './api';

type Variable = { name: string; value: string; updated_at: string };

/** Shared, non-secret values usable in any node as {{vars.NAME}}. Admins and owners can change them. */
export function VariablesPanel({ canEdit, notify }: { canEdit: boolean; notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const [rows, setRows] = useState<Variable[] | null>(null);
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const load = useCallback(() => {
    request<Variable[]>('/api/variables').then(setRows).catch(e => { setRows([]); notify((e as Error).message, 'error'); });
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const save = async (n: string, v: string) => {
    try { await request(`/api/variables/${encodeURIComponent(n)}`, { method: 'PUT', body: JSON.stringify({ value: v }) }); notify('Variable saved'); load(); return true; }
    catch (e) { notify((e as Error).message, 'error'); return false; }
  };
  const remove = async (n: string) => {
    if (!window.confirm(`Delete the variable ${n}? Workflows that use it will fail until it is added again.`)) return;
    try { await request(`/api/variables/${encodeURIComponent(n)}`, { method: 'DELETE' }); notify('Variable deleted'); load(); }
    catch (e) { notify((e as Error).message, 'error'); }
  };

  return (
    <div className="panel settings-card var-card" data-testid="variables-panel">
      <Braces size={22} />
      <h2>Variables</h2>
      <p>Shared values for every workflow, such as a website address or a tax rate. Use them anywhere as <code>{'{{vars.NAME}}'}</code>. They are visible to everyone who signs in, so keep secrets on the Credentials page.</p>
      {canEdit && (
        <form className="var-form" onSubmit={async e => { e.preventDefault(); if (await save(name.trim(), value)) { setName(''); setValue(''); } }}>
          <input aria-label="Variable name" placeholder="NAME" value={name} onChange={e => setName(e.target.value)} maxLength={40} />
          <input aria-label="Variable value" placeholder="value" value={value} onChange={e => setValue(e.target.value)} maxLength={2000} />
          <button className="primary" type="submit" disabled={!name.trim()}>Add</button>
        </form>
      )}
      <div className="var-list">
        {rows === null && <span className="muted">Loading…</span>}
        {rows?.length === 0 && <span className="muted">No variables yet.</span>}
        {rows?.map(r => (
          <div className="var-row" key={r.name} data-testid="variable-row">
            <code>{r.name}</code>
            <input aria-label={`Value of ${r.name}`} defaultValue={r.value} disabled={!canEdit} maxLength={2000} onBlur={e => { if (e.target.value !== r.value) void save(r.name, e.target.value); }} />
            {canEdit && <button className="icon-button danger" aria-label={`Delete ${r.name}`} onClick={() => void remove(r.name)}><Trash2 size={15} /></button>}
          </div>
        ))}
      </div>
    </div>
  );
}
