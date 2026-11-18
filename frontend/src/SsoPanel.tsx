import { useCallback, useEffect, useState } from 'react';
import { LogIn } from 'lucide-react';
import { request } from './api';

type Config = { enabled: boolean; issuer: string; client_id: string; allowed_domains: string; default_role: string; has_secret: boolean; ready: boolean; redirect_uri: string };

/** Owner-only settings for OpenID Connect single sign-on (Google, Microsoft Entra, Okta, Keycloak and others). */
export function SsoPanel({ notify }: { notify: (msg: string, kind?: 'ok' | 'error') => void }) {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [form, setForm] = useState({ enabled: false, issuer: '', client_id: '', client_secret: '', allowed_domains: '', default_role: 'viewer' });
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    request<Config>('/api/sso/config').then(c => {
      setCfg(c);
      setForm({ enabled: c.enabled, issuer: c.issuer, client_id: c.client_id, client_secret: '', allowed_domains: c.allowed_domains, default_role: c.default_role });
    }).catch(e => notify((e as Error).message, 'error'));
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      await request('/api/sso/config', { method: 'PUT', body: JSON.stringify({ ...form, client_secret: form.client_secret || null }) });
      notify('Single sign-on settings saved');
      load();
    } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const test = async () => {
    try {
      const r = await request<{ keys: number; authorization_endpoint: string }>('/api/sso/test', { method: 'POST' });
      notify(`Provider reached: ${r.keys} signing key${r.keys === 1 ? '' : 's'} found`);
    } catch (e) { notify((e as Error).message, 'error'); }
  };
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm(f => ({ ...f, [k]: e.target.value }));

  return (
    <div className="panel settings-card sso-card" data-testid="sso-panel">
      <LogIn size={22} />
      <h2>Single sign-on</h2>
      <p>Let people sign in with your company account (any OpenID Connect provider: Google, Microsoft Entra ID, Okta, Keycloak and others). Local passwords keep working, and the owner can always sign in with a password.</p>
      {cfg && <p className="hint">In your provider, create a web application and register this redirect address: <code>{cfg.redirect_uri}</code></p>}
      <div className="sso-form">
        <label>Issuer address<input value={form.issuer} onChange={set('issuer')} spellCheck={false} placeholder="https://accounts.google.com" /></label>
        <label>Client ID<input value={form.client_id} onChange={set('client_id')} spellCheck={false} /></label>
        <label>Client secret<input type="password" value={form.client_secret} onChange={set('client_secret')} autoComplete="new-password" placeholder={cfg?.has_secret ? 'Saved. Leave blank to keep it.' : ''} /></label>
        <label>Allowed email domains<input value={form.allowed_domains} onChange={set('allowed_domains')} spellCheck={false} placeholder="company.com, partner.org" /></label>
        <label>Role for new people<select value={form.default_role} onChange={set('default_role')}><option value="viewer">viewer (read only)</option><option value="editor">editor</option><option value="admin">admin</option></select></label>
        <label className="check"><input type="checkbox" checked={form.enabled} onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} />Turn single sign-on on</label>
      </div>
      <p className="hint">The first time someone signs in, an account is created for their email address with the role above (the address must belong to an allowed domain and be confirmed by the provider). An existing local account is never taken over. Addresses can be at most 40 characters. You can change roles on the Users page.</p>
      <div className="var-form-actions">
        <button className="primary" onClick={() => void save()} disabled={busy}>Save</button>
        <button className="ghost" onClick={() => void test()} disabled={!cfg?.issuer}>Test the provider</button>
      </div>
    </div>
  );
}
