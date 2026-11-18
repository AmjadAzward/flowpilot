import type { Node } from '@xyflow/react';
import { Copy, RefreshCcw } from 'lucide-react';
import { operators, type FlowDoc } from './catalog';

type Props = {
  node: Node;
  update: (field: string, value: string) => void;
  credentials: string[];
  flows: FlowDoc[];
  currentFlowId: string;
  webhookUrl: string;
  isActive: boolean;
  onCopyWebhook: () => void;
  onRotateWebhook: () => void;
};

const cronPresets = [
  { label: 'Every 5 min', value: '*/5 * * * *' },
  { label: 'Hourly', value: '0 * * * *' },
  { label: 'Weekdays 9:00', value: '0 9 * * 1-5' },
  { label: '1st of month', value: '0 0 1 * *' },
];

/** The type-specific settings form for the selected node. */
export function NodeSettings({ node, update, credentials, flows, currentFlowId, webhookUrl, isActive, onCopyWebhook, onRotateWebhook }: Props) {
  const d = node.data as Record<string, unknown>;
  const v = (k: string, fallback = '') => String(d[k] ?? fallback);
  const text = (k: string) => (e: { target: { value: string } }) => update(k, e.target.value);
  const credSelect = (field: string, label: string) => (
    <label>{label}
      <select value={v(field)} onChange={text(field)}>
        <option value="">Choose a credential</option>
        {credentials.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
      {!credentials.length && <span className="hint">None saved yet. Add one on the Credentials page.</span>}
    </label>
  );

  switch (node.type) {
    case 'input':
      return <label>JSON value<textarea rows={7} spellCheck={false} value={v('json')} onChange={text('json')} /></label>;
    case 'transform':
      return <label>Field path (for example user.name)<input value={v('path')} onChange={text('path')} /></label>;
    case 'template':
      return <label>Template<textarea rows={5} spellCheck={false} value={v('template')} onChange={text('template')} /><span className="hint">Use {'{{field}}'} to insert values from the incoming data.</span></label>;
    case 'set':
      return (
        <label>JSON template<textarea rows={7} spellCheck={false} value={v('json')} onChange={text('json')} />
          <span className="hint">Build a new object. Text values are escaped for you, so quotes in the data cannot break the JSON. Example: {'{"who": "{{name}}", "count": {{n}}}'}. {'{{}}'} inserts all incoming data.</span>
        </label>
      );
    case 'filter':
      return (
        <>
          <label>Field to test (empty tests the item itself)<input value={v('path')} onChange={text('path')} /></label>
          <label>Keep items where it<select value={v('op', 'equals')} onChange={text('op')}>{operators.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
          {!['exists', 'not_exists'].includes(v('op', 'equals')) && <label>Compare to<input value={v('equals')} onChange={text('equals')} /></label>}
          <span className="hint">The input must be a list. The output is a shorter list.</span>
        </>
      );
    case 'csv':
      return (
        <>
          <label>Text field (empty uses the input itself)<input value={v('path')} onChange={text('path')} /></label>
          <label>First row is a header<select value={v('header', 'true')} onChange={text('header')}><option value="true">Yes: rows become objects</option><option value="false">No: rows become lists</option></select></label>
          <span className="hint">Up to 10000 rows and 1 MB.</span>
        </>
      );
    case 'loop':
      return (
        <>
          <label>List field (empty uses the input itself)<input value={v('path')} onChange={text('path')} placeholder="rows" /></label>
          <span className="hint">Connect the <b>each</b> handle to the steps that should run once per item, and the <b>done</b> handle to what happens after, which receives the list of results. Up to 100 items. The two paths cannot join.</span>
        </>
      );
    case 'merge':
      return (
        <>
          <label>Mode
            <select value={v('mode', 'append')} onChange={text('mode')}>
              <option value="append">Append: a list of every input</option>
              <option value="combine">Combine: merge objects into one</option>
              <option value="first">First: keep only the first input</option>
            </select>
          </label>
          <span className="hint">Connect several branches into this node. It waits for every branch that is actually running, so a branch that was not taken never blocks it.</span>
        </>
      );
    case 'condition':
      return (
        <>
          <label>Field path<input value={v('path')} onChange={text('path')} /></label>
          <label>Operator<select value={v('op', 'equals')} onChange={text('op')}>{operators.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
          {!['exists', 'not_exists'].includes(v('op', 'equals')) && <label>Compare to<input value={v('equals')} onChange={text('equals')} /></label>}
          <span className="hint">Connect the green <b>true</b> and red <b>false</b> handles to different nodes. Only the matching branch runs.</span>
        </>
      );
    case 'delay':
      return <label>Seconds (0 to 5)<input type="number" min={0} max={5} step={0.5} value={v('seconds', '1')} onChange={text('seconds')} /></label>;
    case 'http': {
      const method = v('method', 'GET');
      const auth = v('auth_type', 'none');
      return (
        <>
          <label>Method<select value={method} onChange={text('method')}>{['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => <option key={m}>{m}</option>)}</select></label>
          <label>Public HTTPS URL<input value={v('url')} onChange={text('url')} /></label>
          <label>Authentication
            <select value={auth} onChange={text('auth_type')}>
              <option value="none">None</option>
              <option value="bearer">Bearer token (from a credential)</option>
              <option value="basic">Basic: credential is user:password</option>
              <option value="header">API key in a custom header</option>
            </select>
          </label>
          {auth !== 'none' && credSelect('auth_credential', 'Credential')}
          {auth === 'header' && <label>Header name<input value={v('auth_header')} onChange={text('auth_header')} placeholder="X-Api-Key" /></label>}
          <label>Headers (JSON object, optional)<textarea rows={3} spellCheck={false} placeholder={'{"Accept": "application/json"}'} value={v('headers')} onChange={text('headers')} /></label>
          {['POST', 'PUT', 'PATCH'].includes(method) && <label>Body (JSON or text)<textarea rows={5} spellCheck={false} placeholder={'{"name": "{{name}}"}'} value={v('body')} onChange={text('body')} /></label>}
          <span className="hint">Use {'{{field}}'} in the URL, headers and body to insert incoming data, {'{{}}'} for all of it, and {'{{cred.NAME}}'} for a saved credential. Private addresses and redirects are blocked.</span>
        </>
      );
    }
    case 'notify':
      return (
        <>
          <label>Service<select value={v('service', 'slack')} onChange={text('service')}><option value="slack">Slack (incoming webhook)</option><option value="discord">Discord (webhook)</option><option value="generic">Other (sends {'{"text": ...}'})</option></select></label>
          <label>Webhook URL<input value={v('url')} onChange={text('url')} placeholder="{{cred.SLACK_URL}}" /></label>
          <label>Message<textarea rows={4} spellCheck={false} value={v('message')} onChange={text('message')} /></label>
          <span className="hint">Webhook URLs are secrets. Save the URL as a credential and enter {'{{cred.NAME}}'} here. The incoming data continues to the next node.</span>
        </>
      );
    case 'email':
      return (
        <>
          {credSelect('credential', 'SMTP credential')}
          <label>To (comma separated)<input value={v('to')} onChange={text('to')} placeholder="ada@example.com" /></label>
          <label>Subject<input value={v('subject')} onChange={text('subject')} /></label>
          <label>Body<textarea rows={5} spellCheck={false} value={v('body')} onChange={text('body')} /></label>
          <span className="hint">Credential format: smtp://user:password@smtp.example.com:587?from=you@example.com. Use smtps:// for SSL. URL-encode special characters in the password (for example @ becomes %40).</span>
        </>
      );
    case 'subflow':
      return (
        <>
          <label>Workflow to run
            <select value={v('workflow_id')} onChange={text('workflow_id')}>
              <option value="">Choose a workflow</option>
              {flows.filter(f => f.id !== currentFlowId).map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
          <span className="hint">Passes the current data in and continues with the other workflow's result. Nesting is limited to a few levels and a workflow cannot call itself.</span>
        </>
      );
    case 'schedule': {
      const mode = v('mode', 'interval');
      return (
        <>
          <label>Repeat
            <select value={mode} onChange={text('mode')}>
              <option value="interval">Every N minutes</option>
              <option value="daily">Every day at a set time</option>
              <option value="cron">Cron expression</option>
            </select>
          </label>
          {mode === 'daily' && <label>Time (24-hour, this computer's time zone)<input type="time" value={v('time', '09:00')} onChange={text('time')} /></label>}
          {mode === 'interval' && <label>Minutes between runs (1 to 10080)<input type="number" min={1} max={10080} value={v('minutes', '15')} onChange={text('minutes')} /></label>}
          {mode === 'cron' && (
            <>
              <label>Cron expression<input value={v('cron', '0 9 * * 1-5')} onChange={text('cron')} spellCheck={false} placeholder="0 9 * * 1-5" /></label>
              <span className="chip-row">{cronPresets.map(p => <button key={p.value} type="button" className="chip" onClick={() => update('cron', p.value)}>{p.label}</button>)}</span>
              <span className="hint">Five fields: minute, hour, day of month, month, day of week (0 or 7 is Sunday). Supports * , - and /. Uses this computer's time zone.</span>
            </>
          )}
          <span className="hint">Runs only while the FlowPilot API is running and this workflow is Active. Use Run workflow to test it with a sample event.</span>
        </>
      );
    }
    case 'webhook':
      return (
        <>
          <div className="field"><span className="field-label">Webhook URL</span>
            <span className="url-box"><code data-testid="webhook-url">{webhookUrl}</code><button type="button" className="icon-button" aria-label="Copy webhook URL" onClick={onCopyWebhook}><Copy size={14} /></button></span>
          </div>
          <span className="hint">Send a POST or GET request to this URL. The URL is the secret, so anyone who has it can run this workflow. {isActive ? 'This workflow is active and listening.' : 'Activate the workflow for the URL to respond.'} It is reachable from this computer only unless you expose it yourself.</span>
          <button type="button" className="ghost" onClick={onRotateWebhook}><RefreshCcw size={14} />Generate new URL</button>
          <label>Test payload (used by Run workflow)<textarea rows={5} spellCheck={false} value={v('sample')} onChange={text('sample')} /></label>
          <span className="hint">Later nodes can read {'{{body.field}}'}, {'{{query.name}}'} and {'{{method}}'}.</span>
        </>
      );
    default:
      return null;
  }
}
