import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';

export type PaletteItem = { id: string; label: string; hint: string; run: () => void };

/** Ctrl+K (or Cmd+K): jump to a page or workflow, or start a new workflow, from the keyboard. */
export function CommandPalette({ items, onClose }: { items: PaletteItem[]; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return items.filter(i => words.every(w => i.label.toLowerCase().includes(w) || i.hint.toLowerCase().includes(w))).slice(0, 30);
  }, [items, query]);
  useEffect(() => { setActive(0); }, [query]);
  const choose = (item?: PaletteItem) => { if (!item) return; onClose(); item.run(); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, Math.max(shown.length - 1, 0))); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(shown[active]); }
  };
  return (
    <div role="dialog" aria-modal="true" aria-label="Command palette" data-testid="palette" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 60, display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: '14vh' }}>
      <div className="panel" onKeyDown={onKey} style={{ width: 'min(560px, calc(100vw - 32px))', padding: 0, overflow: 'hidden' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
          <Search size={17} />
          <input ref={input} aria-label="Search pages and workflows" placeholder="Go to a page or workflow…" value={query} onChange={e => setQuery(e.target.value)} style={{ flex: 1, border: 0, outline: 0, background: 'transparent', font: 'inherit' }} />
        </label>
        <div role="listbox" aria-label="Results" style={{ maxHeight: '50vh', overflowY: 'auto' }}>
          {shown.length === 0 && <div className="empty" style={{ padding: 16 }}>Nothing matches.</div>}
          {shown.map((item, i) => (
            <button key={item.id} role="option" aria-selected={i === active} type="button" data-testid="palette-item" className="ghost" onMouseEnter={() => setActive(i)} onClick={() => choose(item)}
              style={{ display: 'flex', width: '100%', justifyContent: 'space-between', gap: 12, padding: '10px 16px', border: 0, borderRadius: 0, textAlign: 'left', background: i === active ? 'var(--surface-2)' : 'transparent' }}>
              <span>{item.label}</span><span className="muted">{item.hint}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
