// End-to-end browser test. Requires the backend (8000) and frontend (5173) to be running
// and Google Chrome or Microsoft Edge installed. It creates and deletes workflows, so point the
// backend at a throwaway database first:  FLOWPILOT_DB=/tmp/e2e.db uvicorn app.main:app   Run with:  npm run test:e2e
import { chromium } from 'playwright-core';
import { existsSync, mkdirSync } from 'node:fs';

const BASE = process.env.FLOWPILOT_URL || 'http://127.0.0.1:5173';
const SHOTS = process.env.SHOT_DIR || '';
const candidates = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];
const executablePath = candidates.find(existsSync);
if (!executablePath) { console.error('No Chrome/Edge found'); process.exit(2); }
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({ executablePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
const pageErrors = [];
page.on('pageerror', e => pageErrors.push(e.message));
page.on('console', m => { if (m.type() === 'error' && !/fonts\.googleapis|ERR_|status of (400|401)/.test(m.text())) pageErrors.push(m.text()); });

let passed = 0, failed = 0;
const check = async (name, fn) => {
  try { await fn(); passed++; console.log('  PASS', name); }
  catch (e) { failed++; console.log('  FAIL', name, '\n       ', String(e.message).split('\n')[0]); if (SHOTS) await page.screenshot({ path: `${SHOTS}/fail-${name.replace(/\W+/g, '-')}.png` }); }
};
const shot = async n => { if (SHOTS) await page.waitForTimeout(450); if (SHOTS) await page.screenshot({ path: `${SHOTS}/${n}.png` }); };
const expectText = async (loc, re, timeout = 8000) => {
  const end = Date.now() + timeout; let t = '';
  while (Date.now() < end) {
    try { t = await loc.first().innerText({ timeout: 500 }); if (re.test(t)) return; } catch { /* retry */ }
    await page.waitForTimeout(100);
  }
  throw new Error(`text "${t}" !~ ${re}`);
};
const runPanel = () => page.getByTestId('run-panel');

await page.goto(BASE);
await page.evaluate(() => localStorage.clear());
await page.goto(BASE);

const U = 'e2e-owner', PW = 'e2e-password-123';
const fillAuth = async (user, pw, confirm) => {
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password', { exact: true }).fill(pw);
  if (confirm !== undefined) await page.getByLabel('Confirm password').fill(confirm);
};

await check('first visit shows account setup with branded tab title and icon', async () => {
  await expectText(page.locator('h1'), /Create your account/);
  const title = await page.title();
  if (!/FlowPilot/.test(title)) throw new Error('tab title: ' + title);
  const href = await page.locator('link[rel=icon]').getAttribute('href');
  if (href !== '/favicon.svg') throw new Error('favicon href: ' + href);
  const res = await page.request.get(BASE + '/favicon.svg');
  if (res.status() !== 200 || !(res.headers()['content-type'] || '').includes('svg')) throw new Error('favicon not served as svg');
  await shot('00-setup');
});

await check('API is locked while signed out', async () => {
  const status = await page.evaluate(async () => (await fetch('/api/workflows')).status);
  if (status !== 401) throw new Error('expected 401, got ' + status);
});

await check('setup validates matching passwords', async () => {
  await fillAuth(U, PW, 'different-password');
  await page.getByRole('button', { name: /Create account/ }).click();
  await expectText(page.getByRole('alert'), /do not match/);
});

await check('setup creates the account and signs in', async () => {
  await fillAuth(U, PW, PW);
  await page.getByRole('button', { name: /Create account/ }).click();
  await expectText(page.locator('h1'), /^Workflows$/);
  await expectText(page.locator('.crumb strong'), /Overview/);
  if (!/^Overview - FlowPilot$/.test(await page.title())) throw new Error('page title: ' + (await page.title()));
});

await check('sign out returns to the login screen', async () => {
  await page.getByRole('button', { name: /Sign out/ }).click();
  await expectText(page.locator('h1'), /Welcome back/);
  if (!/^Sign in - FlowPilot$/.test(await page.title())) throw new Error('title: ' + (await page.title()));
  await shot('00-login');
});

await check('login rejects a wrong password', async () => {
  await fillAuth(U, 'wrong-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expectText(page.getByRole('alert'), /Incorrect username or password/);
});

await check('password visibility toggle', async () => {
  await page.getByLabel('Show password').click();
  if ((await page.getByLabel('Password', { exact: true }).getAttribute('type')) !== 'text') throw new Error('not revealed');
  await page.getByLabel('Hide password').click();
});

await check('login succeeds and the session survives a reload', async () => {
  await fillAuth(U, PW);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expectText(page.locator('h1'), /^Workflows$/);
  await page.reload();
  await expectText(page.locator('h1'), /^Workflows$/);
});

await check('dashboard renders hero, stats and seeded workflow', async () => {
  await expectText(page.locator('h1'), /^Workflows$/);
  await page.getByTestId('workflow-row').first().waitFor();
  if ((await page.locator('.stat').count()) !== 4) throw new Error('expected 4 stat cards');
  await shot('01-dashboard');
});

await check('theme defaults to light, toggles to dark and persists', async () => {
  const theme = () => page.locator('html').getAttribute('data-theme');
  if ((await theme()) !== 'light') throw new Error('default theme should be light');
  await page.getByLabel('Toggle theme').click();
  if ((await theme()) !== 'dark') throw new Error('not dark after toggle');
  await shot('02-dashboard-dark');
  await page.reload();
  if ((await theme()) !== 'dark') throw new Error('theme not persisted');
  await page.getByLabel('Toggle theme').click();
  if ((await theme()) !== 'light') throw new Error('not light after second toggle');
});

await check('search filters workflows', async () => {
  await page.getByLabel('Search workflows').fill('zzz-no-match');
  await expectText(page.locator('.empty'), /No workflows match/);
  await page.getByLabel('Search workflows').fill('');
});

await check('open seeded workflow and run it', async () => {
  await page.locator('.flow-meta strong', { hasText: /^My first automation$/ }).click();
  await page.locator('.react-flow__node').first().waitFor();
  if ((await page.locator('.react-flow__node').count()) !== 4) throw new Error('expected 4 nodes');
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel(), /Execution successful/);
  await expectText(runPanel().locator('pre'), /In Transit/);
  if ((await page.locator('.fp-node-badge.success').count()) !== 4) throw new Error('expected 4 success badges');
  await shot('03-editor-run');
});

await check('select node, edit path, rerun gives new output', async () => {
  await page.locator('.react-flow__node', { hasText: 'Extract Field' }).click();
  await page.getByTestId('inspector').waitFor();
  await page.getByLabel(/Field path/).fill('shipment');
  await expectText(page.locator('.sub'), /Unsaved/);
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /FP-1001/);
  await expectText(page.locator('.sub'), /All changes saved/);
});

await check('Ctrl+S saves', async () => {
  await page.getByLabel(/Field path/).fill('status');
  await expectText(page.locator('.sub'), /Unsaved/);
  await page.keyboard.press('Control+s');
  await expectText(page.locator('.sub'), /All changes saved/);
});

await check('node library filter and adding a node', async () => {
  if (!(await page.getByLabel('Filter nodes').isVisible())) await page.getByRole('button', { name: /Add nodes/ }).click();
  await page.getByLabel('Filter nodes').fill('delay');
  if ((await page.locator('.catalog-entry').count()) !== 1) throw new Error('filter failed');
  const before = await page.locator('.react-flow__node').count();
  await page.getByTestId('add-delay').click();
  await page.waitForFunction(n => document.querySelectorAll('.react-flow__node').length === n + 1, before);
  await page.getByLabel('Filter nodes').fill('');
  await page.getByTestId('inspector').getByRole('button', { name: /Delete node/ }).click();
  await page.waitForFunction(n => document.querySelectorAll('.react-flow__node').length === n, before);
});

await check('branching template routes to the correct branch', async () => {
  await page.getByLabel('Back to overview').click();
  await page.getByTestId('template-order-triage').click();
  await page.locator('.react-flow__node').first().waitFor();
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel(), /Execution successful/);
  await expectText(runPanel().locator('pre'), /PRIORITY: order A-1042 \(250\)/);
  if ((await page.locator('.fp-node.state-skipped').count()) !== 2) throw new Error('expected 2 skipped nodes (untaken branch)');
  await shot('04-branching');
  // flip the threshold so the false branch is taken
  await page.locator('.react-flow__node', { hasText: 'Compare Value' }).click();
  await page.getByLabel('Compare to').fill('1000');
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Standard: order A-1042/);
});

await check('text template and http templates load; http failure shows error', async () => {
  await page.getByLabel('Back to overview').click();
  await page.getByTestId('template-greeting').click();
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Hello Ada from London!/);
  await page.locator('.react-flow__node', { hasText: 'Text Template' }).click();
  await page.getByLabel('Template', { exact: false }).first().fill('x');
  await page.getByLabel('Back to overview').click();
  await page.getByTestId('template-fetch-todo').click();
  await page.locator('.react-flow__node', { hasText: 'HTTP Request' }).click();
  await page.getByLabel('Public HTTPS URL').fill('https://127.0.0.1/blocked');
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel(), /Execution failed/);
  await expectText(runPanel().locator('pre'), /Local\/private|public/i);
  if ((await page.locator('.fp-node-badge.error').count()) !== 1) throw new Error('expected 1 error badge');
  await shot('05-failed-run');
});

const activeSwitch = () => page.getByRole('switch', { name: 'Workflow active' });
const waitChecked = (want) => page.waitForFunction(w => document.querySelector('[aria-label="Workflow active"]')?.getAttribute('aria-checked') === String(w), want);
const toast = () => page.locator('.toast');

await check('dashboard marks manual workflows and disables their activate switch', async () => {
  await page.getByRole('button', { name: /^Overview/ }).click();
  const row = page.getByTestId('workflow-row').filter({ hasText: 'My first automation' }).first();
  await expectText(row.locator('.trig-chip'), /Manual/);
  if (!(await page.getByLabel('Activate My first automation', { exact: true }).isDisabled())) throw new Error('switch should be disabled');
});

await check('webhook: test run, activate, public call, history, rotate, deactivate', async () => {
  await page.getByTestId('template-webhook-receiver').click();
  await page.locator('.react-flow__node').first().waitFor();
  if (await activeSwitch().isDisabled()) throw new Error('webhook workflow should be activatable');
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /New message from Ada: Hello from a webhook/);

  await page.locator('.react-flow__node', { hasText: 'Webhook Trigger' }).click();
  const url = await page.getByTestId('webhook-url').innerText();
  if (!/\/api\/hooks\/[\w-]{20,}$/.test(url)) throw new Error('unexpected webhook url: ' + url);
  await shot('08-webhook-inspector');

  const outsider = await browser.newContext(); // no cookies: simulates another service calling in
  let res = await outsider.request.post(url, { data: { name: 'Grace', message: 'Hi there' } });
  if (res.status() !== 404) throw new Error('inactive webhook should 404, got ' + res.status());

  await activeSwitch().click();
  await waitChecked(true);
  res = await outsider.request.post(url, { data: { name: 'Grace', message: 'Hi there' } });
  const body = await res.json();
  if (res.status() !== 200 || body.status !== 'success') throw new Error('webhook call failed: ' + res.status());
  if ('output' in body) throw new Error('webhook response must not leak output');

  await page.getByRole('button', { name: /^Executions/ }).click();
  const row = page.getByTestId('history-row').first();
  await expectText(row.locator('.trig-chip'), /Webhook/);
  await row.locator('summary').click();
  await expectText(row.locator('pre').first(), /New message from Grace: Hi there/);

  await page.getByRole('button', { name: /^Workflow Studio/ }).click();
  await page.locator('.react-flow__node', { hasText: 'Webhook Trigger' }).click();
  page.once('dialog', d => d.accept());
  await page.getByRole('button', { name: /Generate new URL/ }).click();
  await page.waitForFunction(old => document.querySelector('[data-testid=webhook-url]')?.textContent !== old, url);
  res = await outsider.request.post(url, { data: {} });
  if (res.status() !== 404) throw new Error('old URL should stop working after rotation, got ' + res.status());

  await activeSwitch().click();
  await waitChecked(false);
  const fresh = await page.getByTestId('webhook-url').innerText();
  res = await outsider.request.post(fresh, { data: {} });
  if (res.status() !== 404) throw new Error('deactivated webhook should 404, got ' + res.status());
  await outsider.close();
});

await check('schedule: validation, activation and next run time', async () => {
  await page.getByLabel('Back to overview').click();
  await page.getByTestId('template-scheduled-check').click();
  await page.locator('.react-flow__node').first().waitFor();
  await page.locator('.react-flow__node', { hasText: 'Schedule Trigger' }).click();
  await page.getByLabel('Repeat').selectOption('daily');
  await page.getByLabel(/^Time/).waitFor();
  await page.getByLabel('Repeat').selectOption('interval');
  await page.getByLabel(/Minutes between runs/).fill('0');
  await activeSwitch().click();
  await expectText(toast(), /between 1 and 10080/);
  if ((await activeSwitch().getAttribute('aria-checked')) !== 'false') throw new Error('should stay inactive');
  await page.getByLabel(/Minutes between runs/).fill('5');
  await activeSwitch().click();
  await waitChecked(true);
  await expectText(page.locator('.sub'), /Next run/);
  await shot('09-schedule-active');
  await page.getByLabel('Back to overview').click();
  const row = page.getByTestId('workflow-row').filter({ hasText: 'Hourly API check' }).first();
  await expectText(row.locator('.trig-chip'), /Schedule/);
  await expectText(row.getByRole('switch'), /Active/);
  await row.getByRole('switch').click();
  await expectText(row.getByRole('switch'), /Off/);
});

await check('HTTP node: method, headers and body fields', async () => {
  await page.getByTestId('template-post-api').click();
  await page.locator('.react-flow__node').first().waitFor();
  await page.locator('.react-flow__node', { hasText: 'HTTP Request' }).click();
  const method = page.getByLabel('Method');
  if ((await method.inputValue()) !== 'POST') throw new Error('template should use POST');
  await page.getByLabel(/^Body/).waitFor();
  if (!/X-Source/.test(await page.getByLabel(/^Headers/).inputValue())) throw new Error('headers missing');
  await method.selectOption('GET');
  if ((await page.getByLabel(/^Body/).count()) !== 0) throw new Error('body field should hide for GET');
  await method.selectOption('DELETE');
  if ((await page.getByLabel(/^Body/).count()) !== 0) throw new Error('body field should hide for DELETE');
  await shot('10-http-node');
});

const goOverview = () => page.getByRole('button', { name: /^Overview/ }).click();
const node = text => page.locator('.react-flow__node', { hasText: text });
const nodeCount = () => page.locator('.react-flow__node').count();
const waitNodes = n => page.waitForFunction(c => document.querySelectorAll('.react-flow__node').length === c, n);
const openLibrary = async () => { if (!(await page.getByLabel('Filter nodes').isVisible())) await page.getByRole('button', { name: /Add nodes/ }).click(); };

await check('credentials: saved encrypted, never shown again, selectable in a node, deletable', async () => {
  await page.getByRole('button', { name: /^Credentials/ }).click();
  await expectText(page.locator('h1'), /^Credentials$/);
  await page.getByTestId('cred-name').fill('API_KEY');
  await page.getByTestId('cred-value').fill('abc'); // too short: the browser blocks the submit
  await page.getByTestId('cred-save').click();
  if ((await page.getByTestId('cred-row').count()) !== 0) throw new Error('a 3 character secret should be rejected');
  await page.getByTestId('cred-value').fill('sk-live-topsecret-value');
  await page.getByTestId('cred-save').click();
  await expectText(page.getByTestId('cred-row'), /API_KEY/);
  if (/topsecret/.test(await page.content())) throw new Error('the secret value must never appear on the page');
  const leaked = await page.evaluate(async () => (await fetch('/api/credentials')).text());
  if (leaked.includes('topsecret')) throw new Error('the API returned the secret value');
  await expectText(page.getByRole('button', { name: /^Credentials/ }), /1$/);
  await shot('11-credentials');

  await goOverview();
  await page.getByTestId('template-post-api').click();
  await node('HTTP Request').click();
  await page.getByLabel('Authentication').selectOption('bearer');
  await page.getByLabel(/^Credential/).selectOption('API_KEY');
  await node('Send data to an API').count();

  await page.getByRole('button', { name: /^Credentials/ }).click();
  page.once('dialog', d => d.accept());
  await page.getByLabel('Delete credential API_KEY').click();
  await expectText(page.locator('.empty'), /No credentials yet/);
});

await check('loop: runs the body once per item and numbers the iterations', async () => {
  await goOverview();
  await page.getByTestId('template-loop-list').click();
  await page.locator('.react-flow__node').first().waitFor();
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Ada scored 91[\s\S]*Bob scored 48[\s\S]*Cy scored 77/);
  if ((await runPanel().locator('.iter').count()) !== 3) throw new Error('expected 3 iteration chips');
  await node('Text Template').click();
  await expectText(page.getByTestId('data-panel'), /3 runs/);
  await shot('12-loop');
});

await check('CSV, filter and loop pipeline', async () => {
  await goOverview();
  await page.getByTestId('template-csv-report').click();
  await page.locator('.react-flow__node').first().waitFor();
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Ada: 90[\s\S]*Cy: 75/);
  if (/Bob/.test(await runPanel().locator('pre').innerText())) throw new Error('Bob should have been filtered out');
});

await check('data panel shows input and output, and pinning freezes a node', async () => {
  await goOverview();
  await page.getByTestId('template-greeting').click();
  await page.locator('.react-flow__node').first().waitFor();
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Hello Ada from London!/);
  await node('Text Template').click();
  await expectText(page.getByTestId('data-panel'), /Hello Ada from London!/);
  await expectText(page.getByTestId('data-panel'), /London/);
  await page.getByRole('button', { name: /Pin last output/ }).click();
  await node('Text Template').locator('.fp-pin').waitFor();
  await node('JSON Input').click();
  await page.getByLabel('JSON value').fill('{"name":"Zed","city":"Paris"}');
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Hello Ada from London!/); // pinned: the new data is ignored
  await expectText(runPanel(), /pinned/);
  await shot('13-pinned');
  await node('Text Template').click();
  await page.getByRole('button', { name: /Unpin data/ }).click();
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel().locator('pre'), /Hello Zed from Paris!/);
});

await check('undo, redo, copy, paste, duplicate and tidy', async () => {
  await goOverview();
  await page.getByTestId('template-greeting').click();
  await page.locator('.react-flow__node').first().waitFor();
  await waitNodes(4);
  await node('Text Template').click();
  await page.keyboard.press('Control+c');
  await page.keyboard.press('Control+v');
  await waitNodes(5);
  await page.keyboard.press('Control+z');
  await waitNodes(4);
  await page.keyboard.press('Control+y');
  await waitNodes(5);
  await page.getByLabel('Undo').click();
  await waitNodes(4);
  await page.getByLabel('Redo').click();
  await waitNodes(5);
  await page.getByLabel('Undo').click();
  await waitNodes(4);
  await node('Text Template').click();
  await page.getByRole('button', { name: /Duplicate node/ }).click();
  await waitNodes(5);
  await page.getByLabel('Undo').click();
  await waitNodes(4);
  await node('Manual Trigger').click();
  await page.keyboard.press('Control+c');
  await page.keyboard.press('Control+v');
  await expectText(toast(), /only have one trigger/);
  if ((await nodeCount()) !== 4) throw new Error('a second trigger must not be pasted');
  await page.getByLabel('Tidy up layout').click();
  await page.waitForTimeout(600);
  const ys = await page.evaluate(() => [...document.querySelectorAll('.react-flow__node')].map(n => Math.round(n.getBoundingClientRect().top)));
  if (Math.max(...ys) - Math.min(...ys) > 4) throw new Error('tidy should line a chain up on one row: ' + ys.join(','));
  await expectText(page.locator('.sub'), /Unsaved/);
});

await check('schedule supports cron expressions with presets and validation', async () => {
  await goOverview();
  await page.getByTestId('template-scheduled-check').click();
  await page.locator('.react-flow__node').first().waitFor();
  await node('Schedule Trigger').click();
  await page.getByLabel('Repeat').selectOption('cron');
  await page.getByRole('button', { name: 'Weekdays 9:00' }).click();
  if ((await page.getByLabel('Cron expression', { exact: true }).inputValue()) !== '0 9 * * 1-5') throw new Error('preset not applied');
  await page.getByLabel('Cron expression', { exact: true }).fill('every day');
  await activeSwitch().click();
  await expectText(toast(), /cron/i);
  if ((await activeSwitch().getAttribute('aria-checked')) !== 'false') throw new Error('invalid cron must not activate');
  await page.getByLabel('Cron expression', { exact: true }).fill('*/10 * * * *');
  await activeSwitch().click();
  await waitChecked(true);
  await expectText(page.locator('.sub'), /Next run/);
  await activeSwitch().click();
  await waitChecked(false);
});

await check('new nodes expose their settings: email, message, merge, filter, sub-workflow', async () => {
  await goOverview();
  await page.getByTestId('template-greeting').click();
  await page.locator('.react-flow__node').first().waitFor();
  await openLibrary();
  await page.getByTestId('add-email').click();
  await page.getByLabel(/^SMTP credential/).waitFor();
  await page.getByLabel(/^To /).waitFor();
  await page.getByTestId('add-notify').click();
  await page.getByLabel(/^Service/).selectOption('discord');
  await page.getByLabel('Webhook URL').waitFor();
  await page.getByTestId('add-merge').click();
  await page.getByLabel(/^Mode/).selectOption('combine');
  await page.getByTestId('add-filter').click();
  await page.getByLabel(/^Keep items where it/).waitFor();
  await page.getByTestId('add-subflow').click();
  const options = await page.getByLabel(/^Workflow to run/).locator('option').allInnerTexts();
  if (options.length < 2) throw new Error('sub-workflow picker should list other workflows: ' + options.join('|'));
  await shot('14-new-nodes');
});

await check('code, switch, stop-and-fail, retries and error workflow settings work in the editor', async () => {
  await goOverview();
  await page.getByTestId('template-greeting').click();
  await page.locator('.react-flow__node').first().waitFor();
  await openLibrary();
  await page.getByTestId('add-code').click();
  await page.getByLabel('Python code').fill("result = {'shout': input['name'].upper()}");
  await page.getByLabel('Retry').selectOption('2');
  await page.getByLabel('After the last try').selectOption('continue');
  await page.getByTestId('add-switch').click();
  await page.getByLabel('Field path').fill('name');
  await page.getByLabel('Path 1 when it equals').fill('Ada');
  await page.getByTestId('add-fail').click();
  await page.getByLabel('Error message').waitFor();
  await node('Manual Trigger').click();
  await page.getByLabel('If this workflow fails, run').waitFor();
  await node('Code').click();
  await page.getByLabel('Python code').waitFor();
  if ((await page.getByLabel('Retry').inputValue()) !== '2') throw new Error('retry setting was not kept');
  await shot('15-logic-nodes');
});

await check('executions page lists runs, filters and expands', async () => {
  await page.getByRole('button', { name: /^Executions/ }).click();
  await page.getByTestId('history-row').first().waitFor();
  await page.getByRole('tab', { name: 'failed' }).click();
  const failedRows = await page.getByTestId('history-row').count();
  if (failedRows < 1) throw new Error('no failed rows');
  await page.getByRole('tab', { name: 'success' }).click();
  if ((await page.locator('.status-chip.fail').count()) !== 0) throw new Error('failed row shown under success filter');
  await page.getByRole('tab', { name: 'all' }).click();
  await page.getByTestId('history-row').first().locator('summary').click();
  await page.getByTestId('history-row').first().locator('pre').first().waitFor();
  await shot('06-executions');
});

await check('duplicate, then delete workflow from dashboard', async () => {
  await page.getByRole('button', { name: /^Overview/ }).click();
  const before = await page.getByTestId('workflow-row').count();
  await page.getByLabel('Duplicate My first automation', { exact: true }).click();
  await page.waitForFunction(n => document.querySelectorAll('[data-testid=workflow-row]').length === n + 1, before);
  page.once('dialog', d => d.accept());
  await page.getByLabel('Delete My first automation (copy)', { exact: true }).click();
  await page.waitForFunction(n => document.querySelectorAll('[data-testid=workflow-row]').length === n, before);
});

await check('export downloads JSON and import recreates a workflow', async () => {
  await page.locator('.flow-meta strong', { hasText: /^My first automation$/ }).click();
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /Export/ }).click()]);
  const path = await dl.path();
  await page.getByLabel('Back to overview').click();
  const before = await page.getByTestId('workflow-row').count();
  await page.locator('input[type=file]').first().setInputFiles(path);
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length > 0);
  await page.getByLabel('Back to overview').click();
  await page.waitForFunction(n => document.querySelectorAll('[data-testid=workflow-row]').length === n + 1, before);
});

await check('clear execution history', async () => {
  await page.getByRole('button', { name: /^Executions/ }).click();
  page.once('dialog', d => d.accept());
  await page.getByRole('button', { name: /Clear/ }).click();
  await expectText(page.locator('.empty'), /No executions yet/);
});

await check('settings page renders', async () => {
  await page.getByRole('button', { name: /^Settings/ }).click();
  await expectText(page.locator('h1'), /Settings/);
  await shot('07-settings');
});

await check('no uncaught browser errors', async () => {
  if (pageErrors.length) throw new Error(pageErrors.join(' | '));
});

await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
