import { useEffect, useState, type FormEvent } from 'react';
import { Copy, Link2 } from 'lucide-react';
import { request } from './api';

type Info = { redirect_uri: string; providers: Record<string, { scopes: string }> };
type Props = { canConnect: boolean; notify: (msg: string, kind?: 'ok' | 'error') => void };

const providerNames: Record<string, string> = { google: 'Google (Sheets, Gmail, Drive and more)', github: 'GitHub', custom: 'Another OAuth 2.0 service' };

/** Start an OAuth connection with your own app (client id and secret). The browser is sent to the provider and returns to FlowPilot. */
export function OAuthConnect({ canConnect, notify }: Props) {
  const [info, setInfo] = useState<Info | null>(null);
  const [name, setName] = useState('');
  const [provider, setProvider] = useState('google');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [scopes, setScopes] = useState('');
  const [authUrl, setAuthUrl] = useState('');
  const [tokenUrl, setTokenUrl] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { request<Info>('/api/oauth/info').then(setInfo).catch(() => setInfo(null)); }, []);
  const defaultScopes = info?.providers[provider]?.scopes ?? '';

  const connect = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await request<{ auth_url: string }>('/api/oauth/start', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), provider, client_id: clientId.trim(), client_secret: clientSecret.trim(), scopes: scopes.trim(), auth_url: authUrl.trim(), token_url: tokenUrl.trim() }),
      });
      window.location.href = res.auth_url;
    } catch (err) { notify((err as Error).message, 'error'); setBusy(false); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(info?.redirect_uri ?? ''); notify('Redirect URL copied'); } catch { notify('Copy failed. Select the text and copy it.', 'error'); }
  };

  return (
    <section className="card" data-testid="oauth-card">
      <div className="card-head"><div className="seg-title"><h2>Connect an account (OAuth)</h2></div></div>
      {!canConnect && <p className="hint pad">Only an owner or admin can connect accounts.</p>}
      <form className="cred-form oauth-form" onSubmit={e => void connect(e)} aria-disabled={!canConnect} style={canConnect ? undefined : { opacity: 0.5, pointerEvents: 'none' }}>
        <label>Name<input data-testid="oauth-name" value={name} onChange={e => setName(e.target.value)} required pattern="[A-Za-z][A-Za-z0-9_\-]{0,39}" placeholder="GOOGLE" title="Start with a letter. Letters, numbers, _ and - (max 40)" /></label>
        <label>Service
          <select data-testid="oauth-provider" value={provider} onChange={e => { setProvider(e.target.value); setScopes(''); }}>
            {Object.keys(providerNames).map(k => <option key={k} value={k}>{providerNames[k]}</option>)}
          </select>
        </label>
        <label>Client ID<input data-testid="oauth-client-id" value={clientId} onChange={e => setClientId(e.target.value)} required autoComplete="off" /></label>
        <label>Client secret<input data-testid="oauth-client-secret" type="password" value={clientSecret} onChange={e => setClientSecret(e.target.value)} required autoComplete="off" /></label>
        {provider === 'custom' && <>
          <label>Authorization URL<input data-testid="oauth-auth-url" value={authUrl} onChange={e => setAuthUrl(e.target.value)} required placeholder="https://id.example.com/authorize" /></label>
          <label>Token URL<input data-testid="oauth-token-url" value={tokenUrl} onChange={e => setTokenUrl(e.target.value)} required placeholder="https://id.example.com/token" /></label>
        </>}
        <label>Permissions (scopes)<input data-testid="oauth-scopes" value={scopes} onChange={e => setScopes(e.target.value)} placeholder={defaultScopes || 'space separated'} /></label>
        <button className="primary" type="submit" disabled={busy || !canConnect} data-testid="oauth-connect"><Link2 size={16} />{busy ? 'Opening…' : 'Connect'}</button>
      </form>
      <div className="hint pad">
        <p><b>1.</b> Create a free OAuth client with the service (for Google: Cloud Console, APIs and Services, Credentials, OAuth client ID, type Web application, and enable the Sheets or Gmail API).</p>
        <p><b>2.</b> Add this redirect URL to that client:</p>
        <span className="url-box"><code data-testid="oauth-redirect">{info?.redirect_uri ?? '…'}</code><button type="button" className="icon-button" aria-label="Copy redirect URL" onClick={() => void copy()}><Copy size={14} /></button></span>
        <p><b>3.</b> Paste the client ID and secret above and press Connect. You approve access on the provider's own page, then return here. The tokens are stored encrypted and refreshed automatically, and the secret is never shown again. If the app is in Google's testing mode, add yourself as a test user.</p>
      </div>
    </section>
  );
}
