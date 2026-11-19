import { useCallback, useEffect, useState } from 'react';
import { Database, Trash2 } from 'lucide-react';
import { request } from './api';

type TableInfo = { name: string; rows: number; last_added: string; project_id: string | null; project_name: string | null };
type TableData = { name: string; total: number; rows: Record<string, unknown>[] };
type Notify = (msg: string, kind?: 'ok' | 'error') => void;

const cell = (v: unknown) => (v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''));

/** Browse the rows that Data Table nodes have stored. Editors and above can remove rows. */
export function TablesPage({ notify, canEdit }: { notify: Notify; canEdit: boolean }) {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [open, setOpen] = useState<TableInfo | null>(null);
  const [data, setData] = useState<TableData | null>(null);

  const loadList = useCallback(async () => {
    try { setTables(await request<TableInfo[]>('/api/tables')); } catch (e) { notify((e as Error).message, 'error'); }
  }, [notify]);
  const url = (tb: TableInfo, tail = '') => `/api/tables/${encodeURIComponent(tb.name)}${tail}${tb.project_id ? `?project_id=${encodeURIComponent(tb.project_id)}` : ''}`;
  const show = useCallback(async (tb: TableInfo) => {
    setOpen(tb);
    try { setData(await request<TableData>(url(tb))); } catch (e) { setData(null); setOpen(null); notify((e as Error).message, 'error'); }
  }, [notify]);
  useEffect(() => { void loadList(); }, [loadList]);

  const removeRow = async (id: unknown) => {
    if (!open) return;
    try { await request(url(open, `/rows/${String(id)}`), { method: 'DELETE' }); await loadList(); await show(open); }
    catch (e) { notify((e as Error).message, 'error'); await loadList(); await show(open); }
  };
  const clear = async () => {
    if (!open || !window.confirm(`Delete every row in ${open.name}? This cannot be undone.`)) return;
    try { await request(url(open), { method: 'DELETE' }); notify('Table emptied'); setOpen(null); setData(null); await loadList(); }
    catch (e) { notify((e as Error).message, 'error'); }
  };

  const cols = data ? Array.from(new Set(data.rows.flatMap(r => Object.keys(r).filter(k => k !== '_id' && k !== 'added')))).slice(0, 12) : [];
  return (
    <div className="content" data-testid="tables-page">
      <div className="page-head">
        <div><h1>Data tables</h1><p>Rows kept by Data Table nodes. A workflow reads and writes the tables of its own project (workflows without a project share one set of tables). A table appears here once a workflow has added a row to it.</p></div>
      </div>
      <section className="card">
        <div className="card-head"><div className="seg-title"><h2>Tables</h2><span className="count-chip">{tables.length}</span></div></div>
        {tables.length === 0 && <p className="muted pad">No tables yet. Add a Data Table node to a workflow and choose "Add a row".</p>}
        {tables.map(tb => (
          <button key={`${tb.project_id ?? ''}/${tb.name}`} className={'table-pick' + (open?.name === tb.name && open?.project_id === tb.project_id ? ' active' : '')} data-testid="table-pick" onClick={() => void show(tb)}>
            <Database size={15} /><strong>{tb.name}</strong>{tb.project_name && <em className="iter">{tb.project_name}</em>}<span className="muted">{tb.rows} row{tb.rows === 1 ? '' : 's'}</span>
          </button>
        ))}
      </section>
      {data && (
        <section className="card" data-testid="table-rows">
          <div className="card-head">
            <div className="seg-title"><h2>{data.name}</h2><span className="count-chip">{data.total}</span></div>
            {canEdit && <button className="ghost danger" onClick={() => void clear()}><Trash2 size={15} />Empty the table</button>}
          </div>
          {data.total > data.rows.length && <p className="hint pad">Showing the newest {data.rows.length} of {data.total} rows.</p>}
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>ID</th>{cols.map(c => <th key={c}>{c}</th>)}<th>Added</th>{canEdit && <th />}</tr></thead>
              <tbody>
                {data.rows.map(r => (
                  <tr key={String(r._id)} data-testid="table-row">
                    <td>{String(r._id)}</td>{cols.map(c => <td key={c}>{cell(r[c])}</td>)}<td className="muted">{new Date(String(r.added)).toLocaleString()}</td>
                    {canEdit && <td><button className="icon-button danger" aria-label={`Delete row ${String(r._id)}`} onClick={() => void removeRow(r._id)}><Trash2 size={14} /></button></td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
