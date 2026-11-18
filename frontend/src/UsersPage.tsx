import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
import { request, roleHelp, type Role } from './api';
import { ProjectsCard } from './ProjectsCard';

type UserRow = { id: string; username: string; role: Role; created_at: string; auth_source?: string };
type Notify = (msg: string, kind?: 'ok' | 'error') => void;

const when = (d: string) => new Date(d).toLocaleDateString();

export function UsersPage({ notify, me, onProjectsChanged }: { notify: Notify; me: string; onProjectsChanged: () => void }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('editor');

  const load = useCallback(async () => {
    try { setUsers(await request<UserRow[]>('/api/users')); } catch (e) { notify((e as Error).message, 'error'); }
  }, [notify]);
  useEffect(() => { void load(); }, [load]);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await request('/api/users', { method: 'POST', body: JSON.stringify({ username: name.trim(), password, role }) });
      setName(''); setPassword(''); await load(); notify('User created');
    } catch (err) { notify((err as Error).message, 'error'); }
  };
  const changeRole = async (u: UserRow, next: Role) => {
    try { await request(`/api/users/${u.id}`, { method: 'PUT', body: JSON.stringify({ role: next }) }); await load(); notify(`${u.username} is now ${next}`); }
    catch (err) { notify((err as Error).message, 'error'); }
  };
  const resetPassword = async (u: UserRow) => {
    const pw = window.prompt(`New password for ${u.username} (at least 8 characters). They will be signed out.`);
    if (!pw) return;
    try { await request(`/api/users/${u.id}`, { method: 'PUT', body: JSON.stringify({ password: pw }) }); notify('Password changed'); }
    catch (err) { notify((err as Error).message, 'error'); }
  };
  const remove = async (u: UserRow) => {
    if (!window.confirm(`Delete ${u.username}? They will be signed out immediately.`)) return;
    try { await request(`/api/users/${u.id}`, { method: 'DELETE' }); await load(); notify('User deleted'); }
    catch (err) { notify((err as Error).message, 'error'); }
  };

  return (
    <div className="content">
      <div className="page-head">
        <div><h1>Users</h1><p>Give teammates their own sign-in. A role limits what they can change, and projects limit which workflows they can see.</p></div>
      </div>
      <section className="card">
        <div className="card-head"><div className="seg-title"><h2>Add a user</h2></div></div>
        <form className="cred-form" onSubmit={e => void add(e)}>
          <label>Username<input data-testid="user-name" value={name} onChange={e => setName(e.target.value)} required minLength={3} maxLength={40} pattern="[A-Za-z0-9_.@\-]+" title="Letters, numbers and _ . @ -" /></label>
          <label>Password<input data-testid="user-password" type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} required minLength={8} placeholder="At least 8 characters" /></label>
          <label>Role
            <select data-testid="user-role" value={role} onChange={e => setRole(e.target.value as Role)}>
              {(['admin', 'editor', 'viewer'] as Role[]).map(r => <option key={r} value={r}>{r}: {roleHelp[r]}</option>)}
            </select>
          </label>
          <button className="primary" type="submit" data-testid="user-add"><Plus size={16} />Add user</button>
        </form>
      </section>
      <section className="card">
        <div className="card-head"><div className="seg-title"><h2>People</h2><span className="count-chip">{users.length}</span></div></div>
        <div className="cred-head"><span>User</span><span>Role</span><span>Added</span><span className="th-actions">Actions</span></div>
        {users.map(u => (
          <div className="cred-row" key={u.id} data-testid="user-row">
            <strong>{u.username}{u.username === me && <em className="iter"> you</em>}{u.auth_source === 'sso' && <em className="iter sso-chip" data-testid="sso-chip"> single sign-on</em>}</strong>
            {u.role === 'owner'
              ? <span className="muted">owner: {roleHelp.owner}</span>
              : <select aria-label={`Role for ${u.username}`} value={u.role} onChange={e => void changeRole(u, e.target.value as Role)}>{(['admin', 'editor', 'viewer'] as Role[]).map(r => <option key={r} value={r}>{r}</option>)}</select>}
            <span className="muted">{when(u.created_at)}</span>
            <span className="row-actions">
              {u.role !== 'owner' && <>
                {u.auth_source !== 'sso' && <button className="icon-button" aria-label={`Reset password for ${u.username}`} title="Reset password" onClick={() => void resetPassword(u)}><KeyRound size={15} /></button>}
                <button className="icon-button danger" aria-label={`Delete user ${u.username}`} title="Delete" onClick={() => void remove(u)}><Trash2 size={15} /></button>
              </>}
            </span>
          </div>
        ))}
      </section>
      <ProjectsCard users={users} notify={notify} onChanged={onProjectsChanged} />
      <section className="panel help-panel">
        <h2>What each role can do</h2>
        <ul>{(['owner', 'admin', 'editor', 'viewer'] as Role[]).map(r => <li key={r}><b>{r}:</b> {roleHelp[r]}</li>)}</ul>
        <p className="hint">Webhooks and schedules run regardless of who is signed in. The owner account cannot be changed or deleted here.</p>
      </section>
    </div>
  );
}

export function ChangePassword({ notify }: { notify: Notify }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await request('/api/auth/password', { method: 'POST', body: JSON.stringify({ current, new: next }) });
      setCurrent(''); setNext(''); notify('Password changed. Your other sessions were signed out.');
    } catch (err) { notify((err as Error).message, 'error'); }
  };
  return (
    <form className="pw-form" onSubmit={e => void submit(e)}>
      <label>Current password<input data-testid="pw-current" type="password" autoComplete="current-password" value={current} onChange={e => setCurrent(e.target.value)} required /></label>
      <label>New password<input data-testid="pw-new" type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} required minLength={8} /></label>
      <button className="ghost" type="submit" data-testid="pw-save">Change password</button>
    </form>
  );
}
