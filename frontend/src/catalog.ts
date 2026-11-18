import type { Edge, Node } from '@xyflow/react';

export type TriggerKind = 'manual' | 'schedule' | 'webhook' | 'email_in';
export type FlowDoc = { settings?: { timeout_seconds?: number; save_runs?: string }; project_id?: string | null; project_name?: string | null; tags?: string[]; id: string; name: string; description: string; nodes: Node[]; edges: Edge[]; updated_at?: string; active?: boolean; trigger?: TriggerKind; next_run_at?: string | null; webhook_token?: string };
export type Step = { node_id: string; label: string; type?: string; status: string; input?: unknown; output?: unknown; error?: string; branch?: string | null; duration_ms?: number; iteration?: number[]; pinned?: boolean; attempts?: number; continued?: boolean };
export type Run = { id: string; resume_at?: string | null; wait?: { resume_at: string }; trigger?: TriggerKind | 'error' | 'replay'; status: string; error: string | null; output: unknown; steps: Step[]; started_at?: string; workflow_id?: string };

export type NodeKind = 'trigger' | 'schedule' | 'webhook' | 'input' | 'transform' | 'template' | 'set' | 'filter' | 'csv' | 'condition' | 'loop' | 'merge' | 'subflow' | 'switch' | 'code' | 'fail' | 'sql' | 'rss' | 'telegram' | 'github' | 'wait' | 'respond' | 'sheets' | 'gmail' | 'sticky' | 'ai' | 'repeat' | 'file' | 'app' | 'sort' | 'limit' | 'dedupe' | 'split' | 'aggregate' | 'rename' | 'datetime' | 'crypto' | 'email_in' | 'http' | 'notify' | 'email' | 'delay' | 'note' | 'output';
export type Category = 'Start' | 'Data' | 'Logic' | 'Network' | 'Finish';

export const catalog: { type: NodeKind; label: string; desc: string; category: Category; color: string; icon: string }[] = [
  { type: 'trigger', label: 'Manual Trigger', desc: 'Start by clicking Run', category: 'Start', color: '#f59e0b', icon: 'Zap' },
  { type: 'schedule', label: 'Schedule Trigger', desc: 'Run on a timer', category: 'Start', color: '#eab308', icon: 'CalendarClock' },
  { type: 'webhook', label: 'Webhook Trigger', desc: 'Run when a URL is called', category: 'Start', color: '#f43f5e', icon: 'Webhook' },
  { type: 'email_in', label: 'Email Trigger', desc: 'Run when new mail arrives', category: 'Start', color: '#0ea5e9', icon: 'MailOpen' },
  { type: 'input', label: 'JSON Input', desc: 'Provide structured data', category: 'Data', color: '#38bdf8', icon: 'Braces' },
  { type: 'transform', label: 'Extract Field', desc: 'Read a nested JSON path', category: 'Data', color: '#2dd4bf', icon: 'ListTree' },
  { type: 'template', label: 'Text Template', desc: 'Build text with {{placeholders}}', category: 'Data', color: '#a78bfa', icon: 'Type' },
  { type: 'set', label: 'Set Fields', desc: 'Build a JSON object', category: 'Data', color: '#06b6d4', icon: 'PenLine' },
  { type: 'filter', label: 'Filter List', desc: 'Keep matching items', category: 'Data', color: '#84cc16', icon: 'Filter' },
  { type: 'csv', label: 'Parse CSV', desc: 'Turn CSV text into rows', category: 'Data', color: '#22c55e', icon: 'Table' },
  { type: 'loop', label: 'Loop Over Items', desc: 'Run steps once per list item', category: 'Logic', color: '#0ea5e9', icon: 'Repeat' },
  { type: 'repeat', label: 'Repeat Until', desc: 'Repeat steps until a condition is met', category: 'Logic', color: '#6366f1', icon: 'RotateCw' },
  { type: 'merge', label: 'Merge', desc: 'Combine several branches', category: 'Logic', color: '#14b8a6', icon: 'GitMerge' },
  { type: 'subflow', label: 'Run Workflow', desc: 'Call another workflow', category: 'Logic', color: '#64748b', icon: 'Workflow' },
  { type: 'switch', label: 'Switch', desc: 'Route into up to 4 paths plus a default', category: 'Logic', color: '#ec4899', icon: 'Split' },
  { type: 'code', label: 'Code', desc: 'Run a short Python snippet', category: 'Logic', color: '#10b981', icon: 'Code' },
  { type: 'fail', label: 'Stop and Fail', desc: 'End the run with an error message', category: 'Logic', color: '#ef4444', icon: 'OctagonX' },
  { type: 'condition', label: 'Compare Value', desc: 'Branch into true and false paths', category: 'Logic', color: '#f472b6', icon: 'GitBranch' },
  { type: 'delay', label: 'Delay', desc: 'Wait up to 5 seconds', category: 'Logic', color: '#fb923c', icon: 'Timer' },
  { type: 'file', label: 'Files', desc: 'Read, write, list or delete files', category: 'Data', color: '#64748b', icon: 'FileText' },
  { type: 'sql', label: 'Database Query', desc: 'Run SQL on SQLite or Postgres', category: 'Data', color: '#0891b2', icon: 'Database' },
  { type: 'rss', label: 'Read RSS Feed', desc: 'Fetch items from an RSS or Atom feed', category: 'Network', color: '#f97316', icon: 'Rss' },
  { type: 'wait', label: 'Wait', desc: 'Pause for seconds, hours or days', category: 'Logic', color: '#f59e0b', icon: 'Hourglass' },
  { type: 'http', label: 'HTTP Request', desc: 'Call a public HTTPS API', category: 'Network', color: '#818cf8', icon: 'Globe' },
  { type: 'notify', label: 'Send Message', desc: 'Slack, Discord or a webhook', category: 'Network', color: '#a855f7', icon: 'Bell' },
  { type: 'telegram', label: 'Telegram Message', desc: 'Send with a Telegram bot', category: 'Network', color: '#0ea5e9', icon: 'Send' },
  { type: 'sort', label: 'Sort', desc: 'Order a list by a field', category: 'Data', color: '#0d9488', icon: 'ArrowUpDown' },
  { type: 'limit', label: 'Limit', desc: 'Keep the first or last N items', category: 'Data', color: '#0d9488', icon: 'ListEnd' },
  { type: 'dedupe', label: 'Remove Duplicates', desc: 'Drop repeated items', category: 'Data', color: '#0d9488', icon: 'CopyMinus' },
  { type: 'split', label: 'Split Out', desc: 'Take a list from inside the data', category: 'Data', color: '#0d9488', icon: 'Ungroup' },
  { type: 'aggregate', label: 'Summarize', desc: 'Count, sum, average, min, max or collect', category: 'Data', color: '#0d9488', icon: 'Sigma' },
  { type: 'rename', label: 'Rename Keys', desc: 'Change field names', category: 'Data', color: '#0d9488', icon: 'TextCursorInput' },
  { type: 'datetime', label: 'Date and Time', desc: 'Add, subtract, compare and format dates', category: 'Data', color: '#0d9488', icon: 'CalendarDays' },
  { type: 'crypto', label: 'Crypto and Encoding', desc: 'Hash, sign, base64 and random values', category: 'Data', color: '#0d9488', icon: 'KeyRound' },
  { type: 'app', label: 'App Action', desc: 'Slack, Notion, Airtable, Stripe, Twilio, Linear, HubSpot', category: 'Network', color: '#6366f1', icon: 'Plug' },
  { type: 'github', label: 'GitHub Issues', desc: 'Create or list issues', category: 'Network', color: '#475569', icon: 'Github' },
  { type: 'sheets', label: 'Google Sheets', desc: 'Read rows or append a row', category: 'Network', color: '#16a34a', icon: 'Sheet' },
  { type: 'gmail', label: 'Gmail', desc: 'Send with your Google account', category: 'Network', color: '#dc2626', icon: 'MailCheck' },
  { type: 'ai', label: 'AI Prompt', desc: 'Ask a local or hosted language model', category: 'Network', color: '#8b5cf6', icon: 'Bot' },
  { type: 'email', label: 'Send Email', desc: 'Send over SMTP', category: 'Network', color: '#d946ef', icon: 'Mail' },
  { type: 'note', label: 'Pass Through', desc: 'Forward data unchanged', category: 'Logic', color: '#94a3b8', icon: 'ArrowRight' },
  { type: 'respond', label: 'Respond to Webhook', desc: 'Choose the reply a webhook caller gets', category: 'Finish', color: '#f43f5e', icon: 'Reply' },
  { type: 'sticky', label: 'Sticky Note', desc: 'Leave a note on the canvas', category: 'Finish', color: '#eab308', icon: 'StickyNote' },
  { type: 'output', label: 'Result Output', desc: 'Finish and inspect data', category: 'Finish', color: '#34d399', icon: 'Flag' },
];

export const startTypes: string[] = ['trigger', 'schedule', 'webhook', 'email_in'];
/** Nodes whose output can be pinned so the node is skipped while you build the rest. */
export const pinnableTypes: string[] = ['sort', 'limit', 'dedupe', 'split', 'aggregate', 'rename', 'datetime', 'crypto', 'app', 'input', 'transform', 'template', 'http', 'delay', 'set', 'filter', 'csv', 'notify', 'email', 'subflow', 'code', 'sql', 'rss', 'file'];
/** Nodes that support retries and continue-on-error. */
/** Nodes that can run once for every element of a list. */
export const eachItemTypes: string[] = ['datetime', 'crypto', 'app', 'transform', 'template', 'set', 'code', 'http', 'ai', 'sql', 'notify', 'email', 'telegram', 'gmail', 'github', 'sheets', 'file', 'rss'];
export const errorTypes: string[] = ['sort', 'limit', 'dedupe', 'split', 'aggregate', 'rename', 'datetime', 'crypto', 'app', 'input', 'transform', 'template', 'set', 'filter', 'csv', 'http', 'notify', 'email', 'subflow', 'code', 'delay', 'sql', 'rss', 'telegram', 'github', 'sheets', 'gmail', 'file'];

type AppOp = { label: string; example: string; hint: string };
export const appServices: Record<string, { label: string; credential: string; operations: Record<string, AppOp> }> = {
  slack: { label: 'Slack', credential: 'Bot token starting with xoxb-', operations: {
    post_message: { label: 'Post a message', example: '{"channel": "#general", "text": "Hello from FlowPilot"}', hint: 'channel and text. The bot must be invited to the channel.' } } },
  notion: { label: 'Notion', credential: 'Internal integration token', operations: {
    create_page: { label: 'Add a row to a database', example: '{"database_id": "", "title": "New item", "title_property": "Name"}', hint: 'database_id and title. Optional: title_property, properties (raw Notion properties). Share the database with your integration first.' },
    query_database: { label: 'List database rows', example: '{"database_id": "", "limit": 20}', hint: 'database_id and an optional limit (up to 100).' } } },
  airtable: { label: 'Airtable', credential: 'Personal access token', operations: {
    list_records: { label: 'List records', example: '{"base_id": "appXXXXXXXXXXXXXX", "table": "Tasks", "limit": 20}', hint: 'base_id, table and an optional limit (up to 100).' },
    create_record: { label: 'Create a record', example: '{"base_id": "appXXXXXXXXXXXXXX", "table": "Tasks", "record": {"Name": "{{name}}"}}', hint: 'base_id, table and record (an object of field values).' } } },
  stripe: { label: 'Stripe', credential: 'Secret key starting with sk_ (use a restricted key)', operations: {
    list_customers: { label: 'List customers', example: '{"limit": 20}', hint: 'Optional limit (up to 100).' },
    create_customer: { label: 'Create a customer', example: '{"email": "{{email}}", "name": "{{name}}"}', hint: 'email and/or name.' },
    list_payments: { label: 'List payments', example: '{"limit": 20}', hint: 'Optional limit (up to 100).' } } },
  twilio: { label: 'Twilio', credential: 'AccountSID:AuthToken', operations: {
    send_sms: { label: 'Send an SMS', example: '{"to": "+15551230000", "from": "+15559990000", "body": "Hello"}', hint: 'to, from (your Twilio number) and body. Numbers in +E.164 form.' } } },
  linear: { label: 'Linear', credential: 'Personal API key (lin_api_...)', operations: {
    create_issue: { label: 'Create an issue', example: '{"team_id": "", "title": "{{title}}", "description": ""}', hint: 'team_id (a UUID), title and an optional description.' } } },
  hubspot: { label: 'HubSpot', credential: 'Private app access token', operations: {
    create_contact: { label: 'Create a contact', example: '{"email": "{{email}}", "firstname": "{{name}}"}', hint: 'email is required. Optional: firstname, lastname, phone, company.' },
    list_contacts: { label: 'List contacts', example: '{"limit": 20}', hint: 'Optional limit (up to 100).' } } },
};

type ToolField = { key: string; label: string; kind: 'text' | 'select' | 'area'; options?: [string, string][]; placeholder?: string; hint?: string; when?: Record<string, string[]> };
/** Settings forms for the small data nodes. `when` shows a field only for the listed values of another field. */
export const toolForms: Record<string, { fields: ToolField[]; hint: string }> = {
  sort: { hint: 'Needs a list. Numbers sort as numbers, text sorts without regard to capitals, empty values go last.', fields: [
    { key: 'path', label: 'Sort by field (blank sorts the values themselves)', kind: 'text', placeholder: 'price' },
    { key: 'order', label: 'Order', kind: 'select', options: [['asc', 'Smallest first'], ['desc', 'Largest first']] }] },
  limit: { hint: 'Needs a list.', fields: [
    { key: 'count', label: 'How many items', kind: 'text' },
    { key: 'from', label: 'Keep from the', kind: 'select', options: [['first', 'Start of the list'], ['last', 'End of the list']] }] },
  dedupe: { hint: 'Needs a list. The first item of each kind is kept.', fields: [
    { key: 'path', label: 'Compare by field (blank compares whole items)', kind: 'text', placeholder: 'email' }] },
  split: { hint: 'Use this when an API wraps its list inside an object, for example data.rows.', fields: [
    { key: 'path', label: 'Field that holds the list', kind: 'text', placeholder: 'data.rows' }] },
  aggregate: { hint: 'Needs a list. Sum, average, minimum and maximum need numbers.', fields: [
    { key: 'operation', label: 'Result', kind: 'select', options: [['count', 'Number of items'], ['sum', 'Sum'], ['avg', 'Average'], ['min', 'Smallest'], ['max', 'Largest'], ['collect', 'List of one field']] },
    { key: 'path', label: 'Field (blank uses the values themselves)', kind: 'text', placeholder: 'amount' }] },
  rename: { hint: 'Works on one object or on every object in a list. Fields not listed keep their names.', fields: [
    { key: 'mapping', label: 'Names to change (JSON)', kind: 'area' }] },
  datetime: { hint: 'Dates look like 2025-01-31 or 2025-01-31T10:00:00Z (UTC is assumed without a zone). Format codes: %Y year, %m month, %d day, %H hour, %M minute.', fields: [
    { key: 'operation', label: 'Action', kind: 'select', options: [['now', 'Current date and time'], ['add', 'Add time'], ['subtract', 'Subtract time'], ['diff', 'Time between two dates'], ['format', 'Format a date']] },
    { key: 'value', label: 'Date (blank means now)', kind: 'text', placeholder: '{{created_at}}', when: { operation: ['add', 'subtract', 'diff', 'format'] } },
    { key: 'amount', label: 'Amount', kind: 'text', when: { operation: ['add', 'subtract'] } },
    { key: 'unit', label: 'Unit', kind: 'select', options: [['seconds', 'Seconds'], ['minutes', 'Minutes'], ['hours', 'Hours'], ['days', 'Days'], ['weeks', 'Weeks']], when: { operation: ['add', 'subtract', 'diff'] } },
    { key: 'other', label: 'Second date', kind: 'text', when: { operation: ['diff'] } },
    { key: 'format', label: 'Format', kind: 'text', placeholder: '%Y-%m-%d', when: { operation: ['format'] } }] },
  crypto: { hint: 'Hashes are one-way. For a signature, save the key on the Credentials page and write {{cred.NAME}} as the key.', fields: [
    { key: 'operation', label: 'Action', kind: 'select', options: [['sha256', 'SHA-256 hash'], ['sha512', 'SHA-512 hash'], ['sha1', 'SHA-1 hash'], ['md5', 'MD5 hash'], ['hmac_sha256', 'HMAC-SHA256 signature'], ['base64_encode', 'Base64 encode'], ['base64_decode', 'Base64 decode'], ['uuid', 'Random unique ID'], ['random', 'Random text']] },
    { key: 'value', label: 'Text (for Random text: the length)', kind: 'text', when: { operation: ['sha256', 'sha512', 'sha1', 'md5', 'hmac_sha256', 'base64_encode', 'base64_decode', 'random'] } },
    { key: 'key', label: 'Secret key', kind: 'text', placeholder: '{{cred.SIGNING_KEY}}', when: { operation: ['hmac_sha256'] } }] },
};

export const operators = [
  { value: 'equals', label: 'equals' },
  { value: 'not_equals', label: 'does not equal' },
  { value: 'contains', label: 'contains' },
  { value: 'gt', label: 'is greater than' },
  { value: 'lt', label: 'is less than' },
  { value: 'exists', label: 'exists' },
  { value: 'not_exists', label: 'does not exist' },
];

export const defaultData = (type: string): Record<string, string> => {
  switch (type) {
    case 'input': return { json: '{"hello":"world"}' };
    case 'transform': return { path: 'hello' };
    case 'template': return { template: 'Hello {{hello}}!' };
    case 'condition': return { path: 'status', op: 'equals', equals: 'active' };
    case 'delay': return { seconds: '1' };
    case 'http': return { url: 'https://jsonplaceholder.typicode.com/todos/1', method: 'GET', headers: '', body: '' };
    case 'schedule': return { mode: 'interval', minutes: '15', time: '09:00', cron: '0 9 * * 1-5' };
    case 'set': return { json: '{"greeting":"Hello {{name}}"}' };
    case 'filter': return { path: 'score', op: 'gt', equals: '50' };
    case 'csv': return { path: '', header: 'true' };
    case 'loop': return { path: '' };
    case 'merge': return { mode: 'append' };
    case 'switch': return { path: 'status', case1: 'new', case2: 'paid', case3: '', case4: '' };
    case 'code': return { code: "# 'input' holds the incoming data. Put your answer in 'result'.\nresult = {'count': len(input) if isinstance(input, list) else 1}" };
    case 'sql': return { credential: '', query: 'SELECT 1 AS ok', params: '' };
    case 'rss': return { url: 'https://hnrss.org/frontpage', limit: '10' };
    case 'wait': return { seconds: '5', unit: 'seconds' };
    case 'sheets': return { credential: '', spreadsheet_id: '', range: 'Sheet1', operation: 'read', header: 'true', values: '["{{name}}", "{{city}}"]' };
    case 'gmail': return { credential: '', to: '', subject: 'Hello', body: 'Hi {{name}}' };
    case 'telegram': return { credential: '', chat_id: '', text: 'Hello from FlowPilot' };
    case 'sort': return { path: '', order: 'asc' };
    case 'limit': return { count: '10', from: 'first' };
    case 'dedupe': return { path: '' };
    case 'split': return { path: 'items' };
    case 'aggregate': return { operation: 'count', path: '' };
    case 'rename': return { mapping: '{"old_name": "new_name"}' };
    case 'datetime': return { operation: 'add', value: '', amount: '1', unit: 'days', other: '', format: '%Y-%m-%d' };
    case 'crypto': return { operation: 'sha256', value: '{{}}', key: '' };
    case 'app': return { service: 'slack', operation: 'post_message', credential: '', fields: appServices.slack.operations.post_message.example };
    case 'github': return { credential: '', repo: 'owner/name', operation: 'create_issue', title: 'New issue', body: '', state: 'open' };
    case 'respond': return { status: '200', body: '{"ok": true}' };
    case 'sticky': return { text: 'Write a note here' };
    case 'ai': return { credential: '', prompt: 'Summarise this in one sentence: {{}}', system: '', temperature: '0.2', json_output: 'false' };
    case 'email_in': return { credential: '', minutes: '5', from_filter: '', subject_filter: '' };
    case 'repeat': return { path: 'status', op: 'equals', equals: 'done', max: '5', pause: '1', fail_if_not_met: 'false' };
    case 'file': return { operation: 'read', path: 'notes.txt', as: 'text', content: '', encoding: 'text', mode: 'overwrite' };
    case 'fail': return { message: 'Something went wrong with {{name}}' };
    case 'subflow': return { workflow_id: '' };
    case 'notify': return { service: 'slack', url: '{{cred.SLACK_URL}}', message: 'Hello from FlowPilot' };
    case 'email': return { credential: '', to: '', subject: 'Hello', body: 'Hi {{name}}' };
    case 'webhook': return { sample: '{"name":"Ada","message":"Hello from a webhook"}' };
    default: return {};
  }
};

const n = (id: string, type: NodeKind, x: number, y: number, extra: Record<string, string> = {}): Node => ({
  id, type, position: { x, y }, data: { label: catalog.find(c => c.type === type)!.label, ...extra },
});
const e = (source: string, target: string, sourceHandle?: string): Edge => ({ id: `${source}-${target}${sourceHandle ? '-' + sourceHandle : ''}`, source, target, ...(sourceHandle ? { sourceHandle } : {}) });

export const blankNodes: Node[] = [n('start', 'trigger', 60, 160), n('input', 'input', 330, 160, { json: '{"message":"Hello FlowPilot"}' }), n('out', 'output', 600, 160)];
export const blankEdges: Edge[] = [e('start', 'input'), e('input', 'out')];

export const templates: { id: string; name: string; description: string; tag: string; nodes: Node[]; edges: Edge[] }[] = [
  {
    id: 'greeting', name: 'Personalised greeting', tag: 'Starter',
    description: 'Read a name from JSON and build a greeting with a text template.',
    nodes: [n('t', 'trigger', 40, 160), n('i', 'input', 290, 160, { json: '{"name":"Ada","city":"London"}' }), n('m', 'template', 560, 160, { template: 'Hello {{name}} from {{city}}!' }), n('o', 'output', 830, 160)],
    edges: [e('t', 'i'), e('i', 'm'), e('m', 'o')],
  },
  {
    id: 'order-triage', name: 'Order triage', tag: 'Branching',
    description: 'Route orders by value: big orders get a priority message, others a standard one.',
    nodes: [
      n('t', 'trigger', 20, 150), n('i', 'input', 250, 150, { json: '{"order":"A-1042","total":250}' }),
      n('c', 'condition', 480, 150, { path: 'total', op: 'gt', equals: '100' }),
      n('hi', 'template', 740, 40, { template: 'PRIORITY: order {{order}} ({{total}})' }),
      n('lo', 'template', 740, 270, { template: 'Standard: order {{order}} ({{total}})' }),
      n('o1', 'output', 990, 40), n('o2', 'output', 990, 270),
    ],
    edges: [e('t', 'i'), e('i', 'c'), e('c', 'hi', 'true'), e('c', 'lo', 'false'), e('hi', 'o1'), e('lo', 'o2')],
  },
  {
    id: 'fetch-todo', name: 'Fetch and extract', tag: 'Network',
    description: 'Call a public API over HTTPS and pull one field out of the response.',
    nodes: [n('t', 'trigger', 40, 160), n('h', 'http', 290, 160, { url: 'https://jsonplaceholder.typicode.com/todos/1', method: 'GET' }), n('x', 'transform', 560, 160, { path: 'title' }), n('o', 'output', 830, 160)],
    edges: [e('t', 'h'), e('h', 'x'), e('x', 'o')],
  },
  {
    id: 'webhook-receiver', name: 'Webhook receiver', tag: 'Webhook',
    description: 'Wait for an HTTP request, then turn its JSON body into a message. Activate it to get a live URL.',
    nodes: [n('w', 'webhook', 40, 160, { sample: '{"name":"Ada","message":"Hello from a webhook"}' }), n('m', 'template', 310, 160, { template: 'New message from {{body.name}}: {{body.message}}' }), n('o', 'output', 580, 160)],
    edges: [e('w', 'm'), e('m', 'o')],
  },
  {
    id: 'scheduled-check', name: 'Hourly API check', tag: 'Schedule',
    description: 'Call a public API every hour and keep the result in your run history. Activate it to start the timer.',
    nodes: [n('s', 'schedule', 40, 160, { mode: 'interval', minutes: '60', time: '09:00' }), n('h', 'http', 310, 160, { url: 'https://jsonplaceholder.typicode.com/todos/1', method: 'GET', headers: '', body: '' }), n('x', 'transform', 580, 160, { path: 'title' }), n('o', 'output', 850, 160)],
    edges: [e('s', 'h'), e('h', 'x'), e('x', 'o')],
  },
  {
    id: 'post-api', name: 'Send data to an API', tag: 'HTTP POST',
    description: 'Build a JSON body from your data, POST it to an API and read a field from the response.',
    nodes: [n('t', 'trigger', 30, 160), n('i', 'input', 250, 160, { json: '{"title":"Hello","body":"From FlowPilot","userId":1}' }), n('h', 'http', 480, 160, { url: 'https://jsonplaceholder.typicode.com/posts', method: 'POST', headers: '{"X-Source":"flowpilot"}', body: '{{}}' }), n('x', 'transform', 740, 160, { path: 'id' }), n('o', 'output', 980, 160)],
    edges: [e('t', 'i'), e('i', 'h'), e('h', 'x'), e('x', 'o')],
  },
  {
    id: 'loop-list', name: 'Process a list', tag: 'Loop',
    description: 'Run steps once for every item in a list, then collect the results. Great for orders, rows or users.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 240, 150, { json: '[{"name":"Ada","score":91},{"name":"Bob","score":48},{"name":"Cy","score":77}]' }), n('l', 'loop', 480, 150), n('m', 'template', 740, 40, { template: '{{name}} scored {{score}}' }), n('o', 'output', 740, 260)],
    edges: [e('t', 'i'), e('i', 'l'), e('l', 'm', 'each'), e('l', 'o', 'done')],
  },
  {
    id: 'csv-report', name: 'CSV to filtered report', tag: 'Data',
    description: 'Parse CSV text, keep rows above a score, then build one line per row with a loop.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 230, 150, { json: '"name,score\\nAda,90\\nBob,40\\nCy,75"' }), n('c', 'csv', 440, 150), n('f', 'filter', 650, 150, { path: 'score', op: 'gt', equals: '50' }), n('l', 'loop', 860, 150), n('m', 'template', 1100, 40, { template: '{{name}}: {{score}}' }), n('o', 'output', 1100, 260)],
    edges: [e('t', 'i'), e('i', 'c'), e('c', 'f'), e('f', 'l'), e('l', 'm', 'each'), e('l', 'o', 'done')],
  },
];
