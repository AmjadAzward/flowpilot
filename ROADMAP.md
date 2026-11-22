# FlowPilot Product Roadmap

This file describes planned functionality, **not current features**.

## Shipped
### v0.1 starter
- Full-stack local web app (React / TypeScript / FastAPI / SQLite)
- Visual node editor, manual execution, workflow CRUD, import/export, logs
- Local Windows startup script

### v0.2
- True boolean branch routing from Compare Value (true / false outputs)
- Text Template and Delay nodes, seven comparison operators
- Fan-out from a node to several children
- Per-step timings, per-node run badges on the canvas
- Workflow duplication, execution filtering and clearing, starter templates
- Light and dark themes, unsaved-change indicator, Ctrl+S
- Redesigned light/dark interface with an orange brand theme, branded tab title and icon
- Owner login with hashed passwords, sessions, lockout and password reset script
- 65 API tests and a 23-check browser end-to-end suite

### v0.3
- Schedule trigger (interval or daily) with a background scheduler and missed-run catch-up
- Webhook trigger with secret URLs, rotation, rate and size limits
- Active/inactive switch per workflow, trigger and next-run shown in the dashboard
- HTTP Request node: GET, POST, PUT, PATCH, DELETE with headers, body and `{{placeholders}}`
- Three more templates, execution history shows what triggered each run
- 126 API tests and a 27-check browser suite

### v0.51
- Slowest steps: a Timings button on each workflow lists its steps by typical time (the median over its latest 50 finished runs), with the worst time, how often each step ran and how often it failed. Dry runs and unfinished runs are left out, and waiting steps do not count towards the time. It reads stored runs only; `GET /api/workflows/{id}/step-timings?runs=N` (1 to 500)
- 1422 API tests and a 93-check browser suite. The older cancel-wait browser check did not fail in this release's runs, but it is still not fixed

### v0.50
- Workflow health on the dashboard: a workflow whose last day looks worse than its own previous week gets a badge (failing, failing more than usual, or slower than usual), with the reason on hover. It is calculated from stored runs and nothing is sent. It needs at least 3 runs in the last day, so one bad run never raises it; "slower" needs a baseline of 3 runs, at least twice the usual typical run time and at least half a second of difference. Dry runs, waiting runs and running runs are ignored
- This only shows a badge. It does not notify anyone; sending alerts is still to do
- 1409 API tests and a 92-check browser suite. One older browser check (cancelling a waiting run) failed once in about eight full runs and passed on re-run; it looks like a timing race in that check and has not been fixed yet

### v0.49
- The Credentials page shows which workflows use each credential ("used by 2 workflows", or "unused" when nothing refers to it), and deleting one that is in use warns first and names the workflows. Credentials of other projects, and workflows you cannot see, are not named. Values are never returned
- A credential counts as used when a workflow refers to it with `{{cred.NAME}}` or picks it in a credential field. A credential used only by something outside the saved workflows (for example a script calling the API directly) will show as unused
- 1392 API tests and a 91-check browser suite

### v0.48
- Notes on runs: add a short note (up to 500 characters) to any run, such as "customer confirmed fixed". It shows next to the run in the list and is found by the run search. Viewers can read notes but not write them; people cannot note runs of workflows they cannot see
- Checked on a real Postgres as well as SQLite (the new API tests for notes, search, compare, connections and AI usage: 44 passed on Postgres)
- 1384 API tests and a 90-check browser suite

### v0.47
- A Connections panel on each workflow: which workflows it runs (Run Workflow nodes, AI Agent tools, error handlers), which workflows run it, whether it is the shared error workflow, and which credentials it uses (flagging names that are not saved yet). Links to workflows it points at that no longer exist
- Deleting a workflow (one or several) now warns first when another workflow still uses it or it is the shared error workflow. The warning is advice only; deleting still works
- People only see connections to workflows they can see; others are counted, not named. Credential values are never returned
- 1375 API tests and an 89-check browser suite. Connections are read from the saved workflow definitions, so a workflow name typed into a node's text is not detected

### v0.46
- Search inside past runs: type an order number, an email or an error message on the Executions page and see which runs contained it, with a snippet around the match. It scans the newest 2,000 runs (the page says how many it searched) and shows up to 50. Run data never holds credential values, so they cannot be found this way. People only find runs of workflows they can see
- 1362 API tests and an 88-check browser suite. This is a text scan, not an index: it is fast for thousands of runs but is not meant for millions

### v0.45
- Compare two runs step by step: on the Executions page, pick another run of the same workflow and see which steps behaved differently (result, error or output), with both outputs side by side and the first difference named. Useful for "it worked yesterday, what changed"
- 1350 API tests and an 87-check browser suite. Compared against real runs of a small offline workflow; outputs shown are shortened to 1,500 characters

### v0.44
- A run marks its slowest step (shown when a run has three or more timed steps and the slowest took at least 50 ms)
- Export on a workflow now includes its saved tests, and importing that file restores them before the workflow opens. Invalid tests in a file are skipped without losing the workflow
- 1340 API tests and an 86-check browser suite. The slowest-step marker is checked against a stubbed run response; the export and import check runs against the real test backend

### v0.43
- Every run that used AI records how many calls and tokens it used (shown in the run panel and on the Executions page, and summed in the run stats). Tokens come from what the AI service reports, or are estimated when it reports nothing
- Workflow backups, restores and duplicates now carry the saved test cases (before this, restoring a backup silently dropped them)
- 1340 API tests and an 85-check browser suite. The AI usage display in the browser is checked against a stubbed run response, since the test backend has no model

### v0.42
- Saved test cases per workflow (sample input, expected output or expected failure), run together as a dry run
- Replay all recent failed runs in one click; each run is replayed once
- Typing `{{` suggests fields from the previous step's real data (after one run)
- Limit AI tokens per run, and a time limit per step. The step limit stops waiting for a slow step; it cannot stop a request that was already sent
- 1330 API tests and an 84-check browser suite, which also passes against a real Postgres

### v0.41
- Failed runs: replay from the failed step with edited input, and an AI "Explain this failure" (credentials are removed from what the model sees; the answer can be wrong)
- Version history: Compare a saved version with the current workflow (steps, settings and connections that changed)
- Per-workflow limit on AI calls per run, a guard against loops that spend money (the time limit already existed)
- 1301 API tests and an 81-check browser suite. The explanation is tested against a simulated model only

### v0.40
- Dry run (describe, do not send) and an AI workflow draft from a plain description, both things n8n does not offer
- Editor toolbar wraps instead of overlapping on narrower screens
- 1282 API tests and a 78-check browser suite

### v0.38.1
- Review of the v0.30 to v0.38 additions: webhook secrets follow project scope, credential names are hidden from outsiders, imports list the credentials they use, failed runs free their idempotency key
- Verified that a key sent in a connector's address never appears in a stored failed run

### v0.38
- Convert Data: YAML to JSON and back, text from a PDF; Ctrl+K command palette (adds PyYAML and pypdf)
- 1242 API tests and a 76-check browser suite

### v0.37
- Run summary on the Executions page and a Prometheus-style metrics endpoint; webhook IP allow-list; AI translate and rewrite plus one corrective retry for invalid JSON; backups include connectors and can be imported from a link
- 1235 API tests and a 75-check browser suite; the backend suite also passed on a throwaway Postgres (full run at v0.36, then the newer test files separately; the browser suite was not repeated on Postgres)

### v0.36
- AI node tasks: extract fields, classify, sentiment, summarize (simulated-model tests only)
- 1204 API tests and a 73-check browser suite

### v0.35
- Credentials can be limited to one project (shared ones keep working everywhere)
- 1186 API tests and a 73-check browser suite

### v0.34
- Shared error workflow (Settings) for every workflow without its own; connector download and file import
- 1176 API tests and a 72-check browser suite

### v0.33
- Connector library: 22 ready-made manifests (not run against the live services)
- Fixed a settings race (a late reply could overwrite what you were typing, which made one browser check flaky), and the footer shows the real version
- 1166 API tests and a 72-check browser suite

### v0.32
- New Items trigger watches a folder of files and any GET action of a custom connector
- 1139 API tests and a 72-check browser suite

### v0.31
- Custom connectors: a JSON manifest turns any REST API into an App Action service (Settings page to manage them; simulated-server tests only)
- 1126 API tests and a 72-check browser suite

### v0.30
- Signed webhooks (HMAC-SHA256 with optional timestamp and replay window), idempotency keys, a time zone per Daily or Cron schedule
- 1110 API tests and a 71-check browser suite

### v0.29
- App triggers: the New Items trigger can watch a list action of 11 App Action services (Stripe, Airtable, HubSpot, Notion, Shopify, Supabase, Todoist, Dropbox, Google Calendar, Google Drive, Slack); simulated-server tests only
- 1097 API tests and a 71-check browser suite

### v0.28
- Fifteen more App Action operations on popular services: Slack edit/react/list channels, Notion read/update page, Airtable read/delete record, Stripe customer/refund/invoices, HubSpot update contact/create deal, Trello comment, Todoist list/complete (simulated-server tests only)
- 1091 API tests and a 71-check browser suite

### v0.27
- Docker image built and checked by hand once (see README); Make route filters with one plain comparison become Condition nodes
- Import from Make: blueprint conversion with warnings for everything that cannot be carried over (tested with self-written blueprints only)
- 1083 API tests and a 71-check browser suite

### v0.26
- Nineteen more App Action services (64 in all): Microsoft Teams, Google Chat, Datadog, Segment, Webex, Rocket.Chat, Pushbullet, MessageBird, Mailjet, Loops, Buttondown, Baserow, Healthchecks.io, Uptime Kuma, Mastodon, Matrix, Telnyx, Zulip, IFTTT (simulated-server tests only)
- 1075 API tests and a 71-check browser suite

### v0.25
- Data tables follow projects: a workflow uses the tables of its own project, and people only see the tables of projects they belong to
- 1065 API tests and a 71-check browser suite

### v0.24
- Live run view: the editor follows a run step by step (running nodes pulse, finished nodes turn green or red)
- 1063 API tests and a 71-check browser suite

### v0.23
- Postgres storage: set FLOWPILOT_DATABASE_URL and FlowPilot keeps everything in Postgres (all tests and all 70 browser checks pass against a real Postgres 16)
- pg_test.py and e2e_pg.py run the tests against a throwaway Postgres with no installation

### v0.22
- Data tables page (browse rows, remove a row, empty a table)
- Overview search finds workflows by node type and node settings
- 1053 API tests and a 70-check browser suite

### v0.21
- Data Table node: small named tables inside FlowPilot (add, find, get, count, change, delete, empty), so workflows remember things between runs
- Six more App Action services (45 in all): Mattermost, Gotify, Cloudflare, PostHog, Mixpanel, Vonage; Airtable update record
- 1042 API tests and a 68-check browser suite

### v0.20
- Eleven more App Action services (39 in all): Brevo, Mailgun, Telegram bot, GitHub, PagerDuty, Opsgenie, Intercom, Freshdesk, Supabase, Bitly, ConvertKit
- Convert Data node (text to JSON and back, XML to JSON with entity definitions refused, pick text from a web page, URL encoding)
- 1026 API tests and a 68-check browser suite

### v0.19
- Single sign-on with OpenID Connect (code flow with PKCE, ID tokens verified in FlowPilot, domain allow-list, no takeover of local accounts)
- 1007 API tests and a 68-check browser suite

### v0.18
- Wait for Response node (approvals through signed one-time links or the Executions page)
- Webhook authentication (secret header or Basic), HTTP Request query parameters, form bodies, timeout and pagination
- Ten more App Action services (28 in all), an approval template (28 templates)
- 973 API tests and a 67-check browser suite

### v0.17
- Webhook file uploads (multipart, public form file fields, stored with safe names, cleaned up with run history)
- AI Agent node (tool calling with a calculator, the clock, a web page reader and chosen workflows)
- 911 API tests and a 65-check browser suite

### v0.16
- Import n8n workflow exports (best effort, with a report of what needs attention)
- Variables: shared non-secret values used as {{vars.NAME}}
- Google Calendar and Google Drive actions (18 App Action services)
- 881 API tests and a 63-check browser suite

### v0.15
- New Items trigger (RSS, GitHub issues, any JSON list) with first-check baseline and per-check cap
- Nine more App Action services (16 in all), two more templates (25), bulk actions on the Overview page
- 829 API tests and a 60-check browser suite

### v0.14
- Production mode: the API serves the built interface (one port), with Docker files and Windows start-at-sign-in scripts
- Fifteen more templates (23 in five groups), a first-run tour and CSV export of run history
- 801 API tests and a 58-check browser suite

### v0.13
- Eight data tools: Sort, Limit, Remove Duplicates, Split Out, Summarize, Rename Keys, Date and Time, Crypto and Encoding
- Per-workflow time limit and run-keeping policy, replay of a failed run from the failed node, owner activity log
- 790 API tests and a 54-check browser suite

### v0.12
- "Run once for each item" mode for action nodes, so a node can process a whole list
- App Action node with Slack, Notion, Airtable, Stripe, Twilio, Linear and HubSpot (simulated-server tests only)
- 751 API tests and a 50-check browser suite

### v0.11
- Responsive layout for phones and tablets (slide-out menu, stacked tables, bottom-sheet inspector)
- Security hardening: security headers, request size limits, Secure cookies behind HTTPS, richer health check
- Credential key rotation (`rotate_key.py`), inbox checks off the scheduler thread, interface crash screen
- 721 API tests and a 49-check browser suite

### v0.10
- Expressions in every {{ }}: arithmetic, comparisons, conditionals, indexing and safe text and list helpers, evaluated without running code
- 707 API tests and a 48-check browser suite

### v0.9
- Queue mode with separate worker processes, atomic job claiming and crash recovery
- SQLite WAL mode for concurrent processes, configurable webhook rate limit
- 550 API tests and a 47-check browser suite

### v0.8
- Parallel execution of branches that are ready together
- Repeat Until, Files and Email (IMAP) trigger
- Per-user projects with visibility enforced on every route
- 527 API tests and a 47-check browser suite

### v0.7
- AI Prompt node for any OpenAI-compatible endpoint (Ollama and LM Studio run free locally)
- Public forms on webhooks, sticky notes, workflow tags with filtering
- Run-history retention, workflow backup and restore, full database download
- Route-by-route access-control test, 473 API tests and a 45-check browser suite

### v0.6
- OAuth 2.0 connections (Google, GitHub, custom) with PKCE and automatic refresh
- Google Sheets and Gmail nodes, OAuth option on the HTTP node
- Durable long waits (up to 7 days) with resume, cancel and restart recovery
- 432 API tests and a 42-check browser suite

### v0.5
- Switch, Code (restricted Python), Stop and Fail and Wait nodes
- Per-node retries, continue-on-error and error-handler workflows
- Database Query (SQLite, Postgres), Read RSS, Telegram and GitHub nodes
- Respond to Webhook
- Users with owner, admin, editor and viewer roles, and change password
- Workflow version history with restore
- 325 API tests and a 39-check browser suite

### v0.4
- Credentials vault: Fernet-encrypted at rest, never returned by the API, redacted from run results
- Engine rebuilt: Loop Over Items, Merge (fan-in), Run Workflow (sub-workflows), pinned node data
- Set Fields, Filter List, Parse CSV, Send Email (SMTP), Send Message (Slack, Discord)
- HTTP authentication (Bearer, Basic, API-key header) and cron schedules
- Editor: undo/redo, copy/paste, duplicate, tidy layout, per-node input and output panel
- 245 API tests and a 34-check browser suite

## Phase 2: True automation engine
- Worker queue (Redis or database-backed)
- Cron expressions, time zones per workflow and durable job queue for schedules
- Signed webhook payloads and replay protection
- Pause/resume, job retry rules, idempotency
- Bounded parallel execution, retries with backoff and error-handler workflows
- Node-level error handling and execution versioning

## Phase 3: Integrations and credentials
- API connector SDK and connector manifest schema
- Credential key rotation
- OAuth integrations (Google, Microsoft, Slack, etc.)
- Email and chat notification action nodes
- CSV, JSON, database, Google Sheets actions
- Files, storage providers and document workflows

## Phase 4: Intelligent workflows
- Optional self-hosted AI model integration
- Document classification, extraction and summarization
- Agent-assisted workflow suggestions with human confirmation
- Template marketplace

## Phase 5: Production SaaS
- PostgreSQL migration, Redis queue, scaled workers
- Multi-user accounts (v0.2 has one owner login), organization isolation, RBAC, audit logs
- Security hardening incl. network egress isolation for untrusted connectors
- Rate limiting, usage quotas, monitoring and disaster recovery
- Multi-tenant deployment and subscription plans (optional)

## Architectural direction
The saved workflow definition (`nodes` and `edges`) is portable JSON. Keep connector runtime and execution engine separate so the UI remains stable as the backend scales.
