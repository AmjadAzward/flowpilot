import type { Edge, Node } from '@xyflow/react';

export type TriggerKind = 'manual' | 'schedule' | 'webhook' | 'email_in' | 'poll_in';
export type FlowDoc = { settings?: { timeout_seconds?: number; save_runs?: string }; project_id?: string | null; project_name?: string | null; tags?: string[]; id: string; name: string; description: string; nodes: Node[]; edges: Edge[]; updated_at?: string; active?: boolean; trigger?: TriggerKind; next_run_at?: string | null; webhook_token?: string };
export type Step = { node_id: string; label: string; type?: string; status: string; input?: unknown; output?: unknown; error?: string; branch?: string | null; duration_ms?: number; iteration?: number[]; pinned?: boolean; attempts?: number; continued?: boolean };
export type Run = { waiting_for?: string | null; id: string; resume_at?: string | null; wait?: { resume_at: string }; trigger?: TriggerKind | 'error' | 'replay'; status: string; error: string | null; output: unknown; steps: Step[]; started_at?: string; workflow_id?: string };

export type NodeKind = 'trigger' | 'schedule' | 'webhook' | 'input' | 'transform' | 'template' | 'set' | 'filter' | 'csv' | 'condition' | 'loop' | 'merge' | 'subflow' | 'switch' | 'code' | 'fail' | 'sql' | 'rss' | 'telegram' | 'github' | 'wait' | 'respond' | 'sheets' | 'gmail' | 'sticky' | 'ai' | 'repeat' | 'file' | 'app' | 'agent' | 'callback' | 'poll_in' | 'sort' | 'limit' | 'dedupe' | 'split' | 'aggregate' | 'rename' | 'datetime' | 'crypto' | 'email_in' | 'http' | 'notify' | 'email' | 'delay' | 'note' | 'output';
export type Category = 'Start' | 'Data' | 'Logic' | 'Network' | 'Finish';

export const catalog: { type: NodeKind; label: string; desc: string; category: Category; color: string; icon: string }[] = [
  { type: 'trigger', label: 'Manual Trigger', desc: 'Start by clicking Run', category: 'Start', color: '#f59e0b', icon: 'Zap' },
  { type: 'schedule', label: 'Schedule Trigger', desc: 'Run on a timer', category: 'Start', color: '#eab308', icon: 'CalendarClock' },
  { type: 'webhook', label: 'Webhook Trigger', desc: 'Run when a URL is called', category: 'Start', color: '#f43f5e', icon: 'Webhook' },
  { type: 'poll_in', label: 'New Items Trigger', desc: 'Run for each new RSS post, issue or API item', category: 'Start', color: '#14b8a6', icon: 'Radar' },
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
  { type: 'callback', label: 'Wait for Response', desc: 'Pause until someone approves or rejects', category: 'Logic', color: '#d97706', icon: 'UserCheck' },
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
  { type: 'app', label: 'App Action', desc: '28 services: Slack, Shopify, Jira, Stripe, WhatsApp and more', category: 'Network', color: '#6366f1', icon: 'Plug' },
  { type: 'github', label: 'GitHub Issues', desc: 'Create or list issues', category: 'Network', color: '#475569', icon: 'Github' },
  { type: 'sheets', label: 'Google Sheets', desc: 'Read rows or append a row', category: 'Network', color: '#16a34a', icon: 'Sheet' },
  { type: 'gmail', label: 'Gmail', desc: 'Send with your Google account', category: 'Network', color: '#dc2626', icon: 'MailCheck' },
  { type: 'agent', label: 'AI Agent', desc: 'A model that can use tools and run workflows', category: 'Network', color: '#7c3aed', icon: 'Sparkles' },
  { type: 'ai', label: 'AI Prompt', desc: 'Ask a local or hosted language model', category: 'Network', color: '#8b5cf6', icon: 'Bot' },
  { type: 'email', label: 'Send Email', desc: 'Send over SMTP', category: 'Network', color: '#d946ef', icon: 'Mail' },
  { type: 'note', label: 'Pass Through', desc: 'Forward data unchanged', category: 'Logic', color: '#94a3b8', icon: 'ArrowRight' },
  { type: 'respond', label: 'Respond to Webhook', desc: 'Choose the reply a webhook caller gets', category: 'Finish', color: '#f43f5e', icon: 'Reply' },
  { type: 'sticky', label: 'Sticky Note', desc: 'Leave a note on the canvas', category: 'Finish', color: '#eab308', icon: 'StickyNote' },
  { type: 'output', label: 'Result Output', desc: 'Finish and inspect data', category: 'Finish', color: '#34d399', icon: 'Flag' },
];

export const startTypes: string[] = ['trigger', 'schedule', 'webhook', 'email_in', 'poll_in'];
/** Nodes whose output can be pinned so the node is skipped while you build the rest. */
export const pinnableTypes: string[] = ['agent', 'sort', 'limit', 'dedupe', 'split', 'aggregate', 'rename', 'datetime', 'crypto', 'app', 'input', 'transform', 'template', 'http', 'delay', 'set', 'filter', 'csv', 'notify', 'email', 'subflow', 'code', 'sql', 'rss', 'file'];
/** Nodes that support retries and continue-on-error. */
/** Nodes that can run once for every element of a list. */
export const eachItemTypes: string[] = ['agent', 'datetime', 'crypto', 'app', 'transform', 'template', 'set', 'code', 'http', 'ai', 'sql', 'notify', 'email', 'telegram', 'gmail', 'github', 'sheets', 'file', 'rss'];
export const errorTypes: string[] = ['agent', 'sort', 'limit', 'dedupe', 'split', 'aggregate', 'rename', 'datetime', 'crypto', 'app', 'input', 'transform', 'template', 'set', 'filter', 'csv', 'http', 'notify', 'email', 'subflow', 'code', 'delay', 'sql', 'rss', 'telegram', 'github', 'sheets', 'gmail', 'file'];

type AppOp = { label: string; example: string; hint: string };
export const appServices: Record<string, { label: string; credential: string; oauth?: boolean; operations: Record<string, AppOp> }> = {
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

appServices.gcalendar = { label: 'Google Calendar', credential: 'A Google connection with the Calendar permission (add the scope https://www.googleapis.com/auth/calendar.events when connecting)', oauth: true, operations: {
  list_events: { label: 'List upcoming events', example: '{"calendar_id": "primary", "limit": 20}', hint: 'Optional: calendar_id (default primary), time_min (a date or date and time, default now) and limit (up to 250).' },
  create_event: { label: 'Create an event', example: '{"summary": "{{title}}", "start": "2026-03-01T10:00:00Z", "end": "2026-03-01T11:00:00Z", "description": "", "location": ""}', hint: 'summary, start and end (a date like 2026-03-01 for an all-day event, or a date and time). Optional: calendar_id, description, location.' } } };
appServices.gdrive = { label: 'Google Drive', credential: 'A Google connection with the Drive permission (add the scope https://www.googleapis.com/auth/drive.file when connecting)', oauth: true, operations: {
  list_files: { label: 'List files', example: '{"name_contains": "invoice", "folder_id": "", "limit": 20}', hint: 'Optional: name_contains, folder_id and limit (up to 100). Newest first. Trashed files are left out.' },
  create_folder: { label: 'Create a folder', example: '{"name": "Reports", "folder_id": ""}', hint: 'name. Optional: folder_id of the parent folder.' } } };
appServices.shopify = { label: 'Shopify', credential: 'Admin API access token (from a custom app)', operations: {
  list_orders: { label: 'List orders', example: '{"shop": "mystore", "status": "any", "limit": 20}', hint: 'shop (the part before .myshopify.com). Optional: status (any, open, closed, cancelled) and limit (up to 50).' },
  list_products: { label: 'List products', example: '{"shop": "mystore", "limit": 20}', hint: 'shop. Optional: limit (up to 50).' } } };
appServices.zendesk = { label: 'Zendesk', credential: 'email:API_TOKEN (like you@example.com:abc123)', operations: {
  create_ticket: { label: 'Create a ticket', example: '{"subdomain": "mycompany", "subject": "{{title}}", "body": "{{message}}", "requester_email": ""}', hint: 'subdomain, subject and body. Optional: requester_email.' } } };
appServices.pipedrive = { label: 'Pipedrive', credential: 'API token', operations: {
  create_person: { label: 'Create a person', example: '{"name": "{{name}}", "email": "{{email}}", "phone": ""}', hint: 'name. Optional: email and phone.' },
  create_deal: { label: 'Create a deal', example: '{"title": "{{title}}", "value": "1000", "currency": "USD", "person_id": ""}', hint: 'title. Optional: value, currency, person_id and org_id (numbers).' } } };
appServices.clickup = { label: 'ClickUp', credential: 'Personal API token (pk_...)', operations: {
  create_task: { label: 'Create a task', example: '{"list_id": "", "name": "{{title}}", "description": "", "priority": "3"}', hint: 'list_id (a number) and name. Optional: description and priority (1 urgent to 4 low).' } } };
appServices.monday = { label: 'monday.com', credential: 'API token', operations: {
  create_item: { label: 'Create an item', example: '{"board_id": "", "item_name": "{{title}}", "column_values": {}}', hint: 'board_id (a number) and item_name. Optional: column_values (an object keyed by column id).' } } };
appServices.dropbox = { label: 'Dropbox', credential: 'Access token', operations: {
  list_folder: { label: 'List a folder', example: '{"path": "", "limit": 50}', hint: 'Optional: path (starts with /, blank is the root) and limit (up to 200).' },
  create_folder: { label: 'Create a folder', example: '{"path": "/Reports"}', hint: 'path, starting with /.' } } };
appServices.resend = { label: 'Resend (email)', credential: 'API key (re_...)', operations: {
  send_email: { label: 'Send an email', example: '{"to": "{{email}}", "from": "You <you@yourdomain.com>", "subject": "Hello", "text": "Hi {{name}}"}', hint: 'to, from (a verified domain), subject and text.' } } };
appServices.postmark = { label: 'Postmark (email)', credential: 'Server API token', operations: {
  send_email: { label: 'Send an email', example: '{"to": "{{email}}", "from": "you@yourdomain.com", "subject": "Hello", "text": "Hi {{name}}"}', hint: 'to, from (a verified sender), subject and text.' } } };
appServices.whatsapp = { label: 'WhatsApp (Cloud API)', credential: 'Access token', operations: {
  send_text: { label: 'Send a text message', example: '{"phone_number_id": "", "to": "15551230000", "text": "Hello from FlowPilot"}', hint: 'phone_number_id (from the WhatsApp setup page), to (with country code) and text. Outside a conversation window WhatsApp only allows approved templates.' } } };
appServices.pushover = { label: 'Pushover', credential: 'APP_TOKEN:USER_KEY', operations: {
  send: { label: 'Send a notification', example: '{"message": "{{message}}", "title": "FlowPilot", "priority": "0"}', hint: 'message. Optional: title and priority (-2, -1, 0 or 1).' } } };
appServices.sendgrid = { label: 'SendGrid', credential: 'API key starting with SG.', operations: {
  send_email: { label: 'Send an email', example: '{"to": "{{email}}", "from": "you@yourdomain.com", "subject": "Hello", "text": "Hi {{name}}"}', hint: 'to, from (a verified sender), subject and text.' } } };
appServices.mailchimp = { label: 'Mailchimp', credential: 'API key ending in a data centre, like ...-us21', operations: {
  add_subscriber: { label: 'Add a subscriber', example: '{"list_id": "", "email": "{{email}}", "first_name": "{{name}}", "status": "subscribed"}', hint: 'list_id (Audience ID) and email. Optional: first_name, last_name, status (subscribed or pending).' } } };
appServices.trello = { label: 'Trello', credential: 'API_KEY:TOKEN (both from trello.com/app-key)', operations: {
  create_card: { label: 'Create a card', example: '{"list_id": "", "name": "{{title}}", "description": ""}', hint: 'list_id (24 characters) and name. Optional: description.' } } };
appServices.asana = { label: 'Asana', credential: 'Personal access token', operations: {
  create_task: { label: 'Create a task', example: '{"name": "{{title}}", "notes": "", "project_id": ""}', hint: 'name. Optional: notes and project_id (a number).' } } };
appServices.jira = { label: 'Jira', credential: 'email:API_TOKEN (like you@example.com:abc123)', operations: {
  create_issue: { label: 'Create an issue', example: '{"site": "mycompany", "project": "ENG", "summary": "{{title}}", "description": "", "type": "Task"}', hint: 'site (the part before .atlassian.net), project (key), summary. Optional: description, type.' } } };
appServices.todoist = { label: 'Todoist', credential: 'API token', operations: {
  create_task: { label: 'Create a task', example: '{"content": "{{title}}", "description": "", "due": "tomorrow"}', hint: 'content. Optional: description and due (plain words like "tomorrow at 9am").' } } };
appServices.gitlab = { label: 'GitLab', credential: 'Personal access token with the api scope', operations: {
  create_issue: { label: 'Create an issue', example: '{"project": "group/name", "title": "{{title}}", "description": ""}', hint: 'project (a number or group/name), title. Optional: description.' } } };
appServices.discord = { label: 'Discord bot', credential: 'Bot token', operations: {
  post_message: { label: 'Post a message', example: '{"channel_id": "", "content": "Hello from FlowPilot"}', hint: 'channel_id (a long number) and content. The bot must be in the server with permission to post.' } } };
appServices.ntfy = { label: 'ntfy (free push notifications)', credential: 'Optional access token (leave the credential empty for public topics)', operations: {
  publish: { label: 'Send a notification', example: '{"topic": "my-flowpilot-alerts", "message": "{{message}}", "title": "FlowPilot", "priority": "3"}', hint: 'topic and message. Optional: title, priority (1 to 5), server (default https://ntfy.sh). Anyone who knows a public topic name can read it, so pick a long random one.' } } };

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
    case 'callback': return { seconds: '1', unit: 'days', on_timeout: 'continue' };
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
    case 'agent': return { credential: '', system: 'You are a careful assistant. Use the tools when they help, and answer briefly.', prompt: 'Work out what {{topic}} would cost with 20% tax on 49.90.', tools: 'calculator', workflows: '', max_steps: '4' };
    case 'ai': return { credential: '', prompt: 'Summarise this in one sentence: {{}}', system: '', temperature: '0.2', json_output: 'false' };
    case 'poll_in': return { source: 'rss', url: 'https://hnrss.org/newest', minutes: '15', repo: '', state: 'open', credential: '', items_path: '', id_path: '', order: 'newest_first' };
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
  {
    id: 'top-n', name: 'Top three by score', tag: 'Lists',
    description: 'Sort a list from highest to lowest, keep the first three and write one line for each.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '[{"name":"Ada","score":91},{"name":"Bob","score":48},{"name":"Cy","score":77},{"name":"Di","score":85},{"name":"Ed","score":62}]' }), n('s', 'sort', 430, 150, { path: 'score', order: 'desc' }), n('l', 'limit', 630, 150, { count: '3', from: 'first' }), n('m', 'template', 830, 150, { template: '{{name}}: {{score}}', each_item: 'true' }), n('o', 'output', 1040, 150)],
    edges: [e('t', 'i'), e('i', 's'), e('s', 'l'), e('l', 'm'), e('m', 'o')],
  },
  {
    id: 'clean-contacts', name: 'Clean a contact list', tag: 'Data',
    description: 'Rename a field and remove contacts that appear twice, keeping the first of each.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 230, 150, { json: '[{"email":"a@x.com","name":"Ada"},{"email":"a@x.com","name":"Ada L."},{"email":"b@x.com","name":"Bob"}]' }), n('r', 'rename', 450, 150, { mapping: '{"email":"mail"}' }), n('d', 'dedupe', 670, 150, { path: 'mail' }), n('o', 'output', 890, 150)],
    edges: [e('t', 'i'), e('i', 'r'), e('r', 'd'), e('d', 'o')],
  },
  {
    id: 'sales-summary', name: 'Paid sales total', tag: 'Data',
    description: 'Keep only paid orders, add up their amounts and write a one-line summary.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '[{"id":1,"status":"paid","amount":120},{"id":2,"status":"refunded","amount":40},{"id":3,"status":"paid","amount":80.5}]' }), n('f', 'filter', 430, 150, { path: 'status', op: 'equals', equals: 'paid' }), n('a', 'aggregate', 640, 150, { operation: 'sum', path: 'amount' }), n('m', 'template', 850, 150, { template: 'Paid total: {{}}' }), n('o', 'output', 1060, 150)],
    edges: [e('t', 'i'), e('i', 'f'), e('f', 'a'), e('a', 'm'), e('m', 'o')],
  },
  {
    id: 'due-date', name: 'Due date calculator', tag: 'Dates',
    description: 'Add 14 days to a start date, then show the result as a readable date.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 230, 150, { json: '{"start":"2025-01-31"}' }), n('d', 'datetime', 450, 150, { operation: 'add', value: '{{start}}', amount: '14', unit: 'days' }), n('f', 'datetime', 680, 150, { operation: 'format', value: '{{}}', format: '%A %d %B %Y' }), n('o', 'output', 910, 150)],
    edges: [e('t', 'i'), e('i', 'd'), e('d', 'f'), e('f', 'o')],
  },
  {
    id: 'hash-email', name: 'Fingerprint an email', tag: 'Crypto',
    description: 'Lower-case and trim an email, then hash it with SHA-256 so records can be matched without storing the address.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 230, 150, { json: '{"email":" Ada@Example.com "}' }), n('m', 'template', 450, 150, { template: '{{ email.lower().strip() }}' }), n('c', 'crypto', 680, 150, { operation: 'sha256', value: '{{}}', key: '' }), n('o', 'output', 900, 150)],
    edges: [e('t', 'i'), e('i', 'm'), e('m', 'c'), e('c', 'o')],
  },
  {
    id: 'add-field', name: 'Add a field to every item', tag: 'Lists',
    description: 'One Set Fields node, switched to "each item", works out a price with tax for a whole list.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 230, 150, { json: '[{"item":"Pen","price":10},{"item":"Book","price":24.5}]' }), n('s', 'set', 460, 150, { json: '{"item":"{{item}}","price":{{price}},"price_with_tax":{{ round(price * 1.2, 2) }}}', each_item: 'true' }), n('o', 'output', 700, 150)],
    edges: [e('t', 'i'), e('i', 's'), e('s', 'o')],
  },
  {
    id: 'unwrap-list', name: 'Unwrap an API list', tag: 'Lists',
    description: 'Many APIs return a list inside an object. Split Out takes it out so the next steps see a plain list.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '{"data":{"rows":[{"id":1},{"id":2},{"id":3}]},"page":1}' }), n('s', 'split', 450, 150, { path: 'data.rows' }), n('l', 'limit', 660, 150, { count: '2', from: 'first' }), n('o', 'output', 870, 150)],
    edges: [e('t', 'i'), e('i', 's'), e('s', 'l'), e('l', 'o')],
  },
  {
    id: 'route-tickets', name: 'Route support tickets', tag: 'Branching',
    description: 'Send high, normal and any other priority down different paths with a Switch node.',
    nodes: [
      n('t', 'trigger', 20, 190), n('i', 'input', 220, 190, { json: '{"ticket":"T-77","priority":"high"}' }),
      n('s', 'switch', 440, 190, { path: 'priority', case1: 'high', case2: 'normal', case3: '', case4: '' }),
      n('h', 'template', 720, 20, { template: 'Page the on-call: {{ticket}}' }), n('q', 'template', 720, 190, { template: 'Queue for today: {{ticket}}' }), n('r', 'template', 720, 360, { template: 'Needs review: {{ticket}}' }),
      n('o1', 'output', 970, 20), n('o2', 'output', 970, 190), n('o3', 'output', 970, 360),
    ],
    edges: [e('t', 'i'), e('i', 's'), e('s', 'h', '1'), e('s', 'q', '2'), e('s', 'r', 'default'), e('h', 'o1'), e('q', 'o2'), e('r', 'o3')],
  },
  {
    id: 'webhook-reply', name: 'Webhook with a custom reply', tag: 'Webhook',
    description: 'Build a response from the request body and send it back to the caller. Activate it, then POST to its URL.',
    nodes: [n('w', 'webhook', 20, 150, { sample: '{"name":"Ada"}' }), n('s', 'set', 250, 150, { json: '{"greeting":"Hello {{body.name}}"}' }), n('r', 'respond', 480, 150, { status: '200', body: '{"message": "{{greeting}}"}' }), n('o', 'output', 710, 150)],
    edges: [e('w', 's'), e('s', 'r'), e('r', 'o')],
  },
  {
    id: 'resilient-call', name: 'API call that retries', tag: 'Reliability',
    description: 'Retry a flaky API up to three times. If it still fails, carry on with a friendly message instead of stopping. Needs internet.',
    nodes: [
      n('t', 'trigger', 20, 150), n('h', 'http', 230, 150, { url: 'https://jsonplaceholder.typicode.com/todos/1', method: 'GET', retries: '3', retry_wait: '1', on_error: 'continue' }),
      n('c', 'condition', 470, 150, { path: 'error', op: 'not_exists', equals: '' }),
      n('x', 'transform', 720, 40, { path: 'title' }), n('m', 'template', 720, 260, { template: 'Request failed: {{error}}' }),
      n('o1', 'output', 950, 40), n('o2', 'output', 950, 260),
    ],
    edges: [e('t', 'h'), e('h', 'c'), e('c', 'x', 'true'), e('c', 'm', 'false'), e('x', 'o1'), e('m', 'o2')],
  },
  {
    id: 'error-alert', name: 'Error alert workflow', tag: 'Reliability',
    description: 'Pick this as the "If this workflow fails, run" workflow on any trigger. It receives the workflow name, node and error. Swap the output for Send Message or Slack.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 230, 150, { json: '{"workflow":"Nightly import","error":"Database timed out","node":"HTTP Request","execution_id":"abc-123"}' }), n('m', 'template', 460, 150, { template: 'ALERT: {{workflow}} failed at {{node}}: {{error}}' }), n('o', 'output', 700, 150)],
    edges: [e('t', 'i'), e('i', 'm'), e('m', 'o')],
  },
  {
    id: 'rss-digest', name: 'News digest from a feed', tag: 'Network',
    description: 'Read an RSS feed, keep the latest five items and write one line for each. Needs internet.',
    nodes: [n('t', 'trigger', 20, 150), n('r', 'rss', 220, 150, { url: 'https://hnrss.org/frontpage', limit: '10' }), n('l', 'limit', 430, 150, { count: '5', from: 'first' }), n('m', 'template', 630, 150, { template: '{{title}} ({{link}})', each_item: 'true' }), n('o', 'output', 850, 150)],
    edges: [e('t', 'r'), e('r', 'l'), e('l', 'm'), e('m', 'o')],
  },
  {
    id: 'slack-alert', name: 'Slack alert', tag: 'Apps',
    description: 'Post a message to a Slack channel with an App Action node. Save a bot token on the Credentials page and choose it in the node.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '{"service":"API","status":"down"}' }), n('a', 'app', 440, 150, { service: 'slack', operation: 'post_message', credential: '', fields: '{"channel": "#alerts", "text": "{{service}} is {{status}}"}' }), n('o', 'output', 680, 150)],
    edges: [e('t', 'i'), e('i', 'a'), e('a', 'o')],
  },
  {
    id: 'python-stats', name: 'Python statistics', tag: 'Code',
    description: 'A short Python snippet works out the count, average and best score of a list.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '[{"name":"Ada","score":91},{"name":"Bob","score":48},{"name":"Cy","score":77}]' }), n('c', 'code', 450, 150, { code: "best = input[0]\nfor p in input:\n    if p['score'] > best['score']:\n        best = p\nresult = {'count': len(input), 'average': round(sum(p['score'] for p in input) / len(input), 1), 'best': best['name']}" }), n('o', 'output', 700, 150)],
    edges: [e('t', 'i'), e('i', 'c'), e('c', 'o')],
  },
  {
    id: 'combine-sources', name: 'Combine two sources', tag: 'Data',
    description: 'Two inputs run side by side, then a Merge node combines them into one record.',
    nodes: [n('t', 'trigger', 20, 150), n('a', 'input', 240, 60, { json: '{"name":"Ada"}' }), n('b', 'input', 240, 250, { json: '{"plan":"pro"}' }), n('m', 'merge', 480, 150, { mode: 'combine' }), n('o', 'output', 700, 150)],
    edges: [e('t', 'a'), e('t', 'b'), e('a', 'm'), e('b', 'm'), e('m', 'o')],
  },
  {
    id: 'new-posts', name: 'Alert on new feed posts', tag: 'New items',
    description: 'Runs once for each new item in an RSS feed. Activate it to start watching; Run workflow tries it with a sample post.',
    nodes: [n('p', 'poll_in', 20, 150, { source: 'rss', url: 'https://hnrss.org/newest', minutes: '15' }), n('m', 'template', 260, 150, { template: 'New post: {{title}} {{link}}' }), n('o', 'output', 500, 150)],
    edges: [e('p', 'm'), e('m', 'o')],
  },
  {
    id: 'file-upload', name: 'File upload form', tag: 'Webhook',
    description: 'A public form with a file field. Activate it, open its URL, and each upload is stored and described to the workflow. Run workflow tries it with a sample file.',
    nodes: [n('w', 'webhook', 20, 150, { accept_files: 'true', form_fields: '[{"name":"name","label":"Your name","type":"text"},{"name":"document","label":"Your document","type":"file"}]', sample: '{"name":"Ada","files":[{"field":"document","filename":"cv.pdf","content_type":"application/pdf","size":1234,"path":"uploads/20260101/ab12cd34-cv.pdf"}]}' }), n('m', 'template', 330, 150, { template: '{{body.name}} sent {{body.files.0.filename}} ({{body.files.0.size}} bytes), saved as {{body.files.0.path}}' }), n('o', 'output', 640, 150)],
    edges: [e('w', 'm'), e('m', 'o')],
  },
  {
    id: 'ai-agent', name: 'AI agent with a calculator', tag: 'AI',
    description: 'A model that decides when to use a calculator and the clock before it answers. Needs an AI credential (a local model with Ollama is free).',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '{"topic":"a 49.90 order"}' }), n('a', 'agent', 440, 150, { credential: '', system: 'You are a careful assistant. Use the tools when they help, and answer briefly.', prompt: 'Work out what {{topic}} would cost with 20% tax added.', tools: 'calculator,current_datetime', workflows: '', max_steps: '4' }), n('o', 'output', 700, 150)],
    edges: [e('t', 'i'), e('i', 'a'), e('a', 'o')],
  },
  {
    id: 'approval-gate', name: 'Approval gate', tag: 'Approval',
    description: 'Pauses until someone approves or rejects, using the links in the message or the buttons on the Executions page. Send the message with Slack, ntfy or email. Run workflow tries it.',
    nodes: [n('t', 'trigger', 20, 190), n('i', 'input', 210, 190, { json: '{"request":"Buy a laptop","amount":1200}' }), n('s', 'set', 410, 190, { json: '{"request": "{{request}}", "amount": {{amount}}, "message": "Approve {{request}}: {{run.approve_url}}  Reject: {{run.reject_url}}"}' }), n('w', 'callback', 630, 190, { seconds: '1', unit: 'days', on_timeout: 'continue' }), n('c', 'condition', 850, 190, { path: 'decision', op: 'equals', equals: 'approve' }), n('y', 'template', 1090, 60, { template: 'Approved: {{data.request}}' }), n('x', 'template', 1090, 320, { template: 'Not approved ({{decision}}): {{data.request}}' }), n('o1', 'output', 1330, 60), n('o2', 'output', 1330, 320)],
    edges: [e('t', 'i'), e('i', 's'), e('s', 'w'), e('w', 'c'), e('c', 'y', 'true'), e('c', 'x', 'false'), e('y', 'o1'), e('x', 'o2')],
  },
  {
    id: 'ntfy-alert', name: 'Phone alert with ntfy', tag: 'Apps',
    description: 'Send a free push notification to your phone with the ntfy app. Pick a long random topic name and subscribe to it in the app. Needs internet.',
    nodes: [n('t', 'trigger', 20, 150), n('i', 'input', 220, 150, { json: '{"message":"Backup finished"}' }), n('a', 'app', 440, 150, { service: 'ntfy', operation: 'publish', credential: '', fields: '{"topic": "change-me-to-a-long-random-name", "message": "{{message}}", "title": "FlowPilot", "priority": "3"}' }), n('o', 'output', 700, 150)],
    edges: [e('t', 'i'), e('i', 'a'), e('a', 'o')],
  },
];

export const templateGroups = ['All', 'Getting started', 'Lists and data', 'Webhooks and APIs', 'Reliability', 'Apps'] as const;
const groupById: Record<string, (typeof templateGroups)[number]> = {
  greeting: 'Getting started', 'order-triage': 'Getting started', 'route-tickets': 'Getting started',
  'loop-list': 'Lists and data', 'csv-report': 'Lists and data', 'top-n': 'Lists and data', 'clean-contacts': 'Lists and data', 'sales-summary': 'Lists and data', 'due-date': 'Lists and data',
  'hash-email': 'Lists and data', 'add-field': 'Lists and data', 'unwrap-list': 'Lists and data', 'python-stats': 'Lists and data', 'combine-sources': 'Lists and data',
  'fetch-todo': 'Webhooks and APIs', 'webhook-receiver': 'Webhooks and APIs', 'scheduled-check': 'Webhooks and APIs', 'post-api': 'Webhooks and APIs', 'webhook-reply': 'Webhooks and APIs', 'rss-digest': 'Webhooks and APIs', 'new-posts': 'Webhooks and APIs', 'ntfy-alert': 'Apps', 'approval-gate': 'Reliability', 'ai-agent': 'Apps', 'file-upload': 'Webhooks and APIs',
  'resilient-call': 'Reliability', 'error-alert': 'Reliability', 'slack-alert': 'Apps',
};
export const templateGroup = (id: string) => groupById[id] ?? 'Getting started';
