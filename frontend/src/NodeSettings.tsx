import type { Node } from '@xyflow/react';
import { Copy, RefreshCcw } from 'lucide-react';
import { ConnectorFields } from './ConnectorsPanel';
import { appServices, pollApps, eachItemTypes, toolForms, errorTypes, operators, startTypes, type FlowDoc } from './catalog';

type Props = {
  node: Node;
  update: (field: string, value: string) => void;
  credentials: string[];
  oauth: string[];
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

/** The settings form for the selected node, followed by the retry / error-handling section. */
export function NodeSettings(props: Props) {
  const { node, update, flows, currentFlowId } = props;
  const d = node.data as Record<string, unknown>;
  const v = (k: string, fallback = '') => String(d[k] ?? fallback);
  const type = node.type ?? '';
  return (
    <>
      <TypeSettings {...props} />
      {startTypes.includes(type) && (
        <label>If this workflow fails, run
          <select value={v('error_workflow')} onChange={e => update('error_workflow', e.target.value)}>
            <option value="">Nothing</option>
            {flows.filter(f => f.id !== currentFlowId).map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <span className="hint">That workflow receives the workflow name, the error, the failed node and the execution id.</span>
        </label>
      )}
      {eachItemTypes.includes(type) && (
        <label>Input handling<select value={v('each_item', 'false')} onChange={e => update('each_item', e.target.value)}><option value="false">Run once with the whole input</option><option value="true">Run once for each item in a list</option></select>
          <span className="hint">With "each item" the input must be a list. The node runs for every element and the results come out as a list.</span>
        </label>
      )}
      {errorTypes.includes(type) && (
        <div className="err-box">
          <span className="field-label">When this node fails</span>
          <label>Retry<select value={v('retries', '0')} onChange={e => update('retries', e.target.value)}>{[0, 1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n === 0 ? 'Do not retry' : `Up to ${n} more time${n > 1 ? 's' : ''}`}</option>)}</select></label>
          {v('retries', '0') !== '0' && <label>Wait between tries (seconds, max 3)<input type="number" min={0} max={3} step={0.5} value={v('retry_wait', '1')} onChange={e => update('retry_wait', e.target.value)} /></label>}
          <label>After the last try<select value={v('on_error', 'stop')} onChange={e => update('on_error', e.target.value)}><option value="stop">Stop the workflow</option><option value="continue">Continue with the error as data</option></select></label>
        </div>
      )}
    </>
  );
}

function TypeSettings({ node, update, credentials, oauth, flows, currentFlowId, webhookUrl, isActive, onCopyWebhook, onRotateWebhook }: Props) {
  const d = node.data as Record<string, unknown>;
  const v = (k: string, fallback = '') => String(d[k] ?? fallback);
  const text = (k: string) => (e: { target: { value: string } }) => update(k, e.target.value);
  const connSelect = (field: string, label: string) => (
    <label>{label}
      <select value={v(field)} onChange={text(field)}>
        <option value="">Choose a connection</option>
        {oauth.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
      {!oauth.length && <span className="hint">No connected accounts yet. Connect one on the Credentials page.</span>}
    </label>
  );
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
      return <label>Template<textarea rows={5} spellCheck={false} value={v('template')} onChange={text('template')} /><span className="hint">Use {'{{field}}'} to insert values from the incoming data. You can also calculate: {'{{ price * qty }}'}, {'{{ name.upper() }}'}, {'{{ "big" if total > 100 else "small" }}'}. Works in every text field that accepts {'{{ }}'}.</span></label>;
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
    case 'sql':
      return (
        <>
          {credSelect('credential', 'Database credential')}
          <label>SQL query<textarea rows={6} spellCheck={false} value={v('query')} onChange={text('query')} placeholder="SELECT * FROM orders WHERE total > ?" /></label>
          <label>Parameters (JSON list, optional)<input value={v('params')} onChange={text('params')} spellCheck={false} placeholder={'["{{name}}", 5]'} /></label>
          <span className="hint">Put <b>?</b> (SQLite) or <b>%s</b> (Postgres) in the query and the values in Parameters, so data can never change the query itself. Credential format: <b>sqlite:///shop.db</b> (a file in backend/data) or <b>postgresql://user:password@host:5432/database</b>. Returns a list of rows (up to 1000), or the number of rows changed. For Postgres, use a database user with only the rights this workflow needs.</span>
        </>
      );
    case 'rss':
      return (
        <>
          <label>Feed URL (public HTTPS)<input value={v('url')} onChange={text('url')} /></label>
          <label>Items to keep (1 to 50)<input type="number" min={1} max={50} value={v('limit', '10')} onChange={text('limit')} /></label>
          <span className="hint">Reads RSS and Atom. Each item has title, link, published and summary. Pair it with Loop Over Items.</span>
        </>
      );
    case 'callback':
      return (
        <>
          <label>Wait up to<input type="number" min={1} step="any" value={v('seconds', '1')} onChange={text('seconds')} /></label>
          <label>Unit<select value={v('unit', 'days')} onChange={text('unit')}><option value="minutes">minutes</option><option value="hours">hours</option><option value="days">days</option></select></label>
          <label>If nobody answers<select value={v('on_timeout', 'continue')} onChange={text('on_timeout')}><option value="continue">Continue with the decision "timeout"</option><option value="fail">Fail the run</option></select></label>
          <span className="hint">The run pauses here. Put {'{{run.approve_url}}'} and {'{{run.reject_url}}'} in a message before this node and send it (Slack, ntfy, email), or answer from the Executions page. The next nodes receive {'{{decision}}'} (approve, reject, timeout or your own word via ?decision=word), {'{{data}}'} (what came in) and {'{{body}}'} (anything posted to the link). Each link works once. Anyone who has a link can answer, so send it only to people you trust. Limits: 1 minute to 7 days, one pause per run, not inside a loop or sub-workflow.</span>
        </>
      );
    case 'wait':
      return (
        <>
          <label>Wait for<input type="number" min={0} step="any" value={v('seconds', '5')} onChange={text('seconds')} /></label>
          <label>Unit<select value={v('unit', 'seconds')} onChange={text('unit')}><option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option><option value="days">days</option></select></label>
          <span className="hint">Up to 60 seconds holds the run and continues. Longer waits (up to 7 days) pause the run, free the server, and continue by themselves, even after the API restarts, as long as the API is running when the time comes. Not available inside a loop or a sub-workflow, and one long wait per run.</span>
        </>
      );
    case 'sheets': {
      const op = v('operation', 'read');
      return (
        <>
          {connSelect('credential', 'Google connection')}
          <label>Spreadsheet ID<input value={v('spreadsheet_id')} onChange={text('spreadsheet_id')} spellCheck={false} placeholder="the long ID in the sheet's URL" /></label>
          <label>Range<input value={v('range')} onChange={text('range')} placeholder="Sheet1 or Sheet1!A1:D50" /></label>
          <label>Action<select value={op} onChange={text('operation')}><option value="read">Read rows</option><option value="append">Append a row</option></select></label>
          {op === 'read' && <label>First row is a header<select value={v('header', 'true')} onChange={text('header')}><option value="true">Yes: rows become objects</option><option value="false">No: rows become lists</option></select></label>}
          {op === 'append' && <label>Values (JSON)<textarea rows={3} spellCheck={false} value={v('values')} onChange={text('values')} /><span className="hint">One row as a list, or a list of rows. {'{{field}}'} values are escaped for you.</span></label>}
          <span className="hint">Reading returns up to 10000 rows. Pair it with Loop Over Items.</span>
        </>
      );
    }
    case 'gmail':
      return (
        <>
          {connSelect('credential', 'Google connection')}
          <label>To (comma separated)<input value={v('to')} onChange={text('to')} /></label>
          <label>Subject<input value={v('subject')} onChange={text('subject')} /></label>
          <label>Body<textarea rows={5} spellCheck={false} value={v('body')} onChange={text('body')} /></label>
          <span className="hint">Sends from the connected Google account. The connection needs the gmail.send permission. The incoming data continues to the next node.</span>
        </>
      );
    case 'telegram':
      return (
        <>
          {credSelect('credential', 'Bot token credential')}
          <label>Chat ID<input value={v('chat_id')} onChange={text('chat_id')} placeholder="123456789 or @channelname" /></label>
          <label>Message<textarea rows={4} spellCheck={false} value={v('text')} onChange={text('text')} /></label>
          <span className="hint">Create a bot with BotFather, save its token on the Credentials page, and message the bot once so it can reply to you. The incoming data continues to the next node.</span>
        </>
      );
    case 'app': {
      const service = v('service', 'slack');
      const def = appServices[service] ?? appServices.slack;
      const opKey = v('operation', Object.keys(def.operations)[0]);
      const opDef = def.operations[opKey] ?? Object.values(def.operations)[0];
      const serviceOptions = <>{Object.entries(appServices).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}<option value="connector">Custom connector (your own API)</option></>;
      const pickAnyService = (e: React.ChangeEvent<HTMLSelectElement>) => {
        if (e.target.value === 'connector') { update('service', 'connector'); update('connector', ''); update('operation', ''); update('credential', ''); update('fields', '{}'); return; }
        pickService(e);
      };
      const pickService = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const next = appServices[e.target.value];
        const firstOp = Object.keys(next.operations)[0];
        update('service', e.target.value);
        update('operation', firstOp);
        update('fields', next.operations[firstOp].example);
      };
      const pickOp = (e: React.ChangeEvent<HTMLSelectElement>) => { update('operation', e.target.value); update('fields', def.operations[e.target.value].example); };
      if (service === 'connector') {
        return (
          <>
            <label>Service<select value="connector" onChange={pickAnyService}>{serviceOptions}</select></label>
            <ConnectorFields v={v} update={update} credSelect={credSelect} fieldsLabel="Fields (JSON)" />
            <span className="hint">A custom connector is only as tested as you make it: try it with a test account first.</span>
          </>
        );
      }
      return (
        <>
          <label>Service<select value={service} onChange={pickAnyService}>{serviceOptions}</select></label>
          <label>Action<select value={opKey} onChange={pickOp}>{Object.entries(def.operations).map(([k, o]) => <option key={k} value={k}>{o.label}</option>)}</select></label>
          {def.oauth ? connSelect('credential', `${def.label} connection`) : credSelect('credential', `${def.label} credential`)}
          <label>Fields (JSON)<textarea rows={6} spellCheck={false} value={v('fields')} onChange={text('fields')} /><span className="hint">{opDef.hint} {'{{field}}'} values from the previous node are escaped for you.</span></label>
          <span className="hint">Save the credential on the Credentials page: {def.credential}. These services are tested against simulated servers, so try yours with a test account first.</span>
        </>
      );
    }
    case 'github': {
      const op = v('operation', 'create_issue');
      return (
        <>
          {credSelect('credential', 'GitHub token credential')}
          <label>Repository<input value={v('repo')} onChange={text('repo')} placeholder="owner/name" /></label>
          <label>Action<select value={op} onChange={text('operation')}><option value="create_issue">Create an issue</option><option value="list_issues">List issues</option></select></label>
          {op === 'create_issue' && <><label>Title<input value={v('title')} onChange={text('title')} /></label><label>Body<textarea rows={4} spellCheck={false} value={v('body')} onChange={text('body')} /></label></>}
          {op === 'list_issues' && <label>State<select value={v('state', 'open')} onChange={text('state')}><option value="open">Open</option><option value="closed">Closed</option><option value="all">All</option></select></label>}
          <span className="hint">Use a free personal access token with Issues permission on that repository.</span>
        </>
      );
    }
    case 'respond':
      return (
        <>
          <label>Status code (200 to 599)<input type="number" min={200} max={599} value={v('status', '200')} onChange={text('status')} /></label>
          <label>Response body (JSON or text)<textarea rows={5} spellCheck={false} value={v('body')} onChange={text('body')} /></label>
          <span className="hint">When this workflow is called through its webhook, the caller receives this reply instead of the default status. Use {'{{body.field}}'} to include data. Needs a Webhook trigger and an active workflow.</span>
        </>
      );
    case 'poll_in': {
      const source = v('source', 'rss');
      return (
        <>
          <label>Watch<select value={source} onChange={e => {
            update('source', e.target.value);
            if (e.target.value === 'app') { update('service', 'stripe'); update('operation', 'list_customers'); update('fields', appServices.stripe.operations.list_customers.example); update('credential', ''); }
          }}><option value="rss">An RSS or Atom feed</option><option value="github_issues">GitHub issues</option><option value="json_list">Any JSON list from an API</option><option value="app">An app (Stripe, Airtable, Shopify...)</option><option value="folder">A folder of files</option></select></label>
          {source === 'app' && (() => {
            const custom = v('service') === 'connector';
            const service = custom ? 'stripe' : pollApps[v('service', 'stripe')] ? v('service', 'stripe') : 'stripe';
            const def = appServices[service];
            const ops = pollApps[service];
            const opKey = ops.includes(v('operation')) ? v('operation') : ops[0];
            const pickService = (e: React.ChangeEvent<HTMLSelectElement>) => { if (e.target.value === 'connector') { update('service', 'connector'); update('connector', ''); update('operation', ''); update('fields', '{}'); update('credential', ''); return; } const first = pollApps[e.target.value][0]; update('service', e.target.value); update('operation', first); update('fields', appServices[e.target.value].operations[first].example); update('credential', ''); };
            const pickOp = (e: React.ChangeEvent<HTMLSelectElement>) => { update('operation', e.target.value); update('fields', def.operations[e.target.value].example); };
            const serviceSelect = <label>Service<select value={custom ? 'connector' : service} onChange={pickService}>{Object.keys(pollApps).map(k => <option key={k} value={k}>{appServices[k].label}</option>)}<option value="connector">Custom connector (your own API)</option></select></label>;
            if (custom) return (
              <>
                {serviceSelect}
                <ConnectorFields v={v} update={update} credSelect={credSelect} fieldsLabel="Fields (JSON)" getOnly />
                <label>Where the list is (blank when the reply is the list; the connector's own result path applies first)<input value={v('items_path')} onChange={text('items_path')} placeholder="items" /></label>
                <label>Field that identifies an item (blank tries id, _id, number)<input value={v('id_path')} onChange={text('id_path')} placeholder="id" /></label>
                <label>The API lists items<select value={v('order', 'newest_first')} onChange={text('order')}><option value="newest_first">Newest first</option><option value="oldest_first">Oldest first</option></select></label>
              </>
            );
            return (
              <>
                {serviceSelect}
                <label>List to watch<select value={opKey} onChange={pickOp}>{ops.map(k => <option key={k} value={k}>{def.operations[k].label}</option>)}</select></label>
                {def.oauth ? connSelect('credential', `${def.label} connection`) : credSelect('credential', `${def.label} credential`)}
                <label>Fields (JSON)<textarea rows={4} spellCheck={false} value={v('fields')} onChange={text('fields')} /><span className="hint">{def.operations[opKey].hint}</span></label>
                <label>Field that identifies an item (blank tries id, _id, number)<input value={v('id_path')} onChange={text('id_path')} placeholder="id" /></label>
                <label>The service lists items<select value={v('order', 'newest_first')} onChange={text('order')}><option value="newest_first">Newest first</option><option value="oldest_first">Oldest first</option></select></label>
              </>
            );
          })()}
          {source === 'folder' && <>
            <label>Folder (inside the files folder, blank for the folder itself)<input value={v('folder')} onChange={text('folder')} placeholder="incoming/invoices" spellCheck={false} /></label>
            <label>File names to watch<input value={v('pattern', '*')} onChange={text('pattern')} placeholder="*.csv" spellCheck={false} /></label>
            <span className="hint">Each new file, or a file whose size or modified time changed, starts one run with {'{{name}}'}, {'{{path}}'}, {'{{size}}'} and {'{{modified}}'}. The Files node can read the path. Only the files folder is watched.</span>
          </>}
          {source === 'github_issues'
            ? <>
                <label>Repository<input value={v('repo')} onChange={text('repo')} placeholder="owner/name" /></label>
                <label>Which issues<select value={v('state', 'open')} onChange={text('state')}><option value="open">Open</option><option value="closed">Closed</option><option value="all">All</option></select></label>
              </>
            : source !== 'app' && source !== 'folder' && <label>Address (HTTPS)<input value={v('url')} onChange={text('url')} spellCheck={false} placeholder="https://example.com/feed.xml" /></label>}
          {source === 'json_list' && <>
            <label>Where the list is (blank when the reply is the list)<input value={v('items_path')} onChange={text('items_path')} placeholder="data.items" /></label>
            <label>Field that identifies an item (blank uses the whole item)<input value={v('id_path')} onChange={text('id_path')} placeholder="id" /></label>
            <label>The API lists items<select value={v('order', 'newest_first')} onChange={text('order')}><option value="newest_first">Newest first</option><option value="oldest_first">Oldest first</option></select></label>
          </>}
          {source !== 'rss' && source !== 'app' && source !== 'folder' && credSelect('credential', 'Access token (optional)')}
          <label>Check every (minutes, 1 to 1440)<input type="number" min={1} max={1440} value={v('minutes', '15')} onChange={text('minutes')} /></label>
          <span className="hint">When you activate the workflow, what exists now is remembered and does not run. After that, each new item starts one run (up to 10 per check). Later nodes read the item's fields, for example {'{{title}}'} and {'{{link}}'} for a feed, or {'{{number}}'}, {'{{title}}'} and {'{{url}}'} for GitHub issues. Run workflow tests it with a sample item.</span>
        </>
      );
    }
    case 'email_in':
      return (
        <>
          {credSelect('credential', 'IMAP credential')}
          <label>Check every (minutes, 1 to 1440)<input type="number" min={1} max={1440} value={v('minutes', '5')} onChange={text('minutes')} /></label>
          <label>Only from (contains, optional)<input value={v('from_filter')} onChange={text('from_filter')} placeholder="billing@" /></label>
          <label>Subject contains (optional)<input value={v('subject_filter')} onChange={text('subject_filter')} placeholder="Invoice" /></label>
          <span className="hint">Runs the workflow once for each new unread message (up to 10 per check) and marks it read. Later nodes read {'{{subject}}'}, {'{{from}}'}, {'{{text}}'}, {'{{date}}'} and {'{{attachments}}'}. Credential format: imaps://user:password@imap.example.com:993 (URL-encode special characters; Gmail needs an app password). Activate the workflow to start checking. Messages that fail are not retried, but the failed run keeps the message.</span>
        </>
      );
    case 'repeat':
      return (
        <>
          <label>Stop when this field<input value={v('path')} onChange={text('path')} placeholder="status (empty tests the whole value)" /></label>
          <label>Is<select value={v('op', 'equals')} onChange={text('op')}>{operators.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
          {!['exists', 'not_exists'].includes(v('op', 'equals')) && <label>Compare to<input value={v('equals')} onChange={text('equals')} /></label>}
          <label>At most (1 to 20 attempts)<input type="number" min={1} max={20} value={v('max', '5')} onChange={text('max')} /></label>
          <label>Pause between attempts (seconds, up to 3)<input type="number" min={0} max={3} step={0.5} value={v('pause', '1')} onChange={text('pause')} /></label>
          <label>If it never succeeds<select value={v('fail_if_not_met', 'false')} onChange={text('fail_if_not_met')}><option value="false">Continue with the last result</option><option value="true">Fail the workflow</option></select></label>
          <span className="hint">Connect the <b>each</b> handle to the steps to repeat. Each attempt receives the previous attempt's result. When the condition is met (or the limit is reached) the <b>done</b> handle continues with the last result. Use it to poll an API until a job finishes.</span>
        </>
      );
    case 'file': {
      const op = v('operation', 'read');
      return (
        <>
          <label>Action<select value={op} onChange={text('operation')}><option value="read">Read a file</option><option value="write">Write a file</option><option value="list">List files</option><option value="delete">Delete a file</option></select></label>
          {op !== 'list' && <label>Path<input value={v('path')} onChange={text('path')} placeholder="reports/{{name}}.txt" /></label>}
          {op === 'read' && <label>Read as<select value={v('as', 'text')} onChange={text('as')}><option value="text">Text</option><option value="base64">Base64 (binary files)</option></select></label>}
          {op === 'write' && <>
            <label>Content<textarea rows={4} spellCheck={false} value={v('content')} onChange={text('content')} /></label>
            <label>Content is<select value={v('encoding', 'text')} onChange={text('encoding')}><option value="text">Text</option><option value="base64">Base64 (binary)</option></select></label>
            <label>If the file exists<select value={v('mode', 'overwrite')} onChange={text('mode')}><option value="overwrite">Replace it</option><option value="append">Add to the end</option></select></label>
          </>}
          <span className="hint">Files live in <b>backend/data/files</b> and cannot leave that folder. Up to 5 MB each. Pair Read (base64) with an HTTP upload, or use the HTTP node's "Save response as file" to download.</span>
        </>
      );
    }
    case 'sticky':
      return <label>Note<textarea rows={6} value={v('text')} onChange={text('text')} /><span className="hint">A note for you and your team. It is not connected to anything and never runs.</span></label>;
    case 'agent': {
      const picked = (k: string) => v(k).split(',').map(x => x.trim()).filter(Boolean);
      const toggle = (k: string, item: string) => { const cur = picked(k); update(k, (cur.includes(item) ? cur.filter(x => x !== item) : [...cur, item]).join(',')); };
      const tools = [['calculator', 'Calculator'], ['current_datetime', 'Current date and time'], ['fetch_url', 'Read a web page (public HTTPS)']] as const;
      const others = flows.filter(f => f.id !== currentFlowId);
      return (
        <>
          {credSelect('credential', 'AI credential')}
          <label>Instructions (optional)<textarea rows={2} spellCheck={false} value={v('system')} onChange={text('system')} /></label>
          <label>Task<textarea rows={4} spellCheck={false} value={v('prompt')} onChange={text('prompt')} /></label>
          <fieldset className="check-group"><legend>Tools it may use</legend>
            {tools.map(([id, label]) => <label key={id} className="check"><input type="checkbox" checked={picked('tools').includes(id)} onChange={() => toggle('tools', id)} />{label}</label>)}
          </fieldset>
          <fieldset className="check-group"><legend>Workflows it may run (up to 5)</legend>
            {others.length ? others.map(f => <label key={f.id} className="check"><input type="checkbox" checked={picked('workflows').includes(f.id)} onChange={() => toggle('workflows', f.id)} />{f.name}</label>) : <span className="hint">No other workflows yet.</span>}
          </fieldset>
          <label>Most tool rounds (1 to 6)<input type="number" min={1} max={6} value={v('max_steps', '4')} onChange={text('max_steps')} /></label>
          <span className="hint">The model decides which tools to call, then answers. The result has the answer and a list of the tool calls it made. It needs a model that supports tool calling (many Ollama models do, for example llama3.1). Use the same credential format as the AI Prompt node. Careful: a web page can contain instructions that try to steer the model, so avoid giving an agent both a web page tool and workflows that send messages or change data.</span>
        </>
      );
    }
    case 'ai': {
      const task = v('task', 'prompt');
      return (
        <>
          {credSelect('credential', 'AI credential')}
          <label>What should the AI do?
            <select value={task} onChange={text('task')}>
              <option value="prompt">Answer a prompt</option>
              <option value="extract">Extract fields from text</option>
              <option value="classify">Sort text into categories</option>
              <option value="sentiment">Judge sentiment (positive, neutral, negative)</option>
              <option value="summarize">Summarize text</option>
            </select>
          </label>
          {task === 'prompt' && <label>System instruction (optional)<textarea rows={2} spellCheck={false} value={v('system')} onChange={text('system')} placeholder="You are a helpful assistant. Answer briefly." /></label>}
          <label>{task === 'prompt' ? 'Prompt' : 'Text to work on'}<textarea rows={task === 'prompt' ? 5 : 3} spellCheck={false} value={v('prompt')} onChange={text('prompt')} placeholder={task === 'prompt' ? '' : '{{body.message}}'} /></label>
          {task === 'extract' && <label>Fields to extract (JSON)<textarea rows={5} spellCheck={false} value={v('schema')} onChange={text('schema')} placeholder={'{"name": "the sender full name", "amount": {"type": "number", "description": "total in dollars"}}'} /><span className="hint">Types: text, number, integer, true_false, list. Anything the text does not say comes back as null. The result has exactly these fields.</span></label>}
          {task === 'classify' && <label>Categories (comma separated)<input value={v('categories')} onChange={text('categories')} placeholder="Billing, Support, Sales" /><span className="hint">The result is {'{category, confidence}'}. Follow it with a Switch or Condition on category.</span></label>}
          {task === 'summarize' && <label>Length<select value={v('length', 'short')} onChange={text('length')}><option value="short">One or two sentences</option><option value="medium">A short paragraph</option><option value="long">Several paragraphs</option></select></label>}
          <label>Creativity (0 to 2)<input type="number" min={0} max={2} step={0.1} value={v('temperature', '0.2')} onChange={text('temperature')} /></label>
          {task === 'prompt' && <label>Reply as<select value={v('json_output', 'false')} onChange={text('json_output')}><option value="false">Text: {'{"text": ...}'}</option><option value="true">JSON: the reply becomes data</option></select></label>}
          <span className="hint">Works with any OpenAI-compatible service. Free and private on your own computer with Ollama or LM Studio. Save the credential as <b>http://127.0.0.1:11434/v1?model=llama3.2</b> (start the API with FLOWPILOT_ALLOW_PRIVATE_HOSTS=1 for local models), or as <b>https://host/v1?model=NAME&amp;key=TOKEN</b> for a hosted one. Use {'{{field}}'} in the text. Small local models can get structured answers wrong, so check the results. Local models can take up to 90 seconds.</span>
        </>
      );
    }
    case 'switch':
      return (
        <>
          <label>Field path<input value={v('path')} onChange={text('path')} /></label>
          {['1', '2', '3', '4'].map(i => <label key={i}>Path {i} when it equals<input value={v('case' + i)} onChange={text('case' + i)} placeholder="leave empty to disable" /></label>)}
          <span className="hint">The first matching path runs. If none match, the <b>default</b> path runs.</span>
        </>
      );
    case 'code':
      return (
        <>
          <label>Python code<textarea rows={9} spellCheck={false} value={v('code')} onChange={text('code')} /></label>
          <span className="hint">Read the incoming data from <b>input</b> and put your answer in <b>result</b>. Provided: json, re, math, datetime and basic functions (len, sum, sorted, and so on). No imports, no files, no network, 5 second limit. Runs in a separate restricted process, so use it for your own workflows only.</span>
        </>
      );
    case 'fail':
      return <label>Error message<input value={v('message')} onChange={text('message')} /><span className="hint">Ends the run as failed. Use {'{{field}}'} to include data. Useful after a Switch path that should never happen, or to test error workflows.</span></label>;
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
              <option value="oauth2">Connected account (OAuth)</option>
            </select>
          </label>
          {auth === 'oauth2' && connSelect('auth_credential', 'Connected account')}
          {auth !== 'none' && auth !== 'oauth2' && credSelect('auth_credential', 'Credential')}
          {auth === 'header' && <label>Header name<input value={v('auth_header')} onChange={text('auth_header')} placeholder="X-Api-Key" /></label>}
          {method === 'GET' && <label>Save response as a file (optional)<input value={v('save_as')} onChange={text('save_as')} placeholder="downloads/{{name}}.pdf" /><span className="hint">Saves the raw response (up to 5 MB) to backend/data/files and returns its path instead of the content.</span></label>}
          <label>Headers (JSON object, optional)<textarea rows={3} spellCheck={false} placeholder={'{"Accept": "application/json"}'} value={v('headers')} onChange={text('headers')} /></label>
          <label>Query parameters (JSON object, optional)<textarea rows={2} spellCheck={false} placeholder={'{"q": "{{name}}", "limit": 50}'} value={v('query')} onChange={text('query')} /></label>
          {['POST', 'PUT', 'PATCH'].includes(method) && <label>Body (JSON or text)<textarea rows={5} spellCheck={false} placeholder={'{"name": "{{name}}"}'} value={v('body')} onChange={text('body')} /></label>}
          {['POST', 'PUT', 'PATCH'].includes(method) && <label>Send the body as<select value={v('body_type', 'auto')} onChange={text('body_type')}><option value="auto">JSON (or plain text if it is not JSON)</option><option value="form">Form fields (urlencoded) from a JSON object</option></select></label>}
          <label>Timeout (seconds, 1 to 60)<input type="number" min={1} max={60} value={v('timeout', '8')} onChange={text('timeout')} /></label>
          {method === 'GET' && !v('save_as') && <>
            <label>Read several pages<select value={v('paginate', 'none')} onChange={text('paginate')}><option value="none">No, one request</option><option value="next_url">Follow a "next" address in each reply</option><option value="page">Count pages with a parameter</option></select></label>
            {v('paginate', 'none') !== 'none' && <>
              <label>Where the items are in each reply (blank when the reply is the list)<input value={v('items_path')} onChange={text('items_path')} placeholder="data.items" /></label>
              {v('paginate') === 'next_url' && <label>Where the next address is<input value={v('next_path')} onChange={text('next_path')} placeholder="links.next" /></label>}
              {v('paginate') === 'page' && <><label>Page parameter name<input value={v('page_param', 'page')} onChange={text('page_param')} /></label><label>First page number<input type="number" value={v('first_page', '1')} onChange={text('first_page')} /></label></>}
              <label>Most pages (1 to 20)<input type="number" min={1} max={20} value={v('max_pages', '5')} onChange={text('max_pages')} /></label>
              <span className="hint">The result is one list of every item. Following stays on the same site, and stops at 5000 items.</span>
            </>}
          </>}
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
          {mode === 'daily' && <label>Time (24-hour, in the time zone below)<input type="time" value={v('time', '09:00')} onChange={text('time')} /></label>}
          {mode === 'interval' && <label>Minutes between runs (1 to 10080)<input type="number" min={1} max={10080} value={v('minutes', '15')} onChange={text('minutes')} /></label>}
          {mode === 'cron' && (
            <>
              <label>Cron expression<input value={v('cron', '0 9 * * 1-5')} onChange={text('cron')} spellCheck={false} placeholder="0 9 * * 1-5" /></label>
              <span className="chip-row">{cronPresets.map(p => <button key={p.value} type="button" className="chip" onClick={() => update('cron', p.value)}>{p.label}</button>)}</span>
              <span className="hint">Five fields: minute, hour, day of month, month, day of week (0 or 7 is Sunday). Supports * , - and /. Uses the time zone below.</span>
            </>
          )}
          {mode !== 'interval' && <label>Time zone (blank is this computer's)<input value={v('timezone')} onChange={text('timezone')} spellCheck={false} placeholder="Europe/Paris" list="tz-list" /></label>}
          <datalist id="tz-list">{['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Sao_Paulo', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Africa/Cairo', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland'].map(z => <option key={z} value={z} />)}</datalist>
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
          <label>Public form fields (optional JSON)<textarea rows={4} spellCheck={false} value={v('form_fields')} onChange={text('form_fields')} placeholder={'[{"name":"email","label":"Your email","type":"email"}]'} /></label>
          <span className="hint">Fill this in and opening the webhook URL in a browser shows a small form (types: text, email, number, textarea, file). Anyone with the URL can submit it. Values arrive as {'{{body.email}}'}.</span>
          <label>Who may call this webhook<select value={v('auth_type', 'none')} onChange={text('auth_type')}><option value="none">Anyone who knows the address</option><option value="header">Callers must send a secret in a header</option><option value="basic">Callers must use Basic authentication (user:password)</option><option value="hmac">Callers must sign the request (HMAC-SHA256)</option></select></label>
          {v('auth_type', 'none') === 'hmac' && <label>Signature header<input value={v('auth_header', 'X-Signature-256')} onChange={text('auth_header')} placeholder="X-Signature-256" /></label>}
          {v('auth_type', 'none') === 'hmac' && <label>Timestamp header (optional, blocks replays)<input value={v('auth_timestamp_header')} onChange={text('auth_timestamp_header')} placeholder="X-Timestamp" /></label>}
          {v('auth_type', 'none') === 'hmac' && <span className="hint">The caller sends the hex HMAC-SHA256 of the request body (or of timestamp.body when a timestamp header is set; the timestamp is in seconds and must be within five minutes), keyed with the credential's value. Signed calls do not show a public form.</span>}
          {v('auth_type', 'none') === 'header' && <label>Header name<input value={v('auth_header', 'X-Webhook-Token')} onChange={text('auth_header')} placeholder="X-Webhook-Token" /></label>}
          {v('auth_type', 'none') !== 'none' && credSelect('auth_credential', v('auth_type') === 'basic' ? 'Credential holding user:password' : 'Credential holding the secret')}
          {v('auth_type', 'none') !== 'none' && <span className="hint">Calls without the right secret get a 401 and do not run the workflow. Browsers cannot send a header, so a public form does not work together with this. The Run button is not affected.</span>}
          <label>Repeated requests<select value={v('idempotency', 'off')} onChange={text('idempotency')}><option value="off">Run every request</option><option value="on">Ignore a request whose Idempotency-Key header was already seen in the last day</option></select></label>
          <label>Accept file uploads<select value={v('accept_files', 'false')} onChange={text('accept_files')}><option value="false">No (a form with a file field still accepts files)</option><option value="true">Yes: multipart uploads from any sender</option></select></label>
          <span className="hint">Uploads are limited to 5 files, 5 MB each and 10 MB in total, and are stored in the files folder under uploads/. They arrive as {'{{body.files.0.filename}}'}, {'{{body.files.0.size}}'} and {'{{body.files.0.path}}'}; the Files node can read the path. Old uploads are deleted with the run-history cleanup. Anyone with the URL can upload, so keep the address private.</span>
          <label>Test payload (used by Run workflow)<textarea rows={5} spellCheck={false} value={v('sample')} onChange={text('sample')} /></label>
          <span className="hint">Later nodes can read {'{{body.field}}'}, {'{{query.name}}'} and {'{{method}}'}.</span>
        </>
      );
    default: {
      const form = toolForms[node.type ?? ''];
      if (!form) return null;
      return (
        <>
          {form.fields.filter(f => !f.when || Object.entries(f.when).every(([k, vals]) => vals.includes(v(k, k === 'operation' ? (node.type === 'crypto' ? 'sha256' : node.type === 'convert' ? 'json_parse' : node.type === 'table' ? 'find' : node.type === 'aggregate' ? 'count' : 'add') : '')))).map(f => (
            <label key={f.key}>{f.label}
              {f.kind === 'select'
                ? <select value={v(f.key, f.options?.[0][0])} onChange={text(f.key)}>{f.options?.map(([val, lab]) => <option key={val} value={val}>{lab}</option>)}</select>
                : f.kind === 'area'
                  ? <textarea rows={4} spellCheck={false} value={v(f.key)} onChange={text(f.key)} />
                  : <input value={v(f.key)} onChange={text(f.key)} spellCheck={false} placeholder={f.placeholder} />}
            </label>
          ))}
          <span className="hint">{form.hint}</span>
        </>
      );
    }
  }
}
