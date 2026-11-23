import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { DatabaseBackup, Download, Eraser, HeartPulse, Siren, Upload } from 'lucide-react';
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
      <p>When any workflow fails and has no error workflow of its own, this one runs with the failure details ({'{{workflow.name}}'}, {'{{error}}'}, {'{{failed_node}}'}). A workflow's own choice always wins, and an error workflow never triggers another. Failure details from every project reach this workflow, so people in its project will see them.</p>
      <label>Run this workflow on failures
        <select aria-label="Shared error workflow" value={current ?? ''} disabled={!canTune || current === null} onChange={e => void choose(e.target.value)}>
          <option value="">None</option>
          {flows.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </label>
    </div>
  );
}

/** Alert through the error workflow when a workflow turns failing, worse or slower, not only when one run fails. */
function HealthAlertsCard({ canTune, notify }: { canTune: boolean; notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => { request<{ enabled: boolean }>('/api/settings/health-alerts').then(r => setOn(r.enabled)).catch(() => setOn(false)); }, []);
  const toggle = async (enabled: boolean) => {
    try { await request('/api/settings/health-alerts', { method: 'PUT', body: JSON.stringify({ enabled }) }); setOn(enabled); notify(enabled ? 'Health alerts on' : 'Health alerts off'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  return (
    <div className="panel settings-card wide" data-testid="health-alerts-card">
      <HeartPulse size={22} /><h2>Health alerts</h2>
      <p>When a workflow turns failing, fails much more than its usual week, or runs much slower, the error workflow runs once with {'{{health.status}}'}, {'{{health.was}}'} and {'{{health.reason}}'} as well as the usual details. It does not repeat while the state stays the same. Nothing is sent unless an error workflow is set above or on the workflow itself.</p>
      <label className="check-row"><input style={{ width: 'auto' }} type="checkbox" aria-label="Alert when a workflow's health changes" checked={!!on} disabled={!canTune || on === null} onChange={e => void toggle(e.target.checked)} /> Alert when a workflow's health changes</label>
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
  const [link, setLink] = useState('');
  const used = (created: { uses_credentials?: string[] }[]) => {
    const names = [...new Set(created.flatMap(c => c.uses_credentials ?? []))];
    return names.length ? `. They refer to your credentials ${names.join(', ')}: read each workflow before running it` : '';
  };
  const importLink = async () => {
    try {
      const res = await request<{ created: { uses_credentials?: string[] }[]; skipped: { reason: string }[]; connectors_created?: string[] }>('/api/backup/import-url', { method: 'POST', body: JSON.stringify({ url: link.trim() }) });
      notify(`Added ${res.created.length} workflow${res.created.length === 1 ? '' : 's'}${res.connectors_created?.length ? ` and ${res.connectors_created.length} connector${res.connectors_created.length === 1 ? '' : 's'}` : ''} (inactive)${res.skipped.length ? `, skipped ${res.skipped.length} (${res.skipped[0].reason})` : ''}${used(res.created)}`, res.skipped.length ? 'error' : 'ok');
      setLink(''); onChanged();
    } catch (err) { notify((err as Error).message, 'error'); }
  };
  const restore = async (file: File) => {
    try {
      const data = JSON.parse(await file.text());
      const res = await request<{ created: { uses_credentials?: string[] }[]; skipped: { name: string; reason: string }[]; connectors_created?: string[]; connectors_skipped?: { name: string; reason: string }[] }>('/api/backup/restore', { method: 'POST', body: JSON.stringify(data) });
      const extra = [res.connectors_created?.length ? ` and ${res.connectors_created.length} connector${res.connectors_created.length === 1 ? '' : 's'}` : '', res.connectors_skipped?.length ? `, skipped ${res.connectors_skipped.length} connector${res.connectors_skipped.length === 1 ? '' : 's'} (${res.connectors_skipped[0].reason})` : ''].join('');
      notify(`Restored ${res.created.length} workflow${res.created.length === 1 ? '' : 's'}${extra}${res.skipped.length ? `, skipped ${res.skipped.length} (${res.skipped[0].reason})` : ''}${used(res.created)}`, res.skipped.length || res.connectors_skipped?.length ? 'error' : 'ok');
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
      <HealthAlertsCard canTune={canTune} notify={notify} />
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
        {isOwner && (
          <div className="backup-actions" style={{ marginTop: 10 }}>
            <input aria-label="Template pack link" data-testid="pack-link" placeholder="https://raw.githubusercontent.com/you/pack/main/pack.json" value={link} onChange={e => setLink(e.target.value)} spellCheck={false} style={{ flex: 1, minWidth: 240 }} />
            <button className="ghost" type="button" data-testid="pack-import" disabled={!link.trim()} onClick={() => void importLink()}><Download size={15} />Import from link</button>
          </div>
        )}
        {isOwner && <span>A template pack is the same file as the Workflows (JSON) download, so you can share workflows and connectors by hosting that file anywhere with an HTTPS link. Imported workflows are inactive and nothing is overwritten. Only import files from people you trust, and look over a workflow before activating it.</span>}
        <span>The workflow file holds no credentials. Restoring adds new inactive workflows and never overwrites. The database copy includes password hashes and encrypted credentials, so keep it private, and also keep <code>backend/flowpilot.key</code>, without which credentials cannot be decrypted.</span>
      </div>
    </>
  );
}
