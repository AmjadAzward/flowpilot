import { useEffect, useState } from 'react';
import { request } from './api';

type Stats = { window: string; total: number; success: number; failed: number; waiting: number; ai_calls?: number; ai_tokens?: number; failure_rate: number | null; average_seconds: number | null; top_failing: { workflow_id: string; name: string; failed: number }[] };

/** A small summary of recent runs: how many worked, how many failed, how long they take, and what fails most. */
export function RunStats({ refreshKey }: { refreshKey: number }) {
  const [window_, setWindow] = useState('7d');
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => { request<Stats>(`/api/stats?window=${window_}`).then(setStats).catch(() => setStats(null)); }, [window_, refreshKey]);
  if (!stats) return null;
  const rate = stats.failure_rate === null ? '–' : `${Math.round(stats.failure_rate * 1000) / 10}%`;
  return (
    <section className="panel run-stats" data-testid="run-stats" style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'center', padding: '14px 18px', margin: '0 0 14px' }}>
      <label style={{ display: 'grid', gap: 4 }}>Period
        <select aria-label="Stats period" value={window_} onChange={e => setWindow(e.target.value)}>
          <option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option>
        </select>
      </label>
      <span><strong data-testid="stat-total">{stats.total}</strong> runs</span>
      <span><strong>{stats.success}</strong> succeeded</span>
      <span><strong data-testid="stat-failed">{stats.failed}</strong> failed ({rate})</span>
      {stats.waiting > 0 && <span><strong>{stats.waiting}</strong> waiting</span>}
      <span>Average <strong>{stats.average_seconds === null ? '–' : `${stats.average_seconds}s`}</strong></span>
      {(stats.ai_calls ?? 0) > 0 && <span data-testid="stat-ai">AI <strong>{stats.ai_calls}</strong> calls, <strong>{(stats.ai_tokens ?? 0).toLocaleString()}</strong> tokens</span>}
      {stats.top_failing.length > 0 && <span className="muted">Fails most: {stats.top_failing.map(t => `${t.name} (${t.failed})`).join(', ')}</span>}
    </section>
  );
}
