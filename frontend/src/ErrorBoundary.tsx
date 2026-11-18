import { Component, type ReactNode } from 'react';

type State = { error: Error | null };

/** Shows a friendly recovery screen instead of a blank page if the interface crashes. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State { return { error }; }

  componentDidCatch(error: Error) { console.error('FlowPilot interface error:', error); }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="splash" role="alert">
        <div className="offline-card">
          <h1>Something went wrong</h1>
          <p>The page hit an unexpected error. Your saved workflows are safe. Reload to continue.</p>
          <p><button className="primary" onClick={() => window.location.reload()}>Reload</button></p>
          <details><summary>Technical details</summary><pre>{String(this.state.error?.stack || this.state.error?.message || this.state.error).slice(0, 1500)}</pre></details>
        </div>
      </div>
    );
  }
}
