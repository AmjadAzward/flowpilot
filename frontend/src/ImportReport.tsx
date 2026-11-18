import { useEffect } from 'react';

export type ImportWarning = { node: string; message: string };
export type ImportInfo = { name: string; nodes: number; placeholders: number; warnings: ImportWarning[] };

/** Shown after an n8n workflow is converted: what came across and what needs attention. */
export function ImportReport({ info, onClose }: { info: ImportInfo; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const grouped = info.warnings.reduce<Record<string, string[]>>((acc, w) => { (acc[w.node] ??= []).push(w.message); return acc; }, {});
  return (
    <div className="tour-backdrop" role="presentation">
      <div className="tour-card import-report" role="dialog" aria-modal="true" aria-labelledby="import-title" data-testid="import-report">
        <h2 id="import-title">Imported from n8n</h2>
        <p><strong>{info.name}</strong> has {info.nodes} node{info.nodes === 1 ? '' : 's'}.{' '}
          {info.warnings.length === 0 ? 'Everything was converted without notes.' : `${info.warnings.length} thing${info.warnings.length === 1 ? '' : 's'} need${info.warnings.length === 1 ? 's' : ''} your attention before you activate it.`}
          {info.placeholders > 0 && ` ${info.placeholders} node${info.placeholders === 1 ? ' has' : 's have'} no FlowPilot equivalent and became Pass Through nodes.`}
        </p>
        {info.warnings.length > 0 && (
          <div className="import-warnings">
            {Object.entries(grouped).map(([node, list]) => (
              <div key={node}><strong>{node}</strong><ul>{list.map((m, i) => <li key={i}>{m}</li>)}</ul></div>
            ))}
          </div>
        )}
        <div className="tour-actions"><span className="spacer" /><button className="primary" autoFocus data-testid="import-report-close" onClick={onClose}>Open the workflow</button></div>
      </div>
    </div>
  );
}
