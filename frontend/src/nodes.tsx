import { createContext, useContext } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ArrowRight, Bell, Braces, CalendarClock, Check, Code, Database, Github, Hourglass, OctagonX, Bot, FileText, MailCheck, Plug, Radar, Sparkles, UserCheck, ArrowUpDown, ListEnd, CopyMinus, Ungroup, Sigma, TextCursorInput, CalendarDays, KeyRound, MailOpen, RotateCw, Reply, StickyNote, Rss, Sheet, Send, Split, Filter, Flag, GitBranch, GitMerge, Globe, ListTree, Mail, Minus, Pin, PenLine, Repeat, Table, Timer, Type, Webhook, Workflow, X, Zap } from 'lucide-react';
import { catalog, startTypes } from './catalog';

export const icons: Record<string, typeof Zap> = { Zap, Braces, ListTree, Type, GitBranch, Timer, Globe, ArrowRight, Flag, CalendarClock, Webhook, Repeat, GitMerge, Workflow, PenLine, Filter, Table, Bell, Mail, Code, OctagonX, Split, Database, Rss, Hourglass, Send, Github, Reply, Sheet, MailCheck, Bot, StickyNote, FileText, MailOpen, RotateCw, Plug, Radar, Sparkles, UserCheck, ArrowUpDown, ListEnd, CopyMinus, Ungroup, Sigma, TextCursorInput, CalendarDays, KeyRound };

/** Per-node result of the latest run: success, error, or skipped (branch not taken). */
export const RunStateContext = createContext<Record<string, 'success' | 'error' | 'skipped'>>({});

export function WorkflowNode({ id, data, selected, type }: NodeProps) {
  const states = useContext(RunStateContext);
  const meta = catalog.find(c => c.type === type);
  const Icon = icons[meta?.icon ?? 'Flag'] ?? Flag;
  const state = states[id];
  if (type === 'sticky') return <div className={`fp-sticky ${selected ? 'selected' : ''}`} data-testid="sticky">{String(data.text ?? '') || 'Empty note'}</div>;
  const subtitle = nodeSummary(type ?? '', data as Record<string, unknown>) || meta?.desc;
  return (
    <div className={`fp-node type-${type} ${selected ? 'selected' : ''} ${state ? 'state-' + state : ''}`} style={{ ['--node-color' as string]: meta?.color }}>
      {!startTypes.includes(type ?? '') && <Handle type="target" position={Position.Left} />}
      <span className="fp-node-icon"><Icon size={17} /></span>
      <span className="fp-node-text">
        <strong>{String(data.label || meta?.label || 'Node')}</strong>
        <small>{subtitle}</small>
      </span>
      {!!String(data.pin ?? '').trim() && <span className="fp-pin" title="Pinned data: this node will not run"><Pin size={10} /></span>}
      {state && (
        <span className={`fp-node-badge ${state}`} title={state}>
          {state === 'success' ? <Check size={11} /> : state === 'error' ? <X size={11} /> : <Minus size={11} />}
        </span>
      )}
      {type === 'loop' || type === 'repeat' ? (
        <>
          <Handle type="source" position={Position.Right} id="each" className="handle-each" style={{ top: '30%' }} />
          <Handle type="source" position={Position.Right} id="done" className="handle-done" style={{ top: '70%' }} />
          <span className="branch-label each-l">each</span>
          <span className="branch-label done-l">done</span>
        </>
      ) : type === 'condition' ? (
        <>
          <Handle type="source" position={Position.Right} id="true" className="handle-true" style={{ top: '30%' }} />
          <Handle type="source" position={Position.Right} id="false" className="handle-false" style={{ top: '70%' }} />
          <span className="branch-label t">true</span>
          <span className="branch-label f">false</span>
        </>
      ) : type === 'switch' ? (
        <>
          {['1', '2', '3', '4', 'default'].map((h, i) => (
            <Handle key={h} type="source" position={Position.Right} id={h} className={h === 'default' ? 'handle-false' : 'handle-each'} style={{ top: `${14 + i * 18}%` }} />
          ))}
          {['1', '2', '3', '4', 'default'].map((h, i) => <span key={h} className="branch-label sw-l" style={{ top: `${14 + i * 18}%` }}>{h}</span>)}
        </>
      ) : type !== 'output' && type !== 'fail' && <Handle type="source" position={Position.Right} />}
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
    case 'schedule': return s('mode') === 'cron' ? clip(`cron ${s('cron')}`) : s('mode') === 'daily' ? `daily at ${s('time') || '09:00'}` : `every ${s('minutes') || '15'} min`;
    case 'webhook': return 'waits for a request';
    case 'loop': return s('path') ? `items in ${s('path')}` : 'once per item';
    case 'sql': return s('query') ? clip(s('query').replace(/\s+/g, ' ')) : 'write a query';
    case 'rss': return s('url') ? clip(s('url').replace(/^https:\/\//, '')) : '';
    case 'callback': return `answer within ${s('seconds') || '1'} ${s('unit') || 'days'}`;
    case 'wait': return `wait ${s('seconds') || '5'} ${s('unit') || 'seconds'}`;
    case 'sheets': return s('spreadsheet_id') ? clip(`${s('operation') === 'append' ? 'append to' : 'read'} ${s('range')}`) : 'choose a sheet';
    case 'gmail': return s('to') ? clip(`to ${s('to')}`) : 'choose recipients';
    case 'telegram': return clip(s('text'));
    case 'sort': return clip(`${s('order') === 'desc' ? 'largest' : 'smallest'} first${s('path') ? ' by ' + s('path') : ''}`);
    case 'limit': return `${s('from') === 'last' ? 'last' : 'first'} ${s('count') || '10'}`;
    case 'dedupe': return s('path') ? clip(`by ${s('path')}`) : 'whole items';
    case 'split': return s('path') ? clip(`list in ${s('path')}`) : 'choose a field';
    case 'aggregate': return clip(`${s('operation') || 'count'}${s('path') ? ' of ' + s('path') : ''}`);
    case 'rename': return 'change field names';
    case 'datetime': return clip((s('operation') || 'add').replace('diff', 'time between'));
    case 'crypto': return clip((s('operation') || 'sha256').replace(/_/g, ' '));
    case 'app': return s('service') ? clip(`${s('service')}: ${s('operation').replace(/_/g, ' ')}`) : 'choose a service';
    case 'github': return s('repo') ? clip(`${s('operation') === 'list_issues' ? 'list' : 'new issue'} in ${s('repo')}`) : '';
    case 'respond': return `reply ${s('status') || '200'}`;
    case 'poll_in': return `${({ rss: 'feed', github_issues: 'issues', json_list: 'API list' } as Record<string, string>)[s('source') || 'rss'] ?? 'items'}, every ${s('minutes') || '15'} min`;
    case 'agent': return s('prompt') ? clip(s('prompt')) : 'write a task';
    case 'email_in': return s('credential') ? `every ${s('minutes') || '5'} min` : 'choose an inbox';
    case 'repeat': return `until ${s('path') || 'value'} ${s('op') || 'equals'} ${s('equals')}`.trim().slice(0, 34);
    case 'file': return `${s('operation') || 'read'} ${s('path')}`.trim().slice(0, 34);
    case 'ai': return s('prompt') ? clip(s('prompt')) : 'write a prompt';
    case 'switch': return s('path') ? clip(`by ${s('path')}`) : 'choose a field';
    case 'code': return 'Python snippet';
    case 'fail': return clip(s('message'));
    case 'merge': return `mode: ${s('mode') || 'append'}`;
    case 'set': return s('json') ? clip(s('json')) : '';
    case 'filter': return clip(`${s('path') || 'item'} ${s('op') || 'equals'} ${s('equals')}`.trim());
    case 'csv': return s('header') === 'false' ? 'CSV to lists' : 'CSV to rows';
    case 'notify': return clip(`${s('service') || 'slack'}: ${s('message')}`);
    case 'email': return s('to') ? clip(`to ${s('to')}`) : 'choose recipients';
    case 'subflow': return s('workflow_id') ? 'runs another workflow' : 'choose a workflow';
    case 'delay': return s('seconds') ? `wait ${s('seconds')}s` : '';
    case 'template': return s('template') ? clip(s('template')) : '';
    case 'input': return s('json') ? clip(s('json')) : '';
    default: return '';
  }
}

export const nodeTypes = Object.fromEntries(catalog.map(c => [c.type, WorkflowNode]));
