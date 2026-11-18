import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { DatabaseBackup, Download, Eraser, Upload } from 'lucide-react';
import { request } from './api';

type Cfg = { retention_days: number; max_executions: number; executions_stored: number };
type Props = { role: string; notify: (msg: string, kind?: 'ok' | 'error') => void; onChanged: () => void };

/** Settings cards for run-history retention and for backing up and restoring workflows. */
export function DataSettings({ role, notify, onChanged }: Props) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [days, setDays] = useState('90');
  const [max, setMax] = useState('5000');
  const canTune = role === 'owner' || role === 'admin';
  const isOwner = role === 'owner';

  const load = useCallback(async () => {
    try {
      const c = await request<Cfg>('/api/settings');
      setCfg(c); setDays(String(c.retention_days)); setMax(String(c.max_executions));
    } catch (e) { notify((e as Error).message, 'error'); }
  }, [notify]);
  useEffect(() => { void load(); }, [load]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const res = await request<{ removed: number }>('/api/settings', { method: 'PUT', body: JSON.stringify({ retention_days: Number(days), max_executions: Number(max) }) });
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
          <label>Keep runs for (days, 0 = forever)<input data-testid="retention-days" type="number" min={0} max={3650} value={days} onChange={e => setDays(e.target.value)} disabled={!canTune} /></label>
          <label>Keep at most (runs, 0 = no limit)<input data-testid="retention-max" type="number" min={0} max={100000} value={max} onChange={e => setMax(e.target.value)} disabled={!canTune} /></label>
          <button className="ghost" type="submit" data-testid="retention-save" disabled={!canTune}>Save and clean up now</button>
        </form>
        {!canTune && <span>Only an owner or admin can change this.</span>}
      </div>
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
