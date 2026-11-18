import { createContext, useContext } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ArrowRight, Braces, CalendarClock, Check, Flag, GitBranch, Globe, ListTree, Timer, Type, Webhook, X, Zap, Minus } from 'lucide-react';
import { catalog, startTypes } from './catalog';

export const icons: Record<string, typeof Zap> = { Zap, Braces, ListTree, Type, GitBranch, Timer, Globe, ArrowRight, Flag, CalendarClock, Webhook };

/** Per-node result of the latest run: success, error, or skipped (branch not taken). */
export const RunStateContext = createContext<Record<string, 'success' | 'error' | 'skipped'>>({});

export function WorkflowNode({ id, data, selected, type }: NodeProps) {
  const states = useContext(RunStateContext);
  const meta = catalog.find(c => c.type === type);
  const Icon = icons[meta?.icon ?? 'Flag'] ?? Flag;
  const state = states[id];
  const subtitle = nodeSummary(type ?? '', data as Record<string, unknown>) || meta?.desc;
  return (
    <div className={`fp-node ${selected ? 'selected' : ''} ${state ? 'state-' + state : ''}`} style={{ ['--node-color' as string]: meta?.color }}>
      {!startTypes.includes(type ?? '') && <Handle type="target" position={Position.Left} />}
      <span className="fp-node-icon"><Icon size={17} /></span>
      <span className="fp-node-text">
        <strong>{String(data.label || meta?.label || 'Node')}</strong>
        <small>{subtitle}</small>
      </span>
      {state && (
        <span className={`fp-node-badge ${state}`} title={state}>
          {state === 'success' ? <Check size={11} /> : state === 'error' ? <X size={11} /> : <Minus size={11} />}
        </span>
      )}
      {type === 'condition' ? (
        <>
          <Handle type="source" position={Position.Right} id="true" className="handle-true" style={{ top: '30%' }} />
          <Handle type="source" position={Position.Right} id="false" className="handle-false" style={{ top: '70%' }} />
          <span className="branch-label t">true</span>
          <span className="branch-label f">false</span>
        </>
      ) : type !== 'output' && <Handle type="source" position={Position.Right} />}
    </div>
  );
}

function nodeSummary(type: string, d: Record<string, unknown>): string {
  const s = (k: string) => String(d[k] ?? '');
  const clip = (t: string) => (t.length > 30 ? t.slice(0, 29) + '…' : t);
  switch (type) {
    case 'transform': return s('path') ? `path: ${clip(s('path'))}` : '';
    case 'condition': return s('path') ? clip(`${s('path')} ${s('op') || 'equals'} ${s('equals')}`.trim()) : '';
    case 'http': return s('url') ? clip(`${s('method') || 'GET'} ${s('url').replace(/^https:\/\//, '')}`) : '';
    case 'schedule': return s('mode') === 'daily' ? `daily at ${s('time') || '09:00'}` : `every ${s('minutes') || '15'} min`;
    case 'webhook': return 'waits for a request';
    case 'delay': return s('seconds') ? `wait ${s('seconds')}s` : '';
    case 'template': return s('template') ? clip(s('template')) : '';
    case 'input': return s('json') ? clip(s('json')) : '';
    default: return '';
  }
}

export const nodeTypes = Object.fromEntries(catalog.map(c => [c.type, WorkflowNode]));
