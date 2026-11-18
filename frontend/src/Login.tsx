import { useEffect, useState, type FormEvent } from 'react';
import { AlertCircle, Eye, EyeOff, GitBranch, Lock, ShieldCheck, Workflow, Zap } from 'lucide-react';

type Props = { mode: 'login' | 'setup'; onDone: (username: string, role: string) => void };

async function post(url: string, body: unknown): Promise<{ username: string; role: string }> {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const d = await res.json();
      if (typeof d.detail === 'string') msg = d.detail;
      else if (Array.isArray(d.detail) && d.detail[0]?.loc) {
        const field = String(d.detail[0].loc.at(-1));
        msg = field === 'password' ? 'Password must be at least 8 characters.' : field === 'username' ? 'Username must be 3 to 40 characters: letters, numbers and . _ @ - only.' : 'Please check the form.';
      }
    } catch { /* keep default */ }
    throw new Error(msg);
  }
  return res.json();
}

export function Login({ mode, onDone }: Props) {
  const setup = mode === 'setup';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [show, setShow] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState(false);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get('sso') === 'error') {
      setError(q.get('reason') || 'Single sign-on did not work. Try again or use your password.');
      window.history.replaceState(null, '', window.location.pathname);
    }
    if (!setup) fetch('/api/auth/status').then(r => r.json()).then(s => setSso(!!s?.sso?.enabled)).catch(() => { /* the button just stays hidden */ });
  }, [setup]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    if (setup && password !== confirm) { setError('The two passwords do not match.'); return; }
    setBusy(true);
    try {
      const res = await post(setup ? '/api/auth/setup' : '/api/auth/login', { username, password });
      onDone(res.username, res.role);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <aside className="auth-aside">
        <div className="brand"><div className="brand-mark"><Workflow size={18} strokeWidth={2.4} /></div><span className="brand-name">FlowPilot</span></div>
        <div className="auth-pitch">
          <h2>Automate the busywork, on your own machine.</h2>
          <ul>
            <li><span><GitBranch size={16} /></span>Build workflows visually with real branching</li>
            <li><span><Zap size={16} /></span>Run them instantly and inspect every step</li>
            <li><span><ShieldCheck size={16} /></span>Your data stays on this computer</li>
          </ul>
        </div>
        <small>FlowPilot Community Edition</small>
      </aside>

      <main className="auth-main">
        <form className="auth-card" onSubmit={e => void submit(e)} aria-labelledby="auth-title">
          <div className="auth-icon"><Lock size={20} /></div>
          <h1 id="auth-title">{setup ? 'Create your account' : 'Welcome back'}</h1>
          <p className="auth-sub">{setup ? 'This is your first visit. Set up the owner account that protects your workflows.' : 'Sign in to open your workspace.'}</p>

          {error && <div className="auth-error" role="alert"><AlertCircle size={16} />{error}</div>}

          <label>Username
            <input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" autoFocus required placeholder="your-name" />
          </label>
          <label>Password
            <span className="pw-wrap">
              <input type={show ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)} autoComplete={setup ? 'new-password' : 'current-password'} required minLength={setup ? 8 : 1} placeholder={setup ? 'At least 8 characters' : 'Your password'} />
              <button type="button" className="pw-toggle" aria-label={show ? 'Hide password' : 'Show password'} onClick={() => setShow(s => !s)}>{show ? <EyeOff size={16} /> : <Eye size={16} />}</button>
            </span>
          </label>
          {setup && (
            <label>Confirm password
              <input type={show ? 'text' : 'password'} value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" required placeholder="Repeat the password" />
            </label>
          )}

          <button className="primary auth-submit" type="submit" disabled={busy}>{busy ? 'Please wait…' : setup ? 'Create account and continue' : 'Sign in'}</button>
          {sso && !setup && <a className="ghost auth-sso" data-testid="sso-button" href="/api/auth/sso/start">Sign in with single sign-on</a>}
          <p className="auth-foot">{setup ? 'There is a single owner account. It cannot be created again later.' : 'Forgot your password? See the README for how to reset it.'}</p>
        </form>
      </main>
    </div>
  );
}
