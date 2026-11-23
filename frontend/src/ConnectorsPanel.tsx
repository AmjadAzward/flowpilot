import { useCallback, useEffect, useState } from 'react';
import { Plug, Trash2 } from 'lucide-react';
import { request } from './api';

export type Connector = { name: string; label: string; base_url: string; auth: { type: string; name?: string }; operations: Record<string, { label: string; method: string; example: Record<string, unknown>; required: string[] }> };

const EXAMPLE = JSON.stringify({
  label: 'Example API',
  base_url: 'https://api.example.com/v1',
  auth: { type: 'bearer' },
  operations: {
    get_item: { label: 'Get an item', method: 'GET', path: '/items/{id}', query: { expand: '{expand}' }, required: ['id'], example: { id: '42' } },
    create_item: { label: 'Create an item', method: 'POST', path: '/items', body: { name: '{name}', price: '{price}' }, required: ['name'], example: { name: 'Pen', price: 2.5 } },
  },
}, null, 2);

/** The connector list for node settings; refetches when mounted. */
export function useConnectors() {
  const [rows, setRows] = useState<Connector[] | null>(null);
  useEffect(() => { request<Connector[]>('/api/connectors').then(setRows).catch(() => setRows([])); }, []);
  return rows;
}

type CheckResult = { ok: boolean; seconds: number; error: string | null; sample: string | null };

/** One real, read-only call to the live service with a saved key, to see whether the connector works. */
function ConnectorCheck({ c, notify }: { c: Connector; notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const reads = Object.entries(c.operations).filter(([, o]) => o.method === 'GET');
  const [op, setOp] = useState(reads[0]?.[0] ?? '');
  const [fields, setFields] = useState(JSON.stringify(reads[0]?.[1].example ?? {}, null, 2));
  const [cred, setCred] = useState('');
  const [creds, setCreds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<CheckResult | null>(null);
  useEffect(() => { if (c.auth.type !== 'none') request<{ name: string }[]>('/api/credentials').then(r => setCreds(r.map(x => x.name))).catch(() => setCreds([])); }, [c.auth.type]);
  if (!reads.length) return <div className="hint" data-testid="check-none">This connector has no read-only actions to check.</div>;
  const run = async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(fields || '{}'); } catch { notify('Fields must be valid JSON', 'error'); return; }
    setBusy(true); setRes(null);
    try { setRes(await request<CheckResult>(`/api/connectors/${encodeURIComponent(c.name)}/check`, { method: 'POST', body: JSON.stringify({ operation: op, fields: parsed, credential: cred }) })); }
    catch (e) { notify((e as Error).message, 'error'); }
    finally { setBusy(false); }
  };
  return (
    <div className="connector-check" data-testid="connector-check">
      <span className="hint">Makes one real read-only call to {c.label} with the key you pick. It never changes anything, and the key is not shown.</span>
      <label>Action<select aria-label="Check action" value={op} onChange={e => { setOp(e.target.value); setFields(JSON.stringify(c.operations[e.target.value].example, null, 2)); setRes(null); }}>{reads.map(([k, o]) => <option key={k} value={k}>{o.label}</option>)}</select></label>
      {c.auth.type !== 'none' && <label>Credential<select aria-label="Check credential" value={cred} onChange={e => setCred(e.target.value)}><option value="">Choose a credential</option>{creds.map(n => <option key={n} value={n}>{n}</option>)}</select></label>}
      <textarea aria-label="Check fields" rows={4} spellCheck={false} value={fields} onChange={e => setFields(e.target.value)} />
      <button className="primary" type="button" disabled={busy || !op || (c.auth.type !== 'none' && !cred)} onClick={() => void run()}>{busy ? 'Checking…' : 'Run check'}</button>
      {res && <div className={'check-result ' + (res.ok ? 'ok' : 'bad')} data-testid="check-result"><strong>{res.ok ? `Worked in ${res.seconds}s` : `Failed after ${res.seconds}s`}</strong>{res.ok ? <code>{res.sample}</code> : <span>{res.error}</span>}</div>}
    </div>
  );
}

/** Owners and admins describe a REST API in a small JSON manifest; it then appears as the "Custom connector" service in App Action. */
export function ConnectorsPanel({ canEdit, notify }: { canEdit: boolean; notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const [rows, setRows] = useState<Connector[] | null>(null);
  const [name, setName] = useState('');
  const [text, setText] = useState('');
  const [lib, setLib] = useState<{ id: string; label: string; about: string; manifest: unknown }[]>([]);
  const [about, setAbout] = useState('');
  const [checking, setChecking] = useState('');
  useEffect(() => { request<typeof lib>('/api/connector-library').then(setLib).catch(() => setLib([])); }, []);
  const load = useCallback(() => {
    request<Connector[]>('/api/connectors').then(setRows).catch(e => { setRows([]); notify((e as Error).message, 'error'); });
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    let manifest: unknown;
    try { manifest = JSON.parse(text); } catch { notify('The manifest is not valid JSON', 'error'); return; }
    try { await request(`/api/connectors/${encodeURIComponent(name.trim())}`, { method: 'PUT', body: JSON.stringify({ manifest }) }); notify('Connector saved'); setName(''); setText(''); load(); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const edit = async (n: string) => {
    try { const m = await request<unknown>(`/api/connectors/${encodeURIComponent(n)}`); setName(n); setText(JSON.stringify(m, null, 2)); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const download = async (n: string) => {
    try {
      const m = await request<unknown>(`/api/connectors/${encodeURIComponent(n)}`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(m, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a'); a.href = url; a.download = `${n}.connector.json`; a.click(); URL.revokeObjectURL(url);
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const importFile = async (file: File) => {
    try {
      const body = await file.text();
      JSON.parse(body);
      setText(body);
      if (!name.trim()) setName(file.name.replace(/\.connector\.json$|\.json$/i, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^[^a-z]+/, '').slice(0, 40));
      setAbout('');
      notify('File loaded. Check the name and manifest, then save.');
    } catch { notify('That file is not valid JSON', 'error'); }
  };
  const remove = async (n: string) => {
    if (!window.confirm(`Delete the connector ${n}? Workflows that use it will fail until it is added again.`)) return;
    try { await request(`/api/connectors/${encodeURIComponent(n)}`, { method: 'DELETE' }); notify('Connector deleted'); load(); }
    catch (e) { notify((e as Error).message, 'error'); }
  };

  return (
    <div className="panel settings-card var-card" data-testid="connectors-panel">
      <Plug size={22} />
      <h2>Custom connectors</h2>
      <p>Describe any REST API once in a small JSON manifest (address, how it signs in, and its actions), then use it from an App Action node by choosing "Custom connector". Secrets stay on the Credentials page.</p>
      {canEdit && (
        <div className="connector-form">
          <label>Start from a ready-made connector
            <select aria-label="Connector library" value="" onChange={e => { const hit = lib.find(x => x.id === e.target.value); if (hit) { setName(hit.id); setText(JSON.stringify(hit.manifest, null, 2)); setAbout(hit.about); } }}>
              <option value="">Choose one ({lib.length} available)</option>
              {lib.map(x => <option key={x.id} value={x.id}>{x.label}</option>)}
            </select>
          </label>
          {about && <span className="hint">{about} This manifest was written from the service's public documentation and has not been run against the live service, so try it with a test account and adjust it here if the API differs.</span>}
          <input aria-label="Connector name" placeholder="name, for example acme" value={name} onChange={e => setName(e.target.value)} maxLength={40} />
          <textarea aria-label="Connector manifest" rows={10} spellCheck={false} placeholder="Manifest (JSON)" value={text} onChange={e => setText(e.target.value)} />
          <span className="chip-row">
            <button type="button" className="chip" onClick={() => setText(EXAMPLE)}>Insert an example</button>
            <label className="chip file-label">Import a file<input type="file" accept=".json,application/json" data-testid="connector-file" onChange={e => { const f = e.target.files?.[0]; if (f) void importFile(f); e.target.value = ''; }} /></label>
            <button className="primary" type="button" disabled={!name.trim() || !text.trim()} onClick={() => void save()}>Save connector</button>
          </span>
          <span className="hint">Paths can use {'{id}'} placeholders. In a body or query, a value written exactly as {'{name}'} keeps the field's type, and a field you leave out is dropped. auth.type is none, bearer, basic, header or query. Only https addresses on public hosts work.</span>
        </div>
      )}
      <div className="var-list">
        {rows === null && <span className="muted">Loading…</span>}
        {rows?.length === 0 && <span className="muted">No connectors yet.</span>}
        {rows?.map(c => (
          <div className="connector-row" key={c.name} data-testid="connector-row">
            <code>{c.name}</code>
            <span>{c.label}: {Object.keys(c.operations).length} actions, {c.auth.type} sign-in</span>
            {canEdit && <button className="ghost" type="button" aria-label={`Check ${c.name}`} onClick={() => setChecking(n => n === c.name ? '' : c.name)}>Check</button>}
            <button className="ghost" type="button" aria-label={`Download ${c.name}`} onClick={() => void download(c.name)}>Download</button>
            {canEdit && <button className="ghost" type="button" aria-label={`Edit ${c.name}`} onClick={() => void edit(c.name)}>Edit</button>}
            {canEdit && <button className="icon-button danger" aria-label={`Delete ${c.name}`} onClick={() => void remove(c.name)}><Trash2 size={15} /></button>}
            {checking === c.name && <ConnectorCheck c={c} notify={notify} />}
          </div>
        ))}
      </div>
    </div>
  );
}

/** App Action settings when the service is "Custom connector". */
export function ConnectorFields({ v, update, credSelect, fieldsLabel, getOnly }: { v: (key: string, fallback?: string) => string; update: (key: string, value: string) => void; credSelect: (field: string, label: string) => JSX.Element; fieldsLabel: string; getOnly?: boolean }) {
  const rows = useConnectors();
  const current = rows?.find(c => c.name === v('connector'));
  const ops = current ? Object.entries(current.operations).filter(([, o]) => !getOnly || o.method === 'GET') : [];
  const opKey = ops.some(([k]) => k === v('operation')) ? v('operation') : ops[0]?.[0] ?? '';
  const pickConnector = (name: string) => {
    const c = rows?.find(x => x.name === name);
    const first = c ? Object.entries(c.operations).find(([, o]) => !getOnly || o.method === 'GET')?.[0] : '';
    update('connector', name); update('operation', first ?? ''); update('fields', first ? JSON.stringify(c!.operations[first].example, null, 2) : '{}');
  };
  return (
    <>
      <label>Connector
        <select value={current ? current.name : ''} onChange={e => pickConnector(e.target.value)}>
          <option value="">{rows === null ? 'Loading…' : rows.length ? 'Choose a connector' : 'No connectors yet (add one in Settings)'}</option>
          {rows?.map(c => <option key={c.name} value={c.name}>{c.label}</option>)}
        </select>
      </label>
      {current && <label>Connector action<select value={opKey} onChange={e => { update('operation', e.target.value); update('fields', JSON.stringify(current.operations[e.target.value].example, null, 2)); }}>{ops.map(([k, o]) => <option key={k} value={k}>{o.label} ({o.method})</option>)}</select></label>}
      {current && current.auth.type !== 'none' && credSelect('credential', `${current.label} credential`)}
      <label>{fieldsLabel}<textarea rows={6} spellCheck={false} value={v('fields')} onChange={e => update('fields', e.target.value)} />
        {current && opKey && <span className="hint">Needs: {current.operations[opKey].required.join(', ') || 'nothing in particular'}. {'{{field}}'} values from the previous node are escaped for you.</span>}
      </label>
    </>
  );
}
