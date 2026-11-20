import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { request } from './api';

/** Describe a workflow in plain words; a model drafts it, FlowPilot checks it, and it opens as an inactive draft. */
export function AiBuilder({ credentials, canEdit, notify, onCreated }: { credentials: string[]; canEdit: boolean; notify: (msg: string, kind?: 'ok' | 'error') => void; onCreated: (id: string) => void }) {
  const [text, setText] = useState('');
  const [cred, setCred] = useState('');
  const [busy, setBusy] = useState(false);
  const chosen = cred || credentials[0] || '';
  const build = async () => {
    setBusy(true);
    try {
      const res = await request<{ id: string; name: string; nodes: number }>('/api/ai/build-workflow', { method: 'POST', body: JSON.stringify({ description: text.trim(), credential: chosen }) });
      notify(`Draft "${res.name}" created with ${res.nodes} steps. Check it, try a Dry run, then activate it.`);
      setText('');
      onCreated(res.id);
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  return (
    <section data-testid="ai-builder">
      <div className="section-label"><h2>Describe a workflow</h2><p>An AI model drafts it from your description. Nothing runs or activates by itself.</p></div>
      <div className="panel" style={{ display: 'grid', gap: 10, padding: 16 }}>
        <label>What should it do?
          <textarea aria-label="Describe the workflow" rows={3} value={text} onChange={e => setText(e.target.value)} disabled={!canEdit || busy} placeholder="When a form is submitted, save the answers, then email the team a summary" maxLength={2000} />
        </label>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end' }}>
          <label>AI credential
            <select aria-label="AI credential for the builder" value={chosen} onChange={e => setCred(e.target.value)} disabled={!canEdit || busy}>
              {credentials.length === 0 && <option value="">None saved yet</option>}
              {credentials.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <button className="primary" type="button" data-testid="ai-build" disabled={!canEdit || busy || text.trim().length < 10 || !chosen} onClick={() => void build()}><Sparkles size={15} />{busy ? 'Drafting…' : 'Draft workflow'}</button>
        </div>
        <span className="hint">Uses an OpenAI-compatible AI credential, for example a free local model through Ollama (see the AI node for the credential format). Credentials are never filled in for you: pick them in each node. Small local models can draft poor workflows, so always review the draft.</span>
      </div>
    </section>
  );
}
