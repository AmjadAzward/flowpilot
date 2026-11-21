import { useState, type ReactNode } from 'react';

/** All field paths in a sample value, like name, user.email, items.0.id (down to three levels, 60 at most). */
export function fieldPaths(value: unknown, prefix = '', depth = 0, out: string[] = []): string[] {
  if (out.length >= 60 || depth > 3 || value === null || typeof value !== 'object') return out;
  const entries = Array.isArray(value) ? value.slice(0, 1).map((v, i) => [String(i), v] as const) : Object.entries(value as Record<string, unknown>);
  for (const [k, v] of entries) {
    if (out.length >= 60) break;
    const path = prefix ? `${prefix}.${k}` : k;
    out.push(path);
    fieldPaths(v, path, depth + 1, out);
  }
  return out;
}

const OPEN = /\{\{\s*([\w.\-]*)$/;

type Active = { el: HTMLInputElement | HTMLTextAreaElement; start: number; query: string } | null;

/** Suggests fields from the previous step's real data while you type {{ in any setting. */
export function ExpressionHints({ fields, children }: { fields: string[]; children: ReactNode }) {
  const [active, setActive] = useState<Active>(null);

  const look = (target: EventTarget | null) => {
    const el = target as HTMLInputElement | HTMLTextAreaElement | null;
    if (!el || !(el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') || (el instanceof HTMLInputElement && !['text', 'search', ''].includes(el.type))) { setActive(null); return; }
    const caret = el.selectionStart ?? 0;
    const m = OPEN.exec(el.value.slice(0, caret));
    setActive(m ? { el, start: caret - m[1].length, query: m[1] } : null);
  };

  const insert = (field: string) => {
    if (!active) return;
    const { el, start, query } = active;
    const caret = start + query.length;
    const after = el.value.slice(caret);
    const closing = /^\s*\}\}/.test(after) ? '' : '}}';
    const next = el.value.slice(0, start) + field + closing + after;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, next); // so React sees the change
    el.dispatchEvent(new Event('input', { bubbles: true }));
    const pos = start + field.length + closing.length;
    el.focus(); el.setSelectionRange(pos, pos);
    setActive(null);
  };

  const shown = active ? fields.filter(f => f.toLowerCase().includes(active.query.toLowerCase())).slice(0, 12) : [];
  return (
    <div onKeyUp={e => { if (e.key === 'Escape') setActive(null); else look(e.target); }} onClick={e => look(e.target)} onInput={e => look(e.target)} onBlur={() => window.setTimeout(() => setActive(null), 150)}>
      {active && (
        <div className="expr-hints" data-testid="expr-hints" role="listbox" aria-label="Fields from the previous step">
          {fields.length === 0 ? <span className="hint">Run the workflow once to see the fields from the previous step.</span>
            : shown.length === 0 ? <span className="hint">No field matches “{active.query}”.</span>
            : <><span className="hint">Fields from the previous step:</span>{shown.map(f => <button type="button" key={f} role="option" aria-selected={false} className="expr-chip" onMouseDown={e => { e.preventDefault(); insert(f); }}>{f}</button>)}</>}
        </div>
      )}
      {children}
    </div>
  );
}
