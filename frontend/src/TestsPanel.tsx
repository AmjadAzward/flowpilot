import { useCallback, useEffect, useState } from 'react';
import { FlaskConical, Play, Trash2, X } from 'lucide-react';
import { request } from './api';

type Notify = (msg: string, kind?: 'ok' | 'error') => void;
type Case = { id: string; name: string; input: unknown; expected: unknown; mode: string; expect: string };
type Result = { id: string; name: string; passed: boolean; status: string; error: string | null; actual: unknown };

/** Saved test cases for one workflow: sample input plus the output you expect. Running them is a dry run, so nothing is sent or written. */
export function TestsPanel({ workflowId, canEdit, notify, onClose, suggestInput, suggestExpected }: { workflowId: string; canEdit: boolean; notify: Notify; onClose: () => void; suggestInput: string; suggestExpected: string }) {
  const [cases, setCases] = useState<Case[] | null>(null);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [name, setName] = useState('');
  const [input, setInput] = useState(suggestInput);
  const [expected, setExpected] = useState(suggestExpected);
  const [mode, setMode] = useState('contains');
  const [expect, setExpect] = useState('success');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setCases(await request<Case[]>(`/api/workflows/${workflowId}/tests`)); }
    catch (e) { setCases([]); notify((e as Error).message, 'error'); }
  }, [workflowId, notify]);
  useEffect(() => { void load(); }, [load]);

  const parse = (text: string, label: string): { ok: true; value: unknown } | { ok: false } => {
    if (!text.trim()) return { ok: true, value: null };
    try { return { ok: true, value: JSON.parse(text) }; } catch { notify(`${label} is not valid JSON`, 'error'); return { ok: false }; }
  };
  const add = async () => {
    const i = parse(input, 'The input'); if (!i.ok) return;
    let exp: unknown = null;
    if (expect === 'failed') exp = expected.trim() || null; // for an expected failure this is just text from the error message
    else { const e = parse(expected, 'The expected output'); if (!e.ok) return; exp = e.value; }
    setBusy(true);
    try {
      await request(`/api/workflows/${workflowId}/tests`, { method: 'POST', body: JSON.stringify({ name: name.trim(), input: i.value, expected: exp, mode, expect }) });
      setName(''); await load(); notify('Test saved');
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const remove = async (c: Case) => {
    try { await request(`/api/workflows/${workflowId}/tests/${c.id}`, { method: 'DELETE' }); setResults(r => { const n = { ...r }; delete n[c.id]; return n; }); await load(); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const runAll = async () => {
    setBusy(true);
    try {
      const res = await request<{ passed: number; failed: number; results: Result[] }>(`/api/workflows/${workflowId}/tests/run`, { method: 'POST' });
      setResults(Object.fromEntries(res.results.map(r => [r.id, r])));
      if (res.failed) notify(`${res.failed} of ${res.passed + res.failed} tests failed`, 'error'); else notify(`All ${res.passed} tests passed`);
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };

  return (
    <aside className="history-drawer" data-testid="tests-panel" aria-label="Workflow tests">
      <div className="mini-heading"><span><FlaskConical size={14} /> Tests</span><button className="icon-button" aria-label="Close tests" onClick={onClose}><X size={15} /></button></div>
      <p className="hint pad">A test is a sample input and the output you expect. Tests run as a dry run: reads happen, but messages and writes are only described.</p>
      <div style={{ padding: '0 16px 12px' }}>
        <button className="primary" data-testid="run-tests" disabled={busy || !cases?.length} onClick={() => void runAll()}><Play size={14} />Run all tests</button>
      </div>
      {cases?.length === 0 && <div className="empty">No tests yet. Add one below.</div>}
      {cases?.map(c => {
        const r = results[c.id];
        return (
          <div className="version-row" key={c.id} data-testid="test-row">
            <div>
              <strong>{r ? (r.passed ? '✓ ' : '✗ ') : ''}{c.name}</strong>
              <span className="muted">{c.expect === 'failed' ? 'expects a failure' : `${c.mode === 'equals' ? 'exactly' : 'contains'} ${JSON.stringify(c.expected ?? 'any output')}`}</span>
              {r && !r.passed && <span className="muted" style={{ whiteSpace: 'normal' }} data-testid="test-detail">Got {r.status}: {typeof r.actual === 'string' ? r.actual : JSON.stringify(r.actual)}</span>}
            </div>
            <button className="icon-button" aria-label={`Delete test ${c.name}`} disabled={!canEdit} onClick={() => void remove(c)}><Trash2 size={14} /></button>
          </div>
        );
      })}
      <div style={{ display: 'grid', gap: 8, padding: 16, borderTop: '1px solid var(--border)' }}>
        <strong>New test</strong>
        <label>Name<input aria-label="Test name" value={name} onChange={e => setName(e.target.value)} maxLength={80} disabled={!canEdit} /></label>
        <label>Input the trigger passes on (JSON)<textarea aria-label="Test input" rows={3} value={input} onChange={e => setInput(e.target.value)} spellCheck={false} disabled={!canEdit} /></label>
        <label>It should<select aria-label="Test expectation" value={expect} onChange={e => setExpect(e.target.value)} disabled={!canEdit}><option value="success">succeed with this output</option><option value="failed">fail with this message</option></select></label>
        <label>{expect === 'failed' ? 'Text the error must contain' : 'Expected output (JSON)'}<textarea aria-label="Test expected" rows={3} value={expected} onChange={e => setExpected(e.target.value)} spellCheck={false} disabled={!canEdit} /></label>
        {expect === 'success' && <label>Compare<select aria-label="Test comparison" value={mode} onChange={e => setMode(e.target.value)} disabled={!canEdit}><option value="contains">The output contains these fields</option><option value="equals">The output is exactly this</option></select></label>}
        <div><button className="ghost" data-testid="add-test" disabled={!canEdit || busy || !name.trim()} onClick={() => void add()}>Save test</button></div>
        <span className="hint">The boxes start with the data from your last run, so you can save what just worked as a test.</span>
      </div>
    </aside>
  );
}
