import { useEffect, useRef, useState } from 'react';
import { Bell } from 'lucide-react';
import { request } from './api';

export type AttentionItem = { kind: 'approval' | 'health'; workflow_id: string; name: string; message: string; status?: string; execution_id?: string };
type Attention = { count: number; items: AttentionItem[] };

const statusWord: Record<string, string> = { failing: 'Failing', worse: 'Failing more than usual', slower: 'Slower than usual' };

/** A bell with a count of what needs a person: runs waiting for approval, and workflows that are failing or slower than usual. Worked out from stored runs; nothing is sent. */
export function AttentionBell({ refreshKey, onOpenWorkflow, onOpenRuns }: { refreshKey: number; onOpenWorkflow: (id: string) => void; onOpenRuns: () => void }) {
  const [data, setData] = useState<Attention>({ count: 0, items: [] });
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let stale = false;
    const load = () => request<Attention>('/api/attention').then(d => { if (!stale) setData(d); }).catch(() => { /* the bell just stays as it was */ });
    void load();
    const t = window.setInterval(load, 60000);
    return () => { stale = true; window.clearInterval(t); };
  }, [refreshKey]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [open]);

  return (
    <div className="attention" ref={box}>
      <button className="icon-button" data-testid="attention-bell" aria-label={data.count ? `${data.count} things need attention` : 'Nothing needs attention'} aria-expanded={open} title="What needs attention" onClick={() => setOpen(o => !o)}>
        <Bell size={17} />{data.count > 0 && <span className="attention-count" data-testid="attention-count">{data.count > 99 ? '99+' : data.count}</span>}
      </button>
      {open && (
        <div className="attention-menu" data-testid="attention-menu" role="dialog" aria-label="Needs attention">
          {data.items.length === 0 && <p className="hint" data-testid="attention-empty" style={{ margin: 0 }}>Nothing needs attention. No run is waiting for an answer and no workflow is failing.</p>}
          {data.items.map((i, n) => (
            <button key={n} className="attention-item" data-testid="attention-item" onClick={() => { setOpen(false); if (i.kind === 'approval') onOpenRuns(); else onOpenWorkflow(i.workflow_id); }}>
              <strong>{i.name}</strong>
              <span className={'attention-tag ' + (i.kind === 'approval' ? 'ask' : i.status)}>{i.kind === 'approval' ? 'Needs an answer' : statusWord[i.status ?? ''] ?? i.status}</span>
              <small className="muted">{i.message}</small>
            </button>
          ))}
          {data.items.length > 0 && <small className="muted">Worked out from stored runs when you look. Nothing is sent to you.</small>}
        </div>
      )}
    </div>
  );
}
