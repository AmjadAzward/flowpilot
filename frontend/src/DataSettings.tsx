import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { DatabaseBackup, Download, Eraser, Siren, Upload } from 'lucide-react';
import { request } from './api';

type Cfg = { retention_days: number; max_executions: number; executions_stored: number };
type Props = { role: string; notify: (msg: string, kind?: 'ok' | 'error') => void; onChanged: () => void };

/** One workflow that runs whenever another workflow fails and has no error workflow of its own. */
function ErrorWorkflowCard({ canTune, notify }: { canTune: boolean; notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const [current, setCurrent] = useState<string | null>(null);
  const [flows, setFlows] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => {
    request<{ workflow_id: string }>('/api/settings/error-workflow').then(r => setCurrent(r.workflow_id)).catch(() => setCurrent(''));
    request<{ id: string; name: string }[]>('/api/workflows').then(setFlows).catch(() => setFlows([]));
  }, []);
  const choose = async (id: string) => {
    try { await request('/api/settings/error-workflow', { method: 'PUT', body: JSON.stringify({ workflow_id: id }) }); setCurrent(id); notify(id ? 'Shared error workflow saved' : 'Shared error workflow cleared'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  return (
    <div className="panel settings-card wide" data-testid="error-workflow-card">
      <Siren size={22} /><h2>Shared error workflow</h2>
      <p>When any workflow fails and has no error workflow of its own, this one runs with the failure details ({'{{workflow.name}}'}, {'{{error}}'}, {'{{failed_node}}'}). A workflow's own choice always wins, and an error workflow never triggers another.</p>
      <label>Run this workflow on failures
        <select aria-label="Shared error workflow" value={current ?? ''} disabled={!canTune || current === null} onChange={e => void choose(e.target.value)}>
          <option value="">None</option>
          {flows.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </label>
    </div>
  );
}

/** Settings cards for run-history retention and for backing up and restoring workflows. */
export function DataSettings({ role, notify, onChanged }: Props) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [days, setDays] = useState('90');
  const [max, setMax] = useState('5000');
  const edited = useRef(false);
  const canTune = role === 'owner' || role === 'admin';
  const isOwner = role === 'owner';

  const load = useCallback(async () => {
    try {
      const c = await request<Cfg>('/api/settings');
      setCfg(c);
      if (!edited.current) { setDays(String(c.retention_days)); setMax(String(c.max_executions)); }  // a late reply must never overwrite what the user has typed
    } catch (e) { notify((e as Error).message, 'error'); }
  }, [notify]);
  useEffect(() => { void load(); }, [load]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const res = await request<{ removed: number }>('/api/settings', { method: 'PUT', body: JSON.stringify({ retention_days: Number(days), max_executions: Number(max) }) });
      edited.current = false;
      notify(res.removed ? `Saved. ${res.removed} old runs removed.` : 'Saved'); await load(); onChanged();
    } catch (err) { notify((err as Error).message, 'error'); }
  };
  const restore = async (file: File) => {
    try {
      const data = JSON.parse(await file.text());
      const res = await request<{ created: unknown[]; skipped: { name: string; reason: string }[] }>('/api/backup/restore', { method: 'POST', body: JSON.stringify(data) });
      notify(`Restored ${res.created.length} workflow${res.created.length === 1 ? '' : 's'}${res.skipped.length ? `, skipped ${res.skipped.length} (${res.skipped[0].reason})` : ''}`, res.skipped.length ? 'error' : 'ok');
      onChanged();
    } catch (err) { notify(err instanceof SyntaxError ? 'That file is not valid JSON' : (err as Error).message, 'error'); }
  };

  return (
    <>
      <div className="panel settings-card wide" data-testid="retention-card">
        <Eraser size={22} /><h2>Run history</h2>
        <p>{cfg ? `${cfg.executions_stored} runs stored.` : 'Loading…'} Old runs are removed automatically, and runs that are still waiting are never removed.</p>
        <form className="retention-form" onSubmit={e => void save(e)}>
          <label>Keep runs for (days, 0 = forever)<input data-testid="retention-days" type="number" min={0} max={3650} value={days} onChange={e => { edited.current = true; setDays(e.target.value); }} disabled={!canTune || !cfg} /></label>
          <label>Keep at most (runs, 0 = no limit)<input data-testid="retention-max" type="number" min={0} max={100000} value={max} onChange={e => { edited.current = true; setMax(e.target.value); }} disabled={!canTune || !cfg} /></label>
          <button className="ghost" type="submit" data-testid="retention-save" disabled={!canTune || !cfg}>Save and clean up now</button>
        </form>
        {!canTune && <span>Only an owner or admin can change this.</span>}
      </div>
      <ErrorWorkflowCard canTune={canTune} notify={notify} />
      <div className="panel settings-card wide" data-testid="backup-card">
        <DatabaseBackup size={22} /><h2>Backup and restore</h2>
        <p>Download your workflows, or a full copy of the database.</p>
        {isOwner ? (
          <div className="backup-actions">
            <a className="ghost" data-testid="backup-workflows" href="/api/backup/workflows" download><Download size={15} />Workflows (JSON)</a>
            <a className="ghost" data-testid="backup-database" href="/api/backup/database" download><Download size={15} />Full database</a>
            <label className="ghost file-label"><Upload size={15} />Restore workflows<input type="file" accept=".json,application/json" data-testid="restore-file" onChange={e => { const f = e.target.files?.[0]; if (f) void restore(f); e.target.value = ''; }} /></label>
          </div>
        ) : <span>Only the owner can back up and restore.</span>}
        <span>The workflow file holds no credentials. Restoring adds new inactive workflows and never overwrites. The database copy includes password hashes and encrypted credentials, so keep it private, and also keep <code>backend/flowpilot.key</code>, without which credentials cannot be decrypted.</span>
      </div>
    </>
  );
}
