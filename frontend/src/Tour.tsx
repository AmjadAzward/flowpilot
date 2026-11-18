import { useEffect, useState } from 'react';

const steps = [
  { title: 'Welcome to FlowPilot', body: 'A workflow is a set of steps drawn on a canvas. Each step is a node, and data flows from one node to the next. Start from a ready-made template on the Overview page, or create an empty workflow.' },
  { title: 'Build it', body: 'Open Add nodes to find triggers, data tools, logic and apps. Drag from the dot on one node to the dot on another to connect them, and click a node to change its settings. Write {{field}} to use data from the step before.' },
  { title: 'Test it', body: 'Press Run workflow to see the input and output of every step. Pin a node to freeze its result while you build the rest. If a run fails, open it on the Executions page and replay it from the failed step.' },
  { title: 'Automate it', body: 'Turn on Active to run a workflow on a schedule, from a webhook URL or when an email arrives. Passwords and API keys belong on the Credentials page, where they are stored encrypted. The Settings page has backups.' },
];

/** A short first-run walkthrough. Shown once, and again from the sidebar. */
export function Tour({ onClose }: { onClose: () => void }) {
  const [i, setI] = useState(0);
  const last = i === steps.length - 1;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="tour-backdrop" role="presentation">
      <div className="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" data-testid="tour">
        <div className="tour-dots" aria-hidden="true">{steps.map((_, k) => <span key={k} className={k === i ? 'on' : ''} />)}</div>
        <p className="tour-count">Step {i + 1} of {steps.length}</p>
        <h2 id="tour-title">{steps[i].title}</h2>
        <p>{steps[i].body}</p>
        <div className="tour-actions">
          <button className="ghost" onClick={onClose}>Skip tour</button>
          <span className="spacer" />
          {i > 0 && <button className="ghost" onClick={() => setI(i - 1)}>Back</button>}
          <button className="primary" autoFocus data-testid="tour-next" onClick={() => (last ? onClose() : setI(i + 1))}>{last ? 'Start building' : 'Next'}</button>
        </div>
      </div>
    </div>
  );
}
