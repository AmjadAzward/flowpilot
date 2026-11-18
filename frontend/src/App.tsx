import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Controls, MiniMap, addEdge, useNodesState, useEdgesState, type Connection, type Node, type Edge } from '@xyflow/react';
import {
  Activity, ArrowLeft, CheckCircle2, ChevronRight, CircleHelp, Clock3, Code2, Copy, Database, Download, Layers3,
  LayoutDashboard, Moon, Play, Plus, RefreshCcw, Save, Search, Settings2, ShieldCheck, Sparkles, Sun, Trash2, Upload, Workflow, X, Zap, TrendingUp,
} from 'lucide-react';
import { blankEdges, blankNodes, catalog, defaultData, operators, templates, type FlowDoc, type Run } from './catalog';
import { icons, nodeTypes, RunStateContext } from './nodes';

type Page = 'dashboard' | 'editor' | 'executions' | 'settings';
type Theme = 'dark' | 'light';

const api = async <T,>(url: string, options?: RequestInit): Promise<T> => {
  const res = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const detail = await res.json(); msg = typeof detail.detail === 'string' ? detail.detail : msg; } catch { /* keep default */ }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
};

const formatTime = (date?: string) => (date ? new Date(date).toLocaleString() : '—');
const pretty = (obj: unknown) => JSON.stringify(obj, null, 2);
const edgeStyle = { stroke: 'var(--edge)', strokeWidth: 2 };
const cleanNodes = (ns: Node[]) => ns.map(({ id, type, position, data }) => ({ id, type, position, data }));
const cleanEdges = (es: Edge[]) => es.map(({ id, source, target, sourceHandle }) => ({ id, source, target, ...(sourceHandle ? { sourceHandle } : {}) }));

function readTheme(): Theme {
  try { const t = localStorage.getItem('fp-theme'); if (t === 'light' || t === 'dark') return t; } catch { /* storage blocked */ }
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export default function App() {
  const [page, setPage] = useState<Page>('dashboard');
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [flows, setFlows] = useState<FlowDoc[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [flowName, setFlowName] = useState('Untitled workflow');
  const [description, setDescription] = useState('');
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
  const [showCatalog, setShowCatalog] = useState(true);
  const [apiOnline, setApiOnline] = useState(true);
  const [savedSnap, setSavedSnap] = useState('');
  const toastTimer = useRef<number>();

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
      const [f, r] = await Promise.all([api<FlowDoc[]>('/api/workflows'), api<Run[]>('/api/executions')]);
      setFlows(f); setRuns(r); setApiOnline(true);
    } catch (e) { setApiOnline(false); notify(`API offline: ${(e as Error).message}`, 'error'); }
  }, [notify]);
  useEffect(() => { void refresh(); }, [refresh]);

  const snapshot = useMemo(() => JSON.stringify({ flowName, description, n: cleanNodes(nodes), e: cleanEdges(edges) }), [flowName, description, nodes, edges]);
  const dirty = page === 'editor' && !!selectedId && snapshot !== savedSnap;

  const onConnect = useCallback((c: Connection) => setEdges(es => addEdge({ ...c, animated: true, style: edgeStyle }, es)), [setEdges]);

  const loadDoc = (doc: FlowDoc) => {
    const ns = doc.nodes, es = doc.edges;
    setSelectedId(doc.id); setFlowName(doc.name); setDescription(doc.description);
    setNodes(ns); setEdges(es); setSelectedNode(null); setLastRun(null);
    setSavedSnap(JSON.stringify({ flowName: doc.name, description: doc.description, n: cleanNodes(ns), e: cleanEdges(es) }));
    setPage('editor');
  };
  const openFlow = async (id: string) => {
    try { loadDoc(await api<FlowDoc>(`/api/workflows/${id}`)); } catch (e) { notify((e as Error).message, 'error'); }
  };
  const createFlow = async (name = 'Untitled workflow', desc = 'Build something great.', ns: Node[] = blankNodes, es: Edge[] = blankEdges) => {
    try {
      const doc = await api<{ id: string }>('/api/workflows', { method: 'POST', body: JSON.stringify({ name, description: desc, nodes: ns, edges: es }) });
      await refresh(); await openFlow(doc.id); notify('Workflow created');
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const payload = () => JSON.stringify({ name: flowName.trim() || 'Untitled workflow', description, nodes: cleanNodes(nodes), edges: cleanEdges(edges) });
  const saveFlow = useCallback(async (silent = false) => {
    if (!selectedId) return false;
    try {
      await api(`/api/workflows/${selectedId}`, { method: 'PUT', body: payload() });
      setSavedSnap(snapshot); await refresh(); if (!silent) notify('Workflow saved'); return true;
    } catch (e) { notify((e as Error).message, 'error'); return false; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, snapshot, flowName, description, nodes, edges, refresh, notify]);
  const execute = async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      if (!(await saveFlow(true))) return;
      const result = await api<Run>(`/api/workflows/${selectedId}/run`, { method: 'POST' });
      setLastRun(result); await refresh();
      notify(result.status === 'success' ? 'Workflow completed' : 'Workflow failed: see the run panel', result.status === 'success' ? 'ok' : 'error');
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
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

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's' && page === 'editor') { ev.preventDefault(); void saveFlow(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [page, saveFlow]);

  const runStates = useMemo(() => {
    if (!lastRun) return {};
    const done = new Map(lastRun.steps.map(s => [s.node_id, s.status === 'success' ? 'success' : 'error'] as const));
    return Object.fromEntries(nodes.map(n => [n.id, done.get(n.id) ?? 'skipped'])) as Record<string, 'success' | 'error' | 'skipped'>;
  }, [lastRun, nodes]);

  const filtered = flows.filter(f => f.name.toLowerCase().includes(query.toLowerCase()));
  const success = runs.filter(r => r.status === 'success').length;
  const rate = runs.length ? Math.round((100 * success) / runs.length) : 0;
  const shownRuns = runs.filter(r => runFilter === 'all' || (runFilter === 'success' ? r.status === 'success' : r.status !== 'success'));
  const library = catalog.filter(c => `${c.label} ${c.desc} ${c.category}`.toLowerCase().includes(libQuery.toLowerCase()));
  const crumb = { dashboard: 'Overview', editor: 'Workflow Studio', executions: 'Execution History', settings: 'Settings' }[page];
  const flowName_ = (id?: string) => flows.find(f => f.id === id)?.name || 'Deleted workflow';

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Workflow size={20} /></div>
          <div className="brand-text"><span>Flow<em>Pilot</em></span><small>Automation studio</small></div>
        </div>
        <div className="nav-category">Workspace</div>
        <button className={'nav ' + (page === 'dashboard' ? 'active' : '')} onClick={() => setPage('dashboard')}><LayoutDashboard size={17} />Overview</button>
        <button className={'nav ' + (page === 'editor' ? 'active' : '')} onClick={() => (selectedId ? setPage('editor') : void createFlow())}><Workflow size={17} />Workflow Studio</button>
        <button className={'nav ' + (page === 'executions' ? 'active' : '')} onClick={() => { void refresh(); setPage('executions'); }}><Activity size={17} />Executions<span className="nav-count">{runs.length}</span></button>
        <div className="nav-category">System</div>
        <button className={'nav ' + (page === 'settings' ? 'active' : '')} onClick={() => setPage('settings')}><Settings2 size={17} />Settings</button>
        <div className="sidebar-bottom">
          <span className={'dot ' + (apiOnline ? 'on' : 'off')} />{apiOnline ? 'API connected' : 'API offline'}<span className="ver">v0.2</span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="crumb">Workspace <ChevronRight size={14} /><strong>{crumb}</strong></div>
          <div className="top-actions">
            <span className="pill"><span className={'dot ' + (apiOnline ? 'on' : 'off')} />Local mode</span>
            <button className="icon-button" aria-label="Toggle theme" title="Toggle light or dark theme" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}>
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <div className="avatar">FP</div>
          </div>
        </header>

        {page === 'dashboard' && (
          <div className="content">
            <section className="hero">
              <div className="hero-copy">
                <div className="eyebrow"><Sparkles size={13} />Your automation workspace</div>
                <h1>Build smarter. <span className="grad">Automate more.</span></h1>
                <p>Design visual workflows, branch on conditions, call APIs and inspect every run, all on your own machine.</p>
                <div className="hero-actions">
                  <button className="primary" onClick={() => void createFlow()}><Plus size={16} />Create workflow</button>
                  <label className="ghost file-label"><Upload size={15} />Import JSON<input type="file" accept=".json,application/json" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} /></label>
                </div>
              </div>
              <div className="hero-art" aria-hidden><span className="orb o1" /><span className="orb o2" /><Zap size={54} /></div>
            </section>

            <section className="stats">
              <Stat label="Workflows" value={flows.length} hint="Ready for automation" icon={<Layers3 size={17} />} tone="violet" />
              <Stat label="Executions" value={runs.length} hint="Stored locally" icon={<Activity size={17} />} tone="blue" />
              <Stat label="Successful runs" value={success} hint="Completed workflows" icon={<CheckCircle2 size={17} />} tone="green" />
              <Stat label="Success rate" value={`${rate}%`} hint="Across all runs" icon={<TrendingUp size={17} />} tone="amber" />
            </section>

            <section className="panel">
              <div className="panel-heading"><div><h2>Start from a template</h2><p>Pre-built workflows you can run immediately and modify</p></div></div>
              <div className="template-grid">
                {templates.map(t => (
                  <button key={t.id} className="template-card" data-testid={`template-${t.id}`} onClick={() => void createFlow(t.name, t.description, t.nodes, t.edges)}>
                    <span className="tag">{t.tag}</span><strong>{t.name}</strong><span>{t.description}</span>
                    <span className="template-cta">Use template <ChevronRight size={14} /></span>
                  </button>
                ))}
              </div>
            </section>

            <section className="panel">
              <div className="panel-heading">
                <div><h2>My workflows</h2><p>Manage and run your automations</p></div>
                <div className="panel-tools">
                  <label className="search"><Search size={15} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search workflows" aria-label="Search workflows" /></label>
                  <button className="icon-button" title="Refresh" aria-label="Refresh" onClick={() => void refresh()}><RefreshCcw size={15} /></button>
                </div>
              </div>
              <div className="workflow-list">
                {filtered.length ? filtered.map(f => (
                  <div key={f.id} className="workflow-row" data-testid="workflow-row">
                    <div className="flow-tile"><Workflow size={18} /></div>
                    <button className="flow-meta" onClick={() => void openFlow(f.id)}><strong>{f.name}</strong><span>{f.description || 'Custom automation'} · Updated {formatTime(f.updated_at)}</span></button>
                    <button className="icon-button" title="Run now" aria-label={`Run ${f.name}`} onClick={() => void runFromDashboard(f.id)}><Play size={15} /></button>
                    <button className="icon-button" title="Duplicate" aria-label={`Duplicate ${f.name}`} onClick={() => void duplicateFlow(f.id)}><Copy size={15} /></button>
                    <button className="icon-button danger" title="Delete" aria-label={`Delete ${f.name}`} onClick={() => void removeFlow(f.id)}><Trash2 size={15} /></button>
                  </div>
                )) : <div className="empty">{flows.length ? 'No workflows match your search.' : 'No workflows yet. Create your first automation.'}</div>}
              </div>
            </section>

            <div className="info-strip"><ShieldCheck size={17} />Everything runs on your computer. No account or cloud connection is required for local workflows.</div>
          </div>
        )}

        {page === 'editor' && (
          <div className="editor-page">
            <div className="editor-bar">
              <div className="editor-heading">
                <button className="icon-button" aria-label="Back to overview" onClick={() => setPage('dashboard')}><ArrowLeft size={18} /></button>
                <div>
                  <input className="workflow-title" value={flowName} onChange={e => setFlowName(e.target.value)} aria-label="Workflow title" />
                  <span className="sub">{dirty ? <><span className="dot warn" />Unsaved changes</> : <><span className="dot on" />All changes saved</>}</span>
                </div>
              </div>
              <div className="editor-actions">
                <button className="ghost" onClick={download}><Download size={15} />Export</button>
                <label className="ghost file-label"><Upload size={15} />Import<input type="file" accept=".json,application/json" onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} /></label>
                <button className="ghost" onClick={() => void saveFlow()} disabled={busy} title="Ctrl+S"><Save size={15} />Save</button>
                <button className="primary" onClick={() => void execute()} disabled={busy}><Play size={15} fill="currentColor" />{busy ? 'Running…' : 'Run workflow'}</button>
              </div>
            </div>

            <div className="studio">
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
                    onNodeClick={(_, n) => setSelectedNode(n.id)} onPaneClick={() => setSelectedNode(null)} fitView fitViewOptions={{ padding: 0.2, maxZoom: 1 }} onInit={inst => window.setTimeout(() => void inst.fitView({ padding: 0.2, maxZoom: 1 }), 120)}
                    colorMode={theme} defaultEdgeOptions={{ animated: true, style: edgeStyle }} proOptions={{ hideAttribution: true }}>
                    <Background gap={22} size={1.2} />
                    <Controls />
                    <MiniMap pannable zoomable bgColor={theme === 'dark' ? '#121a2e' : '#ffffff'} maskColor={theme === 'dark' ? 'rgba(10,15,28,.7)' : 'rgba(220,226,244,.7)'} style={{ width: 130, height: 84 }} nodeColor={n => catalog.find(c => c.type === n.type)?.color ?? '#7c83ff'} />
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
                    {currentNode.type === 'input' && <label>JSON value<textarea rows={7} spellCheck={false} value={String(currentNode.data.json || '')} onChange={e => updateData('json', e.target.value)} /></label>}
                    {currentNode.type === 'transform' && <label>Field path (for example user.name)<input value={String(currentNode.data.path || '')} onChange={e => updateData('path', e.target.value)} /></label>}
                    {currentNode.type === 'template' && <label>Template<textarea rows={5} spellCheck={false} value={String(currentNode.data.template || '')} onChange={e => updateData('template', e.target.value)} /><p className="hint">Use {'{{field}}'} to insert values from the incoming data.</p></label>}
                    {currentNode.type === 'condition' && (
                      <>
                        <label>Field path<input value={String(currentNode.data.path || '')} onChange={e => updateData('path', e.target.value)} /></label>
                        <label>Operator<select value={String(currentNode.data.op || 'equals')} onChange={e => updateData('op', e.target.value)}>{operators.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
                        {!['exists', 'not_exists'].includes(String(currentNode.data.op)) && <label>Compare to<input value={String(currentNode.data.equals || '')} onChange={e => updateData('equals', e.target.value)} /></label>}
                        <p className="hint">Connect the green <b>true</b> and red <b>false</b> handles to different nodes. Only the matching branch runs.</p>
                      </>
                    )}
                    {currentNode.type === 'delay' && <label>Seconds (0 to 5)<input type="number" min={0} max={5} step={0.5} value={String(currentNode.data.seconds ?? '1')} onChange={e => updateData('seconds', e.target.value)} /></label>}
                    {currentNode.type === 'http' && <label>Public HTTPS URL<input value={String(currentNode.data.url || '')} onChange={e => updateData('url', e.target.value)} /><p className="hint">GET only. Private IPs and redirects are blocked.</p></label>}
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
                  <strong><span className={'dot ' + (lastRun.status === 'success' ? 'on' : 'off')} />{lastRun.status === 'success' ? 'Execution successful' : 'Execution failed'}
                    <small>{lastRun.steps.reduce((t, s) => t + (s.duration_ms ?? 0), 0).toFixed(1)} ms total</small></strong>
                  <button className="icon-button" aria-label="Close run panel" onClick={() => setLastRun(null)}><X size={17} /></button>
                </div>
                <div className="run-body">
                  <div className="run-steps">
                    {lastRun.steps.map((s, i) => (
                      <div key={i} className={'run-step ' + s.status}>
                        <span className="step-mark">{s.status === 'success' ? '✓' : '!'}</span>
                        <span className="step-label">{s.label}{s.branch && <em className={'branch-tag ' + s.branch}>{s.branch}</em>}</span>
                        <span className="step-ms">{s.duration_ms != null ? `${s.duration_ms} ms` : ''}</span>
                      </div>
                    ))}
                  </div>
                  <pre>{pretty(lastRun.status === 'success' ? lastRun.output : { error: lastRun.error, steps: lastRun.steps })}</pre>
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
                  <summary><span className={'dot ' + (r.status === 'success' ? 'on' : 'off')} /><strong>{flowName_(r.workflow_id)}</strong><span className="muted">{formatTime(r.started_at)}</span><span className={'status-chip ' + (r.status === 'success' ? 'ok' : 'fail')}>{r.status}</span><ChevronRight size={16} /></summary>
                  <div className="history-details"><strong>Output</strong><pre>{pretty(r.output)}</pre><strong>Steps</strong><pre>{pretty(r.steps)}</pre>{r.error && <p className="error">{r.error}</p>}</div>
                </details>
              )) : <div className="empty">{runs.length ? 'No runs match this filter.' : 'No executions yet. Run a workflow to see it here.'}</div>}
            </div>
          </div>
        )}

        {page === 'settings' && (
          <div className="content">
            <div className="section-title"><div><div className="eyebrow">Environment</div><h1>Settings</h1><p>FlowPilot Community Edition, self-hosted on your computer.</p></div></div>
            <div className="settings-grid">
              <div className="panel settings-card"><Database size={22} /><h2>Local database</h2><p>SQLite file: backend/flowpilot.db</p><span>Data persists between restarts.</span></div>
              <div className="panel settings-card"><ShieldCheck size={22} /><h2>Privacy</h2><p>No analytics or tracking services.</p><span>Only configured HTTP nodes contact outside services.</span></div>
              <div className="panel settings-card"><Code2 size={22} /><h2>Developer API</h2><p>FastAPI docs at localhost:8000/docs</p><span>Designed for single-user local use, not public internet exposure.</span></div>
              <div className="panel settings-card"><Clock3 size={22} /><h2>Coming next</h2><p>Scheduled triggers, credentials, teams and queues.</p><span>See ROADMAP.md for planned modules.</span></div>
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
