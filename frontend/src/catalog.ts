import type { Edge, Node } from '@xyflow/react';

export type FlowDoc = { id: string; name: string; description: string; nodes: Node[]; edges: Edge[]; updated_at?: string };
export type Step = { node_id: string; label: string; type?: string; status: string; output?: unknown; error?: string; branch?: string | null; duration_ms?: number };
export type Run = { id: string; status: string; error: string | null; output: unknown; steps: Step[]; started_at?: string; workflow_id?: string };

export type NodeKind = 'trigger' | 'input' | 'transform' | 'template' | 'condition' | 'http' | 'delay' | 'note' | 'output';
export type Category = 'Start' | 'Data' | 'Logic' | 'Network' | 'Finish';

export const catalog: { type: NodeKind; label: string; desc: string; category: Category; color: string; icon: string }[] = [
  { type: 'trigger', label: 'Manual Trigger', desc: 'Start a workflow', category: 'Start', color: '#f59e0b', icon: 'Zap' },
  { type: 'input', label: 'JSON Input', desc: 'Provide structured data', category: 'Data', color: '#38bdf8', icon: 'Braces' },
  { type: 'transform', label: 'Extract Field', desc: 'Read a nested JSON path', category: 'Data', color: '#2dd4bf', icon: 'ListTree' },
  { type: 'template', label: 'Text Template', desc: 'Build text with {{placeholders}}', category: 'Data', color: '#a78bfa', icon: 'Type' },
  { type: 'condition', label: 'Compare Value', desc: 'Branch into true and false paths', category: 'Logic', color: '#f472b6', icon: 'GitBranch' },
  { type: 'delay', label: 'Delay', desc: 'Wait up to 5 seconds', category: 'Logic', color: '#fb923c', icon: 'Timer' },
  { type: 'http', label: 'HTTP GET', desc: 'Fetch a public HTTPS resource', category: 'Network', color: '#818cf8', icon: 'Globe' },
  { type: 'note', label: 'Pass Through', desc: 'Forward data unchanged', category: 'Logic', color: '#94a3b8', icon: 'ArrowRight' },
  { type: 'output', label: 'Result Output', desc: 'Finish and inspect data', category: 'Finish', color: '#34d399', icon: 'Flag' },
];

export const operators = [
  { value: 'equals', label: 'equals' },
  { value: 'not_equals', label: 'does not equal' },
  { value: 'contains', label: 'contains' },
  { value: 'gt', label: 'is greater than' },
  { value: 'lt', label: 'is less than' },
  { value: 'exists', label: 'exists' },
  { value: 'not_exists', label: 'does not exist' },
];

export const defaultData = (type: string): Record<string, string> => {
  switch (type) {
    case 'input': return { json: '{"hello":"world"}' };
    case 'transform': return { path: 'hello' };
    case 'template': return { template: 'Hello {{hello}}!' };
    case 'condition': return { path: 'status', op: 'equals', equals: 'active' };
    case 'delay': return { seconds: '1' };
    case 'http': return { url: 'https://jsonplaceholder.typicode.com/todos/1', method: 'GET' };
    default: return {};
  }
};

const n = (id: string, type: NodeKind, x: number, y: number, extra: Record<string, string> = {}): Node => ({
  id, type, position: { x, y }, data: { label: catalog.find(c => c.type === type)!.label, ...extra },
});
const e = (source: string, target: string, sourceHandle?: string): Edge => ({ id: `${source}-${target}${sourceHandle ? '-' + sourceHandle : ''}`, source, target, ...(sourceHandle ? { sourceHandle } : {}) });

export const blankNodes: Node[] = [n('start', 'trigger', 60, 160), n('input', 'input', 330, 160, { json: '{"message":"Hello FlowPilot"}' }), n('out', 'output', 600, 160)];
export const blankEdges: Edge[] = [e('start', 'input'), e('input', 'out')];

export const templates: { id: string; name: string; description: string; tag: string; nodes: Node[]; edges: Edge[] }[] = [
  {
    id: 'greeting', name: 'Personalised greeting', tag: 'Starter',
    description: 'Read a name from JSON and build a greeting with a text template.',
    nodes: [n('t', 'trigger', 40, 160), n('i', 'input', 290, 160, { json: '{"name":"Ada","city":"London"}' }), n('m', 'template', 560, 160, { template: 'Hello {{name}} from {{city}}!' }), n('o', 'output', 830, 160)],
    edges: [e('t', 'i'), e('i', 'm'), e('m', 'o')],
  },
  {
    id: 'order-triage', name: 'Order triage', tag: 'Branching',
    description: 'Route orders by value: big orders get a priority message, others a standard one.',
    nodes: [
      n('t', 'trigger', 20, 150), n('i', 'input', 250, 150, { json: '{"order":"A-1042","total":250}' }),
      n('c', 'condition', 480, 150, { path: 'total', op: 'gt', equals: '100' }),
      n('hi', 'template', 740, 40, { template: 'PRIORITY: order {{order}} ({{total}})' }),
      n('lo', 'template', 740, 270, { template: 'Standard: order {{order}} ({{total}})' }),
      n('o1', 'output', 990, 40), n('o2', 'output', 990, 270),
    ],
    edges: [e('t', 'i'), e('i', 'c'), e('c', 'hi', 'true'), e('c', 'lo', 'false'), e('hi', 'o1'), e('lo', 'o2')],
  },
  {
    id: 'fetch-todo', name: 'Fetch and extract', tag: 'Network',
    description: 'Call a public API over HTTPS and pull one field out of the response.',
    nodes: [n('t', 'trigger', 40, 160), n('h', 'http', 290, 160, { url: 'https://jsonplaceholder.typicode.com/todos/1', method: 'GET' }), n('x', 'transform', 560, 160, { path: 'title' }), n('o', 'output', 830, 160)],
    edges: [e('t', 'h'), e('h', 'x'), e('x', 'o')],
  },
];
