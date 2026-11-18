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
page.on('console', m => { if (m.type() === 'error' && !/fonts\.googleapis|ERR_/.test(m.text())) pageErrors.push(m.text()); });

let passed = 0, failed = 0;
const check = async (name, fn) => {
  try { await fn(); passed++; console.log('  PASS', name); }
  catch (e) { failed++; console.log('  FAIL', name, '\n       ', String(e.message).split('\n')[0]); if (SHOTS) await page.screenshot({ path: `${SHOTS}/fail-${name.replace(/\W+/g, '-')}.png` }); }
};
const shot = async n => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${n}.png` }); };
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

await check('dashboard renders hero, stats and seeded workflow', async () => {
  await expectText(page.locator('h1'), /Build smarter/);
  await page.getByTestId('workflow-row').first().waitFor();
  if ((await page.locator('.stat').count()) !== 4) throw new Error('expected 4 stat cards');
  await shot('01-dashboard');
});

await check('theme toggle switches to light and persists', async () => {
  await page.getByLabel('Toggle theme').click();
  if ((await page.locator('html').getAttribute('data-theme')) !== 'light') throw new Error('not light');
  await shot('02-dashboard-light');
  await page.reload();
  if ((await page.locator('html').getAttribute('data-theme')) !== 'light') throw new Error('theme not persisted');
  await page.getByLabel('Toggle theme').click();
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
  await page.locator('.react-flow__node', { hasText: 'HTTP GET' }).click();
  await page.getByLabel('Public HTTPS URL').fill('https://127.0.0.1/blocked');
  await page.getByRole('button', { name: /Run workflow/ }).click();
  await expectText(runPanel(), /Execution failed/);
  await expectText(runPanel().locator('pre'), /Local\/private|public/i);
  if ((await page.locator('.fp-node-badge.error').count()) !== 1) throw new Error('expected 1 error badge');
  await shot('05-failed-run');
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
