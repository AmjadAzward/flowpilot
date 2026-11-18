import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { FolderPlus, Trash2 } from 'lucide-react';
import { request } from './api';

type Member = { user_id: string; username: string };
type Project = { id: string; name: string; workflows: number; members?: Member[] };
type UserRow = { id: string; username: string; role: string };
type Props = { users: UserRow[]; notify: (msg: string, kind?: 'ok' | 'error') => void; onChanged: () => void };

/** Owner tool: create projects and choose who can see the workflows inside them. */
export function ProjectsCard({ users, notify, onChanged }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState('');

  const load = useCallback(async () => {
    try { setProjects(await request<Project[]>('/api/projects')); } catch (e) { notify((e as Error).message, 'error'); }
  }, [notify]);
  useEffect(() => { void load(); }, [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    try { await request('/api/projects', { method: 'POST', body: JSON.stringify({ name: name.trim() }) }); setName(''); await load(); onChanged(); notify('Project created'); }
    catch (err) { notify((err as Error).message, 'error'); }
  };
  const toggle = async (p: Project, userId: string, on: boolean) => {
    const current = (p.members ?? []).map(m => m.user_id);
    const next = on ? [...current, userId] : current.filter(id => id !== userId);
    try { await request(`/api/projects/${p.id}`, { method: 'PUT', body: JSON.stringify({ user_ids: next }) }); await load(); onChanged(); }
    catch (err) { notify((err as Error).message, 'error'); }
  };
  const remove = async (p: Project) => {
    if (!window.confirm(`Delete the project ${p.name}?`)) return;
    try { await request(`/api/projects/${p.id}`, { method: 'DELETE' }); await load(); onChanged(); notify('Project deleted'); }
    catch (err) { notify((err as Error).message, 'error'); }
  };
  const people = users.filter(u => u.role !== 'owner' && u.role !== 'admin');

  return (
    <section className="card" data-testid="projects-card">
      <div className="card-head"><div className="seg-title"><h2>Projects</h2><span className="count-chip">{projects.length}</span></div></div>
      <form className="cred-form" onSubmit={e => void create(e)}>
        <label>New project<input data-testid="project-name" value={name} onChange={e => setName(e.target.value)} required maxLength={60} placeholder="Marketing" /></label>
        <button className="primary" type="submit" data-testid="project-add"><FolderPlus size={16} />Add project</button>
      </form>
      <p className="hint pad">Workflows in a project are visible only to its members, plus owners and admins. Workflows that are not in any project stay visible to everyone. Assign a workflow to a project from the editor.</p>
      {projects.map(p => (
        <div className="project-row" key={p.id} data-testid="project-row">
          <div className="project-head"><strong>{p.name}</strong><span className="muted">{p.workflows} workflow{p.workflows === 1 ? '' : 's'}</span>
            <button className="icon-button danger" aria-label={`Delete project ${p.name}`} title="Delete" onClick={() => void remove(p)}><Trash2 size={15} /></button></div>
          <div className="member-list">
            {people.length ? people.map(u => (
              <label key={u.id} className="member"><input type="checkbox" checked={(p.members ?? []).some(m => m.user_id === u.id)} onChange={e => void toggle(p, u.id, e.target.checked)} aria-label={`${u.username} in ${p.name}`} />{u.username} <em className="iter">{u.role}</em></label>
            )) : <span className="muted">Add editors or viewers on this page first. Owners and admins always see every project.</span>}
          </div>
        </div>
      ))}
    </section>
  );
}
