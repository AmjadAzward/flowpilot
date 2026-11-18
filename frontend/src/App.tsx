import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Controls, MiniMap, addEdge, useNodesState, useEdgesState, type Connection, type Node, type Edge, type ReactFlowInstance } from '@xyflow/react';
import {
  Activity, ArrowLeft, CheckCircle2, ChevronRight, CircleHelp, Clock3, Code2, Copy, Database, Download, Layers3,
  History as HistoryIcon, KeyRound, LayoutDashboard, Users as UsersIcon, LogOut, Moon, Network, Pin, PinOff, Play, Plus, Redo2, RefreshCcw, Save, Search, Settings2, ShieldCheck, Sparkles, Sun, Trash2, Undo2, Upload, Workflow, X, Zap, TrendingUp,
} from 'lucide-react';
import { blankEdges, blankNodes, catalog, defaultData, pinnableTypes, startTypes, templates, type FlowDoc, type Run, type TriggerKind } from './catalog';
import { icons, nodeTypes, RunStateContext } from './nodes';
import { MiniFlow } from './MiniFlow';
import { NodeSettings } from './NodeSettings';
import { Login } from './Login';
import { ChangePassword, UsersPage } from './UsersPage';
import { HistoryPanel } from './HistoryPanel';
import { OAuthConnect } from './OAuthConnect';
import { DataSettings } from './DataSettings';

type Page = 'dashboard' | 'editor' | 'executions' | 'credentials' | 'users' | 'settings';
type CredentialRow = { name: string; created_at: string; updated_at: string; kind?: 'secret' | 'oauth2'; provider?: string | null };
type Theme = 'dark' | 'light';

const api = async <T,>(url: string, options?: RequestInit): Promise<T> => {
  const res = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (res.status === 401) window.dispatchEvent(new Event('fp-unauthorized'));
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const detail = await res.json(); msg = typeof detail.detail === 'string' ? detail.detail : msg; } catch { /* keep default */ }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
};

const triggerLabel: Record<TriggerKind | 'error', string> = { manual: 'Manual', schedule: 'Schedule', webhook: 'Webhook', email_in: 'Email', error: 'On error' };
const formatTime = (date?: string | null) => (date ? new Date(date).toLocaleString() : '—');
const pretty = (obj: unknown) => JSON.stringify(obj, null, 2);
const edgeStyle = { stroke: 'var(--edge)', strokeWidth: 1.75 };
const cleanNodes = (ns: Node[]) => ns.map(({ id, type, position, data }) => ({ id, type, position, data }));
const cleanEdges = (es: Edge[]) => es.map(({ id, source, target, sourceHandle }) => ({ id, source, target, ...(sourceHandle ? { sourceHandle } : {}) }));

function readTheme(): Theme {
  try { const t = localStorage.getItem('fp-theme'); if (t === 'light' || t === 'dark') return t; } catch { /* storage blocked */ }
  return 'light';
}

type AuthState = { phase: 'loading' | 'login' | 'setup' | 'ready' | 'offline'; username: string; role: string; projects?: { id: string; name: string }[] };

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ phase: 'loading', username: '', role: '' });
  const check = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/status');
      const st = (await res.json()) as { setup_required: boolean; authenticated: boolean; username: string | null; role: string | null; projects?: { id: string; name: string }[] };
      setAuth(st.authenticated ? { phase: 'ready', username: st.username ?? '', role: st.role ?? 'viewer', projects: st.projects ?? [] } : { phase: st.setup_required ? 'setup' : 'login', username: '', role: '' });
    } catch { setAuth({ phase: 'offline', username: '', role: '' }); }
  }, []);
  useEffect(() => { void check(); }, [check]);
  useEffect(() => {
    const onUnauth = () => setAuth(a => (a.phase === 'ready' ? { phase: 'login', username: '', role: '' } : a));
    window.addEventListener('fp-unauthorized', onUnauth);
    return () => window.removeEventListener('fp-unauthorized', onUnauth);
  }, []);
  useEffect(() => {
    if (auth.phase === 'login') document.title = 'Sign in - FlowPilot';
    else if (auth.phase === 'setup') document.title = 'Create account - FlowPilot';
    else if (auth.phase === 'loading' || auth.phase === 'offline') document.title = 'FlowPilot - Workflow Automation';
  }, [auth.phase]);
  const signOut = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
    setAuth({ phase: 'login', username: '', role: '' });
  };

  if (auth.phase === 'loading') return <div className="splash" aria-busy="true"><span className="spinner" /></div>;
  if (auth.phase === 'offline') return (
    <div className="splash"><div className="offline-card"><h1>Cannot reach the FlowPilot API</h1><p>Make sure the backend is running on port 8000, then try again.</p><button className="primary" onClick={() => { setAuth({ phase: 'loading', username: '', role: '' }); void check(); }}>Retry</button></div></div>
  );
  if (auth.phase === 'login' || auth.phase === 'setup') return <Login mode={auth.phase} onDone={(username, role) => setAuth({ phase: 'ready', username, role })} />;
  return <Workspace username={auth.username} role={auth.role} projects={auth.projects ?? []} onSignOut={() => void signOut()} />;
}

function Workspace({ username, role, projects, onSignOut }: { username: string; role: string; projects: { id: string; name: string }[]; onSignOut: () => void }) {
  const [projectList, setProjectList] = useState(projects);
  const [newProject, setNewProject] = useState('');
  const [page, setPage] = useState<Page>('dashboard');
  const [showHistory, setShowHistory] = useState(false);
  const canEdit = role !== 'viewer';
  const canCreds = role === 'owner' || role === 'admin';
  const isOwner = role === 'owner';
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [flows, setFlows] = useState<FlowDoc[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [flowName, setFlowName] = useState('Untitled workflow');
  const [description, setDescription] = useState('');
  const [tagsText, setTagsText] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [lastRun, setLastRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: 'ok' | 'error' } | null>(null);
  const [query, setQuery] = useState('');
  const [libQuery, setLibQuery] = useState('');
  const [runFilter, setRunFilter] = useState<'all' | 'success' | 'failed'>('all');
  const [showCatalog, setShowCatalog] = useState(() => window.innerWidth >= 1500);
  const [apiOnline, setApiOnline] = useState(true);
  const [webhookToken, setWebhookToken] = useState('');
  const [savedSnap, setSavedSnap] = useState('');
  const hist = useRef<{ stack: string[]; idx: number }>({ stack: [], idx: -1 });
  const clip = useRef<{ nodes: Node[]; edges: Edge[] } | null>(null);
  const [creds, setCreds] = useState<CredentialRow[]>([]);
  const [credName, setCredName] = useState('');
  const [credValue, setCredValue] = useState('');
  const toastTimer = useRef<number>();
  const flowRef = useRef<ReactFlowInstance | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('fp-theme', theme); } catch { /* storage blocked */ }
  }, [theme]);

  const notify = useCallback((msg: string, kind: 'ok' | 'error' = 'ok') => {
    setToast({ msg, kind });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3800);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [f, r, c] = await Promise.all([api<FlowDoc[]>('/api/workflows'), api<Run[]>('/api/executions'), api<CredentialRow[]>('/api/credentials')]);
      setFlows(f); setRuns(r); setCreds(c); setApiOnline(true);
    } catch (e) { setApiOnline(false); notify(`API offline: ${(e as Error).message}`, 'error'); }
  }, [notify]);
  useEffect(() => { void refresh(); }, [refresh]);

  const snapshot = useMemo(() => JSON.stringify({ flowName, description, tagsText, n: cleanNodes(nodes), e: cleanEdges(edges) }), [flowName, description, tagsText, nodes, edges]);
  const dirty = page === 'editor' && !!selectedId && snapshot !== savedSnap;

  const onConnect = useCallback((c: Connection) => setEdges(es => addEdge({ ...c, style: edgeStyle }, es)), [setEdges]);

  const loadDoc = (doc: FlowDoc) => {
    const ns = doc.nodes, es = doc.edges;
    setSelectedId(doc.id); setFlowName(doc.name); setDescription(doc.description); setTagsText((doc.tags ?? []).join(', ')); setWebhookToken(doc.webhook_token ?? '');
    setNodes(ns); setEdges(es); setSelectedNode(null); setLastRun(null);
    setSavedSnap(JSON.stringify({ flowName: doc.name, description: doc.description, tagsText: (doc.tags ?? []).join(', '), n: cleanNodes(ns), e: cleanEdges(es) }));
    hist.current = { stack: [JSON.stringify({ n: cleanNodes(ns), e: cleanEdges(es) })], idx: 0 };
    setPage('editor');
  };
  const openFlow = async (id: string) => {
    try { loadDoc(await api<FlowDoc>(`/api/workflows/${id}`)); } catch (e) { notify((e as Error).message, 'error'); }
  };
  const createFlow = async (name = 'Untitled workflow', desc = 'Build something great.', ns: Node[] = blankNodes, es: Edge[] = blankEdges) => {
    try {
      const doc = await api<{ id: string }>(`/api/workflows${newProject ? `?project_id=${encodeURIComponent(newProject)}` : ''}`, { method: 'POST', body: JSON.stringify({ name, description: desc, nodes: ns, edges: es }) });
      await refresh(); await openFlow(doc.id); notify('Workflow created');
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const payload = () => JSON.stringify({ name: flowName.trim() || 'Untitled workflow', description, tags: tagsText.split(',').map(t => t.trim()).filter(Boolean), nodes: cleanNodes(nodes), edges: cleanEdges(edges) });
  const saveFlow = useCallback(async (silent = false) => {
    if (!selectedId) return false;
    try {
      const res = await api<{ deactivated_reason?: string | null }>(`/api/workflows/${selectedId}`, { method: 'PUT', body: payload() });
      setSavedSnap(snapshot); await refresh();
      if (res.deactivated_reason) notify(`Workflow deactivated: ${res.deactivated_reason}`, 'error');
      else if (!silent) notify('Workflow saved');
      return true;
    } catch (e) { notify((e as Error).message, 'error'); return false; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, snapshot, flowName, description, tagsText, nodes, edges, refresh, notify]);
  const execute = async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      if (!(await saveFlow(true))) return;
      const result = await api<Run>(`/api/workflows/${selectedId}/run`, { method: 'POST' });
      setLastRun(result); await refresh();
      notify(result.status === 'success' ? 'Workflow completed' : result.status === 'waiting' ? `Workflow is waiting until ${formatTime(result.wait?.resume_at)}` : 'Workflow failed: see the run panel', result.status === 'failed' ? 'error' : 'ok');
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const setActive = async (id: string, on: boolean) => {
    try {
      if (id === selectedId && dirty && !(await saveFlow(true))) return;
      const res = await api<{ next_run_at: string | null }>(`/api/workflows/${id}/active`, { method: 'POST', body: JSON.stringify({ active: on }) });
      await refresh();
      notify(on ? (res.next_run_at ? `Active. Next run ${formatTime(res.next_run_at)}` : 'Active. The webhook is now listening') : 'Workflow deactivated');
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const webhookUrl = `${window.location.origin}/api/hooks/${webhookToken}`;
  const copyWebhook = async () => {
    try { await navigator.clipboard.writeText(webhookUrl); notify('Webhook URL copied'); }
    catch { notify('Copy failed. Select the URL and copy it manually.', 'error'); }
  };
  const rotateWebhook = async () => {
    if (!window.confirm('Generate a new URL? The old URL stops working immediately.')) return;
    try { const r = await api<{ webhook_token: string }>(`/api/workflows/${selectedId}/webhook/rotate`, { method: 'POST' }); setWebhookToken(r.webhook_token); notify('New webhook URL generated'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const runFromDashboard = async (id: string) => {
    try {
      const result = await api<Run>(`/api/workflows/${id}/run`, { method: 'POST' });
      await refresh(); notify(result.status === 'success' ? 'Workflow completed' : `Workflow failed: ${result.error}`, result.status === 'success' ? 'ok' : 'error');
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const removeFlow = async (id: string) => {
    if (!window.confirm('Delete this workflow and its execution logs permanently?')) return;
    try {
      await api(`/api/workflows/${id}`, { method: 'DELETE' });
      if (id === selectedId) { setSelectedId(''); setPage('dashboard'); }
      await refresh(); notify('Workflow deleted');
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const duplicateFlow = async (id: string) => {
    try { await api(`/api/workflows/${id}/duplicate`, { method: 'POST' }); await refresh(); notify('Workflow duplicated'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const clearRuns = async () => {
    if (!window.confirm('Delete all execution history?')) return;
    try { await api('/api/executions', { method: 'DELETE' }); await refresh(); notify('History cleared'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const addNode = (type: string, label: string) => {
    const id = crypto.randomUUID();
    setNodes(ns => [...ns, { id, type, position: { x: 130 + (ns.length % 4) * 250, y: 80 + Math.floor(ns.length / 4) * 140 }, data: { label, ...defaultData(type) } }]);
    setSelectedNode(id);
  };
  const currentNode = useMemo(() => nodes.find(n => n.id === selectedNode), [nodes, selectedNode]);
  const nodeSteps = useMemo(() => (lastRun && selectedNode ? lastRun.steps.filter(st => st.node_id === selectedNode) : []), [lastRun, selectedNode]);
  const shownStep = nodeSteps[nodeSteps.length - 1];
  const isPinned = !!String(currentNode?.data.pin ?? '').trim();
  const canPin = !!shownStep && shownStep.status === 'success' && shownStep.output !== undefined && !shownStep.pinned;
  const updateData = (field: string, value: string) => setNodes(ns => ns.map(n => (n.id === selectedNode ? { ...n, data: { ...n.data, [field]: value } } : n)));
  const deleteNode = () => {
    setNodes(n => n.filter(x => x.id !== selectedNode));
    setEdges(e => e.filter(x => x.source !== selectedNode && x.target !== selectedNode));
    setSelectedNode(null);
  };
  const download = () => {
    const file = new Blob([JSON.stringify({ name: flowName, description, nodes: cleanNodes(nodes), edges: cleanEdges(edges) }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(file);
    a.download = `${flowName.toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'workflow'}.json`; a.click(); URL.revokeObjectURL(a.href);
  };
  const upload = async (file: File) => {
    try {
      const doc = JSON.parse(await file.text());
      if (!Array.isArray(doc.nodes) || !Array.isArray(doc.edges)) throw Error('Invalid workflow file');
      await createFlow(doc.name || 'Imported workflow', doc.description || '', doc.nodes, doc.edges);
    } catch (e) { notify((e as Error).message, 'error'); }
  };

  // ---- undo / redo ---------------------------------------------------------------------------
  const graphSnap = useMemo(() => JSON.stringify({ n: cleanNodes(nodes), e: cleanEdges(edges) }), [nodes, edges]);
  const graphRef = useRef(graphSnap);
  graphRef.current = graphSnap;
  const commitHistory = useCallback(() => {
    const h = hist.current;
    if (h.idx < 0 || h.stack[h.idx] === graphRef.current) return;
    h.stack = h.stack.slice(0, h.idx + 1);
    h.stack.push(graphRef.current);
    if (h.stack.length > 100) h.stack.shift();
    h.idx = h.stack.length - 1;
  }, []);
  useEffect(() => {
    if (page !== 'editor') return;
    const t = window.setTimeout(commitHistory, 450);
    return () => window.clearTimeout(t);
  }, [graphSnap, page, commitHistory]);
  const applyGraph = (snap: string) => {
    const g = JSON.parse(snap) as { n: Node[]; e: Edge[] };
    setNodes(g.n); setEdges(g.e.map(e => ({ ...e, style: edgeStyle }))); setSelectedNode(null);
  };
  const undo = () => { commitHistory(); const h = hist.current; if (h.idx > 0) { h.idx -= 1; applyGraph(h.stack[h.idx]); } };
  const redo = () => { const h = hist.current; if (h.idx < h.stack.length - 1) { h.idx += 1; applyGraph(h.stack[h.idx]); } };
  const pendingChange = hist.current.idx >= 0 && hist.current.stack[hist.current.idx] !== graphSnap;
  const canUndo = hist.current.idx > 0 || pendingChange;
  const canRedo = hist.current.idx < hist.current.stack.length - 1 && !pendingChange;

  // ---- copy, paste, duplicate, tidy ----------------------------------------------------------
  const copyNodes = (only?: Node[]) => {
    const picked = only ?? (nodes.some(n => n.selected) ? nodes.filter(n => n.selected) : currentNode ? [currentNode] : []);
    if (!picked.length) return false;
    const ids = new Set(picked.map(n => n.id));
    clip.current = {
      nodes: JSON.parse(JSON.stringify(cleanNodes(picked))) as Node[],
      edges: JSON.parse(JSON.stringify(cleanEdges(edges.filter(e => ids.has(e.source) && ids.has(e.target))))) as Edge[],
    };
    return true;
  };
  const pasteNodes = () => {
    const c = clip.current;
    if (!c) return;
    const hasStart = nodes.some(n => startTypes.includes(n.type ?? ''));
    const idMap = new Map<string, string>();
    const fresh: Node[] = [];
    for (const n of c.nodes) {
      if (hasStart && startTypes.includes(n.type ?? '')) continue;
      const id = crypto.randomUUID();
      idMap.set(n.id, id);
      fresh.push({ ...n, id, position: { x: n.position.x + 40, y: n.position.y + 40 }, selected: true, data: { ...n.data } });
    }
    if (!fresh.length) { notify('A workflow can only have one trigger', 'error'); return; }
    const freshEdges: Edge[] = c.edges.filter(e => idMap.has(e.source) && idMap.has(e.target))
      .map(e => ({ ...e, id: crypto.randomUUID(), source: idMap.get(e.source)!, target: idMap.get(e.target)!, style: edgeStyle }));
    setNodes(ns => [...ns.map(n => ({ ...n, selected: false })), ...fresh]);
    setEdges(es => [...es, ...freshEdges]);
    setSelectedNode(fresh[fresh.length - 1].id);
    clip.current = { nodes: c.nodes.map(n => ({ ...n, position: { x: n.position.x + 40, y: n.position.y + 40 } })), edges: c.edges }; // next paste cascades
  };
  const duplicateNode = () => { if (currentNode && copyNodes([currentNode])) pasteNodes(); };
  const tidy = () => {
    if (!nodes.length) return;
    const depth = new Map<string, number>(nodes.map(n => [n.id, 0]));
    for (let pass = 0; pass < nodes.length; pass++) {
      let changed = false;
      for (const e of edges) {
        const d = (depth.get(e.source) ?? 0) + 1;
        if ((depth.get(e.target) ?? 0) < d) { depth.set(e.target, d); changed = true; }
      }
      if (!changed) break;
    }
    const layers = new Map<number, Node[]>();
    for (const n of nodes) { const d = depth.get(n.id) ?? 0; layers.set(d, [...(layers.get(d) ?? []), n]); }
    const tallest = Math.max(...[...layers.values()].map(l => l.length));
    const pos = new Map<string, { x: number; y: number }>();
    layers.forEach((layer, d) => {
      const sorted = [...layer].sort((a, b) => a.position.y - b.position.y);
      const offset = ((tallest - sorted.length) * 130) / 2;
      sorted.forEach((n, i) => pos.set(n.id, { x: 60 + d * 290, y: 60 + offset + i * 130 }));
    });
    setNodes(ns => ns.map(n => ({ ...n, position: pos.get(n.id) ?? n.position })));
    window.setTimeout(() => void flowRef.current?.fitView({ padding: 0.2, maxZoom: 1, duration: 250 }), 80);
    notify('Layout tidied');
  };

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (page !== 'editor') return;
      const mod = ev.ctrlKey || ev.metaKey;
      const key = ev.key.toLowerCase();
      if (mod && key === 's') { ev.preventDefault(); void saveFlow(); return; }
      const t = ev.target as HTMLElement | null;
      if (t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable)) return;
      if (mod && key === 'z' && !ev.shiftKey) { ev.preventDefault(); undo(); }
      else if (mod && (key === 'y' || (key === 'z' && ev.shiftKey))) { ev.preventDefault(); redo(); }
      else if (mod && key === 'c') { if (copyNodes()) notify('Copied. Press Ctrl+V to paste'); }
      else if (mod && key === 'v') { ev.preventDefault(); pasteNodes(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ---- credentials ------------------------------------------------------------------------------
  const refreshProjects = useCallback(async () => {
    try { setProjectList(await api<{ id: string; name: string }[]>('/api/projects')); } catch { /* keep the list */ }
  }, []);
  useEffect(() => { void refreshProjects(); }, [refreshProjects]);
  const assignProject = async (projectId: string) => {
    try { await api(`/api/projects/assign/${selectedId}`, { method: 'PUT', body: JSON.stringify({ project_id: projectId || null }) }); await refresh(); notify(projectId ? 'Moved to the project' : 'Removed from its project'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  const cancelWait = async (id: string) => {
    try { await api(`/api/executions/${id}/cancel`, { method: 'POST' }); await refresh(); notify('Run cancelled'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const result = q.get('oauth');
    if (!result) return;
    window.history.replaceState({}, '', window.location.pathname);
    setPage('credentials');
    if (result === 'connected') { notify('Account connected'); void refresh(); }
    else notify(q.get('reason') || 'Could not connect the account', 'error');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const saveCredential = async (ev: { preventDefault: () => void }) => {
    ev.preventDefault();
    try {
      await api(`/api/credentials/${encodeURIComponent(credName.trim())}`, { method: 'PUT', body: JSON.stringify({ value: credValue }) });
      setCredName(''); setCredValue(''); await refresh(); notify('Credential saved');
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const deleteCredential = async (name: string) => {
    if (!window.confirm(`Delete credential ${name}? Workflows that use it will fail until you add it again.`)) return;
    try { await api(`/api/credentials/${encodeURIComponent(name)}`, { method: 'DELETE' }); await refresh(); notify('Credential deleted'); }
    catch (e) { notify((e as Error).message, 'error'); }
  };

  // The canvas resizes when the run panel or node library opens; re-centre the graph afterwards.
  useEffect(() => {
    if (page !== 'editor') return;
    const t = window.setTimeout(() => void flowRef.current?.fitView({ padding: 0.2, maxZoom: 1, duration: 200 }), 80);
    return () => window.clearTimeout(t);
  }, [page, showCatalog, lastRun?.id]);

  const runStates = useMemo(() => {
    if (!lastRun) return {};
    const done = new Map<string, 'success' | 'error'>();
    for (const s of lastRun.steps) done.set(s.node_id, s.status === 'error' || done.get(s.node_id) === 'error' ? 'error' : 'success');
    return Object.fromEntries(nodes.map(n => [n.id, done.get(n.id) ?? 'skipped'])) as Record<string, 'success' | 'error' | 'skipped'>;
  }, [lastRun, nodes]);

  const startKind = useMemo(() => {
    const starts = nodes.filter(n => startTypes.includes(n.type ?? ''));
    return starts.length === 1 ? starts[0].type : 'manual';
  }, [nodes]);
  const canActivate = startKind === 'schedule' || startKind === 'webhook' || startKind === 'email_in';
  const meta = flows.find(f => f.id === selectedId);
  const isActive = !!meta?.active;
  const lastRunOf = useMemo(() => {
    const m: Record<string, Run> = {};
    for (const r of runs) if (r.workflow_id && !m[r.workflow_id]) m[r.workflow_id] = r;
    return m;
  }, [runs]);
  const allTags = Array.from(new Set(flows.flatMap(f => f.tags ?? []))).sort((a, b) => a.localeCompare(b));
  const filtered = flows.filter(f => (f.name.toLowerCase().includes(query.toLowerCase()) || (f.tags ?? []).some(t => t.toLowerCase().includes(query.toLowerCase()))) && (!tagFilter || (f.tags ?? []).includes(tagFilter)));
  const success = runs.filter(r => r.status === 'success').length;
  const rate = runs.length ? Math.round((100 * success) / runs.length) : 0;
  const shownRuns = runs.filter(r => runFilter === 'all' || (runFilter === 'success' ? r.status === 'success' : r.status !== 'success'));
  const library = catalog.filter(c => `${c.label} ${c.desc} ${c.category}`.toLowerCase().includes(libQuery.toLowerCase()));
  const crumb = { dashboard: 'Overview', editor: 'Workflow Studio', executions: 'Execution History', credentials: 'Credentials', users: 'Users', settings: 'Settings' }[page];
  useEffect(() => { document.title = `${crumb} - FlowPilot`; }, [crumb]);
  const flowName_ = (id?: string) => flows.find(f => f.id === id)?.name || 'Deleted workflow';

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Workflow size={18} strokeWidth={2.4} /></div>
          <span className="brand-name">FlowPilot</span>
        </div>
        <button className="primary side-create" disabled={!canEdit} onClick={() => void createFlow()}><Plus size={16} />Create workflow</button>
        <nav className="nav-group">
          <button className={'nav ' + (page === 'dashboard' ? 'active' : '')} onClick={() => setPage('dashboard')}><LayoutDashboard size={17} />Overview</button>
          <button className={'nav ' + (page === 'editor' ? 'active' : '')} onClick={() => (selectedId ? setPage('editor') : void createFlow())}><Workflow size={17} />Workflow Studio</button>
          <button className={'nav ' + (page === 'executions' ? 'active' : '')} onClick={() => { void refresh(); setPage('executions'); }}><Activity size={17} />Executions<span className="nav-count">{runs.length}</span></button>
          <button className={'nav ' + (page === 'credentials' ? 'active' : '')} onClick={() => { void refresh(); setPage('credentials'); }}><KeyRound size={17} />Credentials<span className="nav-count">{creds.length}</span></button>
          {isOwner && <button className={'nav ' + (page === 'users' ? 'active' : '')} onClick={() => setPage('users')}><UsersIcon size={17} />Users</button>}
          <button className={'nav ' + (page === 'settings' ? 'active' : '')} onClick={() => setPage('settings')}><Settings2 size={17} />Settings</button>
        </nav>
        <div className="sidebar-bottom">
          <span className={'dot ' + (apiOnline ? 'on' : 'off')} />{apiOnline ? 'Local API connected' : 'API offline'}<span className="ver">v0.10</span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="crumb"><span>Personal</span><ChevronRight size={14} /><strong>{crumb}</strong></div>
          <div className="top-actions">
            <button className="icon-button" aria-label="Toggle theme" title="Switch light or dark theme" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}>
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            {!canEdit && <span className="role-chip" title="Your role is read only">View only</span>}
            <div className="avatar" title={`Signed in as ${username} (${role})`}>{username.slice(0, 2).toUpperCase()}</div>
            <button className="ghost signout" onClick={onSignOut}><LogOut size={15} />Sign out</button>
          </div>
        </header>

        {page === 'dashboard' && (
          <div className="content">
            <div className="page-head">
              <div><h1>Workflows</h1><p>Build, run and monitor your automations. Everything stays on this computer.</p></div>
              <div className="page-actions">
                <label className="ghost file-label"><Upload size={15} />Import<input type="file" accept=".json,application/json" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} /></label>
                {canEdit && projectList.length > 0 && <label className="project-pick">In project<select data-testid="new-project" value={newProject} onChange={e => setNewProject(e.target.value)}><option value="">None (visible to everyone)</option>{projectList.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
                <button className="primary" disabled={!canEdit} onClick={() => void createFlow()}><Plus size={16} />Create workflow</button>
              </div>
            </div>

            <section className="stats">
              <Stat label="Workflows" value={flows.length} hint={`${flows.filter(f => f.active).length} active`} icon={<Layers3 size={16} />} tone="violet" />
              <Stat label="Executions" value={runs.length} hint="Stored locally" icon={<Activity size={16} />} tone="blue" />
              <Stat label="Succeeded" value={success} hint="Completed runs" icon={<CheckCircle2 size={16} />} tone="green" />
              <Stat label="Success rate" value={`${rate}%`} hint="Across all runs" icon={<TrendingUp size={16} />} tone="amber" />
            </section>

            <section className="card">
              <div className="card-head">
                <div className="seg-title"><h2>All workflows</h2><span className="count-chip">{flows.length}</span></div>
                <div className="panel-tools">
                  <label className="search"><Search size={15} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search workflows" aria-label="Search workflows" /></label>
                  <button className="icon-button" title="Refresh" aria-label="Refresh" onClick={() => void refresh()}><RefreshCcw size={15} /></button>
                </div>
              </div>
              {allTags.length > 0 && <div className="tag-bar" data-testid="tag-bar"><button className={'tag-chip ' + (!tagFilter ? 'on' : '')} onClick={() => setTagFilter('')}>All</button>{allTags.map(t => <button key={t} className={'tag-chip ' + (tagFilter === t ? 'on' : '')} onClick={() => setTagFilter(tagFilter === t ? '' : t)}>{t}</button>)}</div>}
              {filtered.length > 0 && <div className="table-head"><span>Name</span><span>Trigger</span><span>Status</span><span>Last run</span><span>Updated</span><span className="th-actions">Actions</span></div>}
              <div className="workflow-list">
                {filtered.length ? filtered.map(f => {
                  const last = lastRunOf[f.id];
                  return (
                    <div key={f.id} className="workflow-row" data-testid="workflow-row">
                      <button className="flow-meta" onClick={() => void openFlow(f.id)}>
                        <span className="flow-tile"><Workflow size={17} /></span>
                        <span className="flow-text"><strong>{f.name}</strong><span>{f.description || 'Custom automation'}</span>{(!!f.tags?.length || !!f.project_name) && <span className="tag-row">{f.project_name && <em className="tag-mini project" data-testid="project-chip">{f.project_name}</em>}{(f.tags ?? []).map(t => <em key={t} className="tag-mini">{t}</em>)}</span>}</span>
                      </button>
                      <span className={'trig-chip ' + (f.trigger ?? 'manual')}>{triggerLabel[f.trigger ?? 'manual']}</span>
                      <button role="switch" aria-checked={!!f.active} aria-label={`Activate ${f.name}`} className={'switch-row compact ' + (f.active ? 'on' : '')}
                        disabled={(f.trigger ?? 'manual') === 'manual'} title={(f.trigger ?? 'manual') === 'manual' ? 'Add a Schedule or Webhook trigger to activate' : f.active ? 'Click to deactivate' : 'Click to activate'}
                        onClick={() => void setActive(f.id, !f.active)}><span className="switch"><span /></span>{f.active ? 'Active' : 'Off'}</button>
                      <span className={'run-chip ' + (last ? (last.status === 'success' ? 'ok' : 'fail') : 'none')}>{last ? (last.status === 'success' ? 'Succeeded' : 'Failed') : 'Never run'}</span>
                      <span className="muted">{formatTime(f.updated_at)}</span>
                      <span className="row-actions">
                        <button className="icon-button" title="Run now" aria-label={`Run ${f.name}`} onClick={() => void runFromDashboard(f.id)}><Play size={15} /></button>
                        <button className="icon-button" title="Duplicate" aria-label={`Duplicate ${f.name}`} onClick={() => void duplicateFlow(f.id)}><Copy size={15} /></button>
                        <button className="icon-button danger" title="Delete" aria-label={`Delete ${f.name}`} onClick={() => void removeFlow(f.id)}><Trash2 size={15} /></button>
                      </span>
                    </div>
                  );
                }) : <div className="empty">{flows.length ? 'No workflows match your search.' : 'No workflows yet. Create your first automation.'}</div>}
              </div>
            </section>

            <section>
              <div className="section-label"><h2>Start from a template</h2><p>Ready-made workflows you can run immediately and adapt</p></div>
              <div className="template-grid">
                {templates.map(t => (
                  <button key={t.id} className="template-card" data-testid={`template-${t.id}`} onClick={() => void createFlow(t.name, t.description, t.nodes, t.edges)}>
                    <span className="template-preview"><MiniFlow nodes={t.nodes} edges={t.edges} /></span>
                    <span className="template-body">
                      <span className="tag">{t.tag}</span>
                      <strong>{t.name}</strong>
                      <span className="template-desc">{t.description}</span>
                    </span>
                  </button>
                ))}
              </div>
            </section>

            <div className="info-strip"><ShieldCheck size={16} />Runs on your computer. No account or cloud connection required for local workflows.</div>
          </div>
        )}

        {page === 'editor' && (
          <div className="editor-page">
            <div className="editor-bar">
              <div className="editor-heading">
                <button className="icon-button" aria-label="Back to overview" onClick={() => setPage('dashboard')}><ArrowLeft size={18} /></button>
                <div>
                  <input className="workflow-title" value={flowName} onChange={e => setFlowName(e.target.value)} aria-label="Workflow title" />
                  {isOwner && projectList.length > 0 && <select className="project-select" aria-label="Workflow project" value={meta?.project_id ?? ''} onChange={e => void assignProject(e.target.value)}><option value="">No project</option>{projectList.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}
                  <input className="tags-input" value={tagsText} onChange={e => setTagsText(e.target.value)} aria-label="Workflow tags" placeholder="Add tags, separated by commas" maxLength={200} />
                  <span className="sub">{dirty ? <><span className="dot warn" />Unsaved changes</> : <><span className="dot on" />All changes saved</>}{isActive && meta?.next_run_at && <> · Next run {formatTime(meta.next_run_at)}</>}</span>
                </div>
              </div>
              <div className="editor-actions">
                <button role="switch" aria-checked={isActive} aria-label="Workflow active" className={'switch-row ' + (isActive ? 'on' : '')} disabled={!canActivate || busy || !canEdit}
                  title={canActivate ? 'Run automatically when this workflow is active' : 'Add a Schedule or Webhook trigger to activate'} onClick={() => void setActive(selectedId, !isActive)}>
                  <span className="switch"><span /></span>{isActive ? 'Active' : 'Inactive'}</button>
                <span className="tool-group">
                  <button className="icon-button" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={undo}><Undo2 size={16} /></button>
                  <button className="icon-button" aria-label="Redo" title="Redo (Ctrl+Y)" disabled={!canRedo} onClick={redo}><Redo2 size={16} /></button>
                  <button className="icon-button" aria-label="Tidy up layout" title="Tidy up layout" onClick={tidy}><Network size={16} /></button>
                </span>
                <button className="ghost" onClick={download}><Download size={15} />Export</button>
                <label className="ghost file-label"><Upload size={15} />Import<input type="file" accept=".json,application/json" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} /></label>
                <button className="ghost" aria-label="Version history" title="Version history" onClick={() => setShowHistory(h => !h)}><HistoryIcon size={15} />History</button>
                <button className="ghost" onClick={() => void saveFlow()} disabled={busy || !canEdit} title="Ctrl+S"><Save size={15} />Save</button>
                <button className="primary" onClick={() => void execute()} disabled={busy || !canEdit}><Play size={15} fill="currentColor" />{busy ? 'Running…' : 'Run workflow'}</button>
              </div>
            </div>

            <div className="studio">
              {showHistory && selectedId && <HistoryPanel workflowId={selectedId} canRestore={canEdit} dirty={dirty} notify={notify} onClose={() => setShowHistory(false)} onRestored={() => { setShowHistory(false); void openFlow(selectedId); void refresh(); }} />}
              {showCatalog && (
                <aside className="node-panel">
                  <div className="mini-heading">Node library<button className="icon-button" aria-label="Hide library" onClick={() => setShowCatalog(false)}><X size={15} /></button></div>
                  <label className="search small"><Search size={14} /><input value={libQuery} onChange={e => setLibQuery(e.target.value)} placeholder="Filter nodes" aria-label="Filter nodes" /></label>
                  <div className="catalog-scroll">
                    {library.map(c => {
                      const Icon = icons[c.icon];
                      return (
                        <button className="catalog-entry" key={c.type} data-testid={`add-${c.type}`} onClick={() => addNode(c.type, c.label)} style={{ ['--node-color' as string]: c.color }}>
                          <span className="catalog-icon"><Icon size={16} /></span>
                          <span className="catalog-text"><strong>{c.label}</strong><small>{c.desc}</small></span>
                          <Plus size={14} />
                        </button>
                      );
                    })}
                    {!library.length && <div className="empty small">No matching nodes.</div>}
                  </div>
                </aside>
              )}

              <div className="canvas" data-testid="canvas">
                <RunStateContext.Provider value={runStates}>
                  <ReactFlow nodeTypes={nodeTypes} nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
                    onNodeClick={(_, n) => setSelectedNode(n.id)} onPaneClick={() => setSelectedNode(null)} fitView fitViewOptions={{ padding: 0.2, maxZoom: 1 }} onInit={inst => { flowRef.current = inst; window.setTimeout(() => void inst.fitView({ padding: 0.2, maxZoom: 1 }), 120); }}
                    colorMode={theme} defaultEdgeOptions={{ style: edgeStyle }} proOptions={{ hideAttribution: true }}>
                    <Background gap={20} size={1.6} color="var(--dots)" />
                    <Controls />
                    <MiniMap pannable zoomable bgColor="var(--surface)" maskColor={theme === 'dark' ? 'rgba(20,22,27,.7)' : 'rgba(228,230,235,.7)'} style={{ width: 130, height: 84 }} nodeColor={n => catalog.find(c => c.type === n.type)?.color ?? '#7c83ff'} />
                  </ReactFlow>
                </RunStateContext.Provider>
                {!showCatalog && <button className="canvas-add" onClick={() => setShowCatalog(true)}><Plus size={16} />Add nodes</button>}
                <div className="canvas-label"><span className="dot on" />{nodes.length} nodes · {edges.length} connections</div>
              </div>

              <aside className="inspector">
                <div className="mini-heading">{currentNode ? 'Node settings' : 'Inspector'}<Settings2 size={15} /></div>
                {currentNode ? (
                  <div className="inspect-body" data-testid="inspector">
                    <div className="inspect-type">{currentNode.type}</div>
                    <label>Display name<input value={String(currentNode.data.label || '')} onChange={e => updateData('label', e.target.value)} /></label>
                    <NodeSettings node={currentNode} update={updateData} credentials={creds.filter(c => c.kind !== 'oauth2').map(c => c.name)} oauth={creds.filter(c => c.kind === 'oauth2').map(c => c.name)} flows={flows} currentFlowId={selectedId}
                      webhookUrl={webhookUrl} isActive={isActive} onCopyWebhook={() => void copyWebhook()} onRotateWebhook={() => void rotateWebhook()} />
                    {lastRun && nodeSteps.length > 0 && shownStep && (
                      <div className="data-panel" data-testid="data-panel">
                        <div className="data-head"><span>Last run</span>{nodeSteps.length > 1 && <em className="iter">{nodeSteps.length} runs</em>}{shownStep.pinned && <em className="pin-chip">pinned</em>}</div>
                        {shownStep.error && <p className="error">{shownStep.error}</p>}
                        <div className="data-block"><span className="field-label">Input</span><pre>{pretty(shownStep.input)}</pre></div>
                        {shownStep.output !== undefined && <div className="data-block"><span className="field-label">Output</span><pre>{pretty(shownStep.output)}</pre></div>}
                      </div>
                    )}
                    {pinnableTypes.includes(currentNode.type ?? '') && (isPinned
                      ? <><span className="hint">Pinned: this node uses the saved output below and does not run.</span><button className="ghost" onClick={() => updateData('pin', '')}><PinOff size={14} />Unpin data</button></>
                      : <button className="ghost" disabled={!canPin} title={canPin ? 'Reuse this output without running the node again' : 'Run the workflow first'} onClick={() => updateData('pin', JSON.stringify(shownStep?.output))}><Pin size={14} />Pin last output</button>)}
                    <button className="ghost" onClick={duplicateNode}><Copy size={14} />Duplicate node</button>
                    <button className="delete-node" onClick={deleteNode}><Trash2 size={14} />Delete node</button>
                  </div>
                ) : (
                  <div className="inspector-empty">
                    <CircleHelp size={28} /><h3>Select a node</h3><p>Click a node on the canvas to edit its settings.</p>
                    <label>Workflow description<textarea rows={4} value={description} onChange={e => setDescription(e.target.value)} /></label>
                  </div>
                )}
              </aside>
            </div>

            {lastRun && (
              <div className="run-panel" data-testid="run-panel">
                <div className="run-head">
                  <strong><span className={'dot ' + (lastRun.status === 'success' ? 'on' : lastRun.status === 'waiting' ? 'warn' : 'off')} />{lastRun.status === 'success' ? 'Execution successful' : lastRun.status === 'waiting' ? 'Waiting to continue' : 'Execution failed'}
                    <small>{lastRun.steps.reduce((t, s) => t + (s.duration_ms ?? 0), 0).toFixed(1)} ms total</small></strong>
                  <button className="icon-button" aria-label="Close run panel" onClick={() => setLastRun(null)}><X size={17} /></button>
                </div>
                <div className="run-body">
                  <div className="run-steps">
                    {lastRun.steps.map((s, i) => (
                      <div key={i} className={'run-step ' + s.status}>
                        <span className="step-mark">{s.status === 'success' ? '✓' : s.status === 'waiting' ? '…' : '!'}</span>
                        <span className="step-label">{s.label}{s.iteration && <em className="iter">#{s.iteration.map(k => k + 1).join('.')}</em>}{s.pinned && <em className="pin-chip">pinned</em>}{s.branch && <em className={'branch-tag ' + s.branch}>{s.branch}</em>}</span>
                        <span className="step-ms">{s.duration_ms != null ? `${s.duration_ms} ms` : ''}</span>
                      </div>
                    ))}
                  </div>
                  <pre>{pretty(lastRun.status === 'success' ? lastRun.output : lastRun.status === 'waiting' ? { waiting_until: lastRun.wait?.resume_at, note: 'The run continues by itself. Follow it on the Executions page.' } : { error: lastRun.error, steps: lastRun.steps })}</pre>
                </div>
              </div>
            )}
          </div>
        )}

        {page === 'executions' && (
          <div className="content">
            <div className="section-title">
              <div><div className="eyebrow">Run monitoring</div><h1>Execution history</h1><p>Inspect all workflow runs and troubleshoot errors.</p></div>
              <div className="section-actions">
                <div className="segmented" role="tablist">
                  {(['all', 'success', 'failed'] as const).map(f => <button key={f} role="tab" aria-selected={runFilter === f} className={runFilter === f ? 'on' : ''} onClick={() => setRunFilter(f)}>{f}</button>)}
                </div>
                <button className="ghost" onClick={() => void refresh()}><RefreshCcw size={15} />Refresh</button>
                <button className="ghost danger" onClick={() => void clearRuns()} disabled={!runs.length}><Trash2 size={15} />Clear</button>
              </div>
            </div>
            <div className="panel history">
              {shownRuns.length ? shownRuns.map(r => (
                <details className="history-row" key={r.id} data-testid="history-row">
                  <summary><span className={'dot ' + (r.status === 'success' ? 'on' : r.status === 'waiting' ? 'warn' : 'off')} /><strong>{flowName_(r.workflow_id)}</strong><span className={'trig-chip ' + (r.trigger ?? 'manual')}>{triggerLabel[r.trigger ?? 'manual']}</span><span className="muted">{formatTime(r.started_at)}</span><span className={'status-chip ' + (r.status === 'success' ? 'ok' : r.status === 'waiting' ? 'wait' : 'fail')}>{r.status}</span><ChevronRight size={16} /></summary>
                  <div className="history-details">{r.status === 'waiting' && <p className="wait-note">Resumes {formatTime(r.resume_at)}. <button className="ghost" data-testid="cancel-wait" disabled={!canEdit} onClick={() => void cancelWait(r.id)}>Cancel this run</button></p>}<strong>Output</strong><pre>{pretty(r.output)}</pre><strong>Steps</strong><pre>{pretty(r.steps)}</pre>{r.error && <p className="error">{r.error}</p>}</div>
                </details>
              )) : <div className="empty">{runs.length ? 'No runs match this filter.' : 'No executions yet. Run a workflow to see it here.'}</div>}
            </div>
          </div>
        )}

        {page === 'credentials' && (
          <div className="content">
            <div className="page-head">
              <div><h1>Credentials</h1><p>Save API keys, tokens and passwords once. They are encrypted on this computer and hidden in run results.</p></div>
            </div>
            <section className="card">
              <div className="card-head"><div className="seg-title"><h2>Add or replace a credential</h2></div></div>
              {!canCreds && <p className="hint pad">Your role can use credentials in workflows but cannot add or delete them.</p>}
              <form className="cred-form" onSubmit={e => void saveCredential(e)} aria-disabled={!canCreds} style={canCreds ? undefined : { opacity: 0.5, pointerEvents: "none" }}>
                <label>Name<input data-testid="cred-name" value={credName} onChange={e => setCredName(e.target.value)} placeholder="SLACK_URL" required pattern="[A-Za-z][A-Za-z0-9_\-]{0,39}" title="Start with a letter. Use letters, numbers, _ and - (max 40)" /></label>
                <label>Secret value<input data-testid="cred-value" type="password" autoComplete="off" value={credValue} onChange={e => setCredValue(e.target.value)} placeholder="Paste the token or URL" required minLength={4} /></label>
                <button className="primary" type="submit" data-testid="cred-save"><Plus size={16} />Save credential</button>
              </form>
              <p className="hint pad">Saving a name that already exists replaces its value. Values can never be viewed again, only replaced or deleted.</p>
            </section>
            <OAuthConnect canConnect={canCreds} notify={notify} />
            <section className="card">
              <div className="card-head"><div className="seg-title"><h2>Saved credentials</h2><span className="count-chip">{creds.length}</span></div></div>
              <div className="cred-head"><span>Name</span><span>Use it in a node</span><span>Updated</span><span className="th-actions">Actions</span></div>
              {creds.length ? creds.map(c => (
                <div className="cred-row" key={c.name} data-testid="cred-row">
                  <strong>{c.name}{c.kind === 'oauth2' && <em className="iter"> connected account{c.provider ? ` (${c.provider})` : ''}</em>}</strong>
                  {c.kind === 'oauth2' ? <span className="muted">Choose it in a node</span> : <code>{`{{cred.${c.name}}}`}</code>}
                  <span className="muted">{formatTime(c.updated_at)}</span>
                  <span className="row-actions"><button className="icon-button danger" aria-label={`Delete credential ${c.name}`} title="Delete" disabled={!canCreds} onClick={() => void deleteCredential(c.name)}><Trash2 size={15} /></button></span>
                </div>
              )) : <div className="empty">No credentials yet. Add your first one above.</div>}
            </section>
            <section className="panel help-panel">
              <h2>How to use them</h2>
              <ul>
                <li><b>Anywhere in a node:</b> type <code>{'{{cred.NAME}}'}</code> in a URL, header, body, message or template.</li>
                <li><b>HTTP Request:</b> choose Authentication (Bearer, Basic or an API-key header) and pick the credential.</li>
                <li><b>Send Email:</b> save an SMTP credential like <code>smtp://user:password@smtp.example.com:587?from=you@example.com</code>.</li>
                <li><b>Send Message:</b> save the Slack or Discord webhook URL as a credential.</li>
              </ul>
              <p className="hint">Back up <code>backend/flowpilot.key</code> together with the database. Without that key file the saved values cannot be decrypted.</p>
            </section>
          </div>
        )}

        {page === 'users' && isOwner && <UsersPage notify={notify} me={username} onProjectsChanged={() => void refreshProjects()} />}

        {page === 'settings' && (
          <div className="content">
            <div className="section-title"><div><div className="eyebrow">Environment</div><h1>Settings</h1><p>FlowPilot Community Edition, self-hosted on your computer.</p></div></div>
            <div className="settings-grid">
              <DataSettings role={role} notify={notify} onChanged={() => void refresh()} />
              <div className="panel settings-card"><ShieldCheck size={22} /><h2>Account</h2><p>Signed in as <strong>{username}</strong></p><span>Role: {role}. Sessions last 7 days. Passwords are stored as salted PBKDF2 hashes.</span><ChangePassword notify={notify} /></div>
              <div className="panel settings-card"><Clock3 size={22} /><h2>Schedules and webhooks</h2><p>They run while the API window is open.</p><span>Keep the API terminal running for active workflows. Run one API process only.</span></div>
              <div className="panel settings-card"><Database size={22} /><h2>Local database</h2><p>SQLite file: backend/flowpilot.db</p><span>Data persists between restarts.</span></div>
              <div className="panel settings-card"><ShieldCheck size={22} /><h2>Privacy</h2><p>No analytics or tracking services.</p><span>Only configured HTTP nodes contact outside services.</span></div>
              <div className="panel settings-card"><Code2 size={22} /><h2>Developer API</h2><p>FastAPI docs at localhost:8000/docs</p><span>Designed for single-user local use, not public internet exposure.</span></div>
              <div className="panel settings-card"><Clock3 size={22} /><h2>Coming next</h2><p>OAuth connectors, queues and per-user projects.</p><span>See ROADMAP.md for planned modules.</span></div>
            </div>
          </div>
        )}
      </main>
      {toast && <div className={'toast ' + toast.kind} role="status"><Zap size={15} />{toast.msg}</div>}
    </div>
  );
}

function Stat({ label, value, hint, icon, tone }: { label: string; value: number | string; hint: string; icon: React.ReactNode; tone: string }) {
  return <div className={'stat ' + tone}><div className="stat-top">{label}<span className="stat-icon">{icon}</span></div><strong>{value}</strong><span>{hint}</span></div>;
}
