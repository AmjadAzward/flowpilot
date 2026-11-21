<div align="center">

<img src="docs/banner.svg" alt="FlowPilot" width="100%"/>

<br/>

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Tests](https://img.shields.io/badge/tests-1340%20API%20%2B%2085%20browser-34D399?style=for-the-badge)
![Auth](https://img.shields.io/badge/login-built%20in-E9522A?style=for-the-badge)
![Triggers](https://img.shields.io/badge/triggers-schedule%20%2B%20webhook%20%2B%20cron-F43F5E?style=for-the-badge)
![Vault](https://img.shields.io/badge/credentials-encrypted-8B5CF6?style=for-the-badge)
![Cost](https://img.shields.io/badge/cost-free-F59E0B?style=for-the-badge)

**Design workflows visually. Run them on a schedule or from a webhook. Loop over lists, merge branches, call APIs and send email. Keep secrets encrypted. Debug every step.**
**No account, no cloud, no subscription.**

</div>

---

## <img src="https://img.shields.io/badge/-Screenshots-8B7BFF?style=flat-square" alt="Screenshots"/>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/01-dashboard.png" alt="Dashboard"/><br/><sub><b>Overview</b> with stats, your workflows and starter templates (light theme)</sub></td>
    <td width="50%"><img src="docs/screenshots/02-dashboard-dark.png" alt="Dashboard in dark mode"/><br/><sub><b>Dark theme</b>, remembered between visits</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/04-branching.png" alt="Branching workflow"/><br/><sub><b>Branching</b>: only the matching path runs, the other is greyed out</sub></td>
    <td width="50%"><img src="docs/screenshots/05-failed-run.png" alt="Failed run"/><br/><sub><b>Failure inspection</b> with the failing node marked on the canvas</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/08-webhook-inspector.png" alt="Webhook trigger"/><br/><sub><b>Webhook trigger</b> with a secret URL, a test payload and an Active switch</sub></td>
    <td width="50%"><img src="docs/screenshots/10-http-node.png" alt="HTTP request node"/><br/><sub><b>HTTP Request</b> node: GET, POST, PUT, PATCH and DELETE with headers and a body</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/12-loop.png" alt="Loop over items"/><br/><sub><b>Loop Over Items</b>: numbered iterations, and a per-node data panel with the real input and output</sub></td>
    <td width="50%"><img src="docs/screenshots/11-credentials.png" alt="Credentials vault"/><br/><sub><b>Credentials vault</b>: encrypted at rest, never shown again, hidden in run results</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/00-login.png" alt="Sign in screen"/><br/><sub><b>Sign in</b> screen protecting your workspace</sub></td>
    <td width="50%"><img src="docs/screenshots/06-executions.png" alt="Execution history"/><br/><sub><b>Execution history</b> with status filters and step details</sub></td>
  </tr>
</table>

---

## <img src="https://img.shields.io/badge/-Features-34D399?style=flat-square" alt="Features"/>

| Area | What you get |
| :--- | :--- |
| **Visual editor** | Drag, connect and configure nodes on a React Flow canvas with a minimap, zoom and a searchable node library |
| **Real branching** | The Compare Value node has green *true* and red *false* outputs. Only the matching branch executes |
| **Credentials vault** | API keys, tokens and passwords are encrypted with Fernet (AES) on your computer, referenced as `{{cred.NAME}}` or chosen in a dropdown, never returned by the API, and replaced with `[hidden]` in outputs, inputs and error messages |
| **Loops, lists and merging** | Loop Over Items (up to 100 per loop, nested loops work), Merge (append, combine or first), Filter List, Parse CSV and Set Fields. Branches that are not taken never block a Merge |
| **Debug like a pro** | Click a node to see the exact input and output of its last run, with iteration counts. Pin a node's output so it is skipped while you build the rest (no repeated API calls) |
| **Expressions** | Any `{{ }}` can hold a calculation, not just a field name: `{{ price * qty }}`, `{{ name.upper() }}`, `{{ items[0].title }}`, `{{ 'big' if total > 100 else 'small' }}`. Evaluated by a restricted interpreter that never runs code, with size and step limits |
| **Queue mode and workers** | Start the API with `FLOWPILOT_QUEUE=1` and run one or more worker processes (`start-worker-windows.bat`, or `python -m app.worker`). Webhooks, schedules and email triggers are queued and run by workers, so a slow or heavy run never blocks the API. Jobs are claimed atomically (never run twice) and survive an API restart |
| **Parallel branches** | Branches that become ready together run at the same time (up to 4 at once) when they are network, database, AI, code or file nodes. Results are recorded for every branch, and a failure still finishes its siblings |
| **Repeat until** | Repeat steps until a condition is met, for example polling an API until a job is done, with a limit of 20 attempts |
| **Files** | Read, write, list and delete files (text or binary as base64) in one safe folder, and download an HTTP response straight into a file (up to 5 MB) |
| **New Items trigger** | Watch an RSS or Atom feed, the issues of a GitHub repository, any JSON list from an API, or a list from one of 11 App Action services (new Stripe customers, payments or invoices, Airtable records, HubSpot contacts, Notion database pages, Shopify orders or products, Supabase rows, Todoist tasks, Dropbox files, Google Calendar events, Google Drive files, Slack channels), and run the workflow once for each new item (up to 10 per check, every 1 to 1440 minutes). Activating remembers what exists now so old items do not run. Failed checks are recorded as failed runs |
| **Bulk actions** | Tick workflows on the Overview page to activate, deactivate, run or delete several at once |
| **Email trigger** | Poll an IMAP inbox and run the workflow once per new message, with optional sender and subject filters. Failed checks are recorded as failed runs |
| **Projects** | The owner groups workflows into projects and chooses who is a member. Members see their projects plus unassigned workflows. Enforced on every route, including history and cancelling runs |
| **AI, free and private** | The AI Prompt node talks to any OpenAI-compatible service. Run a model on your own computer with Ollama or LM Studio at no cost, or use a hosted one. Replies can be text or JSON data |
| **Forms** | Give a webhook form fields and its URL becomes a small public web page. Submissions run the workflow and the visitor sees a thank-you page |
| **Organise and protect** | Tags with a dashboard filter, sticky notes on the canvas, automatic run-history cleanup (by age and count), workflow backup and restore, and a full database download (owner only) |
| **Connected accounts (OAuth)** | Connect Google (Sheets, Gmail and any Google API through the HTTP node), GitHub or any OAuth 2.0 service with your own free app. Authorization code with PKCE, tokens encrypted in the vault and refreshed automatically, hidden in run results |
| **Long waits** | A Wait of up to 7 days pauses the run, survives API restarts and continues by itself. Waiting runs show a resume time and can be cancelled |
| **Teams and roles** | The owner adds users as admin, editor or viewer. Viewers are read only, editors build and run, admins also manage credentials. Everyone can change their own password, and removing or demoting a user takes effect immediately |
| **Version history** | Every save that changes a workflow keeps the previous version (last 30). Open History in the editor and restore one. A restore is itself undoable |
| **Databases** | Database Query runs SQL on SQLite (files in backend/data) or Postgres, always with bound parameters so data cannot change the query |
| **Webhook replies** | Respond to Webhook lets the caller get your own status code and JSON body |
| **Reliability** | Per-node retries (up to 5, with a wait), "continue with the error as data", and an error workflow that runs when a workflow fails and receives the error, the failed node and the execution id |
| **Code and routing** | Switch routes into up to four paths plus a default. The Code node runs a short Python snippet in a separate restricted process with a 5 second limit, no imports, files or network |
| **Editor comfort** | Undo and redo (Ctrl+Z, Ctrl+Y), copy and paste nodes (Ctrl+C, Ctrl+V), duplicate node, one-click Tidy layout, unsaved-change indicator, Ctrl+S |
| **Connect to services** | Send Email over SMTP, Send Message to Slack or Discord, HTTP with Bearer, Basic or API-key auth, and Run Workflow to call one workflow from another |
| **Run on their own** | Schedule trigger (every N minutes, daily at a time, or any cron expression) and Webhook trigger (a secret URL that starts the workflow), with an Active switch per workflow |
| **Work on lists** | Most action nodes can run once for every element of a list ("Run once for each item", up to 200 items), so one HTTP, Code, Set or message node handles a whole list and returns a list of results |
| **Import from n8n** | The Import button on the Overview page and in the editor also accepts an n8n workflow export. Triggers, Set/Edit Fields, IF, Switch, Filter, HTTP Request, Merge, Wait, Respond to Webhook, Sort, Limit, Remove Duplicates, Split Out, Summarize, Date and Time, Crypto, Slack, Telegram, GitHub, Email, RSS and Google Sheets nodes are converted, and `$json` expressions are translated. Everything else becomes a Pass Through node, and a report lists what needs attention (credentials are never exported by n8n, JavaScript code and cross-node references cannot be converted, loops must be rebuilt). It is best effort: always read the report and test before activating |
| **Import from Make** | The same Import button accepts a Make (Integromat) scenario blueprint (scenario menu, Export Blueprint). Webhook, HTTP, Set Variable, Sleep, Slack and Send Email modules convert, routers become a Pass Through node with one branch per route (a route filter with one plain comparison becomes a Condition node), and the schedule becomes a Schedule trigger. Other filters, Make functions, references to modules other than the one before, and every other module are reported in the warnings. This was written from Make's documented blueprint layout and tested only with blueprints I wrote myself, not real exports. **Zapier is not supported**: Zapier has no export format that can be imported |
| **Single sign-on (OpenID Connect)** | The owner can turn on sign-in with a company account (Google, Microsoft Entra ID, Okta, Keycloak or any OpenID Connect provider) in Settings: issuer, client ID and secret, allowed email domains and the role for new people. It uses the authorization code flow with PKCE and verifies the ID token itself (RS256 signature against the provider's published keys, issuer, audience, expiry, nonce and a confirmed email address). People get an account of their own kind with no password, an existing local account is never taken over, and local passwords keep working. It has been tested against a stand-in provider that signs real tokens, not against a real Google, Entra or Okta tenant, so try it with a test account first |
| **Wait for Response (approvals)** | A node that pauses the run until someone answers. Any earlier node can put `{{run.approve_url}}` and `{{run.reject_url}}` in a message (Slack, ntfy, email); opening a link answers the run, or an editor presses Approve or Reject on the Executions page. The next nodes get `{{decision}}` (approve, reject, timeout, or your own word with `?decision=word`), `{{data}}` and `{{body}}`. Links are signed, work once, and expire after the time limit (1 minute to 7 days), when the run continues with the decision "timeout" or fails. Anyone who has a link can answer, so send it only to people you trust |
| **Dry run** | A Dry run button runs the whole workflow but only describes what would be sent or changed: POST/PUT/DELETE requests, emails, chat messages, file writes, table and SQL changes, app actions that write, and waits are skipped and listed ("Not carried out"). Reads still really run (GET requests, file and table reads, AI calls, list/get actions), so those services are contacted. Dry runs never call the error workflow and are stored as their own kind. n8n has no equivalent: its test run executes for real |
| **Describe a workflow (AI draft)** | On the Overview page you describe what you want, an AI model of your choice drafts the workflow, and FlowPilot checks it (known node types only, no code/SQL/file nodes, one trigger, everything reachable, no credentials copied in) before saving it as an inactive draft with the `ai-draft` tag. One corrective retry; nothing runs by itself, so review it and try a Dry run first. Tested against a simulated model only; small local models draft poorly |
| **Fix failed runs faster** | On a failed run you can edit the data the failed step received and replay from there, or ask an AI model to explain the failure (credentials are removed from what it sees; it is tested against a simulated model only and can be wrong). Version history has a Compare button showing which steps, settings and connections changed. A workflow can cap its AI calls per run. |
| **Test cases and guards** | Save sample inputs with the output you expect (or the failure you expect) and run them all after an edit; they run as a dry run, so nothing is sent or written. Replay every failure from the last hour in one click. Typing `{{` suggests fields from the previous step's real data once the workflow has run. Per-workflow limits on AI calls and AI tokens per run, and a per-step time limit that stops waiting for a slow step (a request already sent may still finish). Tokens are counted from what the AI service reports, or estimated. Each run shows the AI calls and tokens it used, and the run stats add them up. Saved tests travel with workflow backups and copies. |
| **Scope and import safety** | A webhook can only use secrets its own project may use; people outside a project do not see that project's credential names; a workflow import (file or link) lists the credentials each workflow refers to so you can read it before running it; a failed run frees its Idempotency-Key so the sender can retry. Known limits: error details from every project reach the shared error workflow, and a hostile PDF could make reading slow (5 MB cap, no time limit yet) |
| **YAML, PDF and the palette** | Convert Data reads YAML (safe loading only: no aliases, dates or custom types) and writes it, and reads the text of a PDF from the files folder (first 50 pages, 5 MB, 200,000 characters; scanned PDFs without a text layer give nothing, there is no OCR). Press Ctrl+K anywhere to jump to a page or workflow. Adds two small free libraries, PyYAML and pypdf, to requirements.txt |
| **Operations and sharing** | The Executions page shows a summary (runs, failures, failure rate, average time, workflows that fail most) for the runs you may see; owners get a Prometheus-style `/api/metrics`. Webhooks can be limited to listed IP addresses or ranges (it sees the connecting address, so a proxy hides the real caller). The workflow backup now includes connectors and can be imported from an HTTPS link (inactive workflows, nothing overwritten, owner only) |
| **AI tasks** | The AI node can also extract named fields from text (typed, null when missing), sort text into your categories, judge sentiment, summarize, translate or rewrite, on any OpenAI-compatible service (free with Ollama). Answers outside your categories are refused. Tested against a simulated model only; small local models can get structured answers wrong, so check results |
| **Project credentials** | A credential can be limited to one project: only workflows in that project can use it (credentials with no project stay shared by every workflow). A workflow that calls another through Run Workflow runs with its own credentials, so it cannot borrow another project's. A project that still has credentials cannot be deleted. Scope is changed on the Credentials page without retyping the value |
| **Shared error workflow** | One workflow, chosen in Settings, runs whenever any workflow fails and has no error workflow of its own (the workflow's own choice wins; an error workflow never triggers another). Custom connectors can be downloaded as a file and imported again |
| **Connector library** | 22 ready-made connector manifests (Open-Meteo, OpenWeatherMap, Calendly, Typeform, Clockify, Vercel, Netlify, DigitalOcean, Sentry, Webflow, Plausible and more) that you can pick in Settings and save. They were written from public API documentation and never run against the live services, so treat each as a starting point |
| **Folder and connector triggers** | The New Items trigger can also watch a folder inside the files folder (new or changed files, optional name pattern such as *.csv; symlinks ignored) and any GET action of a custom connector. Simulated-server tests only for connectors |
| **Custom connectors** | Owners and admins describe any REST API in a small JSON manifest (https address, sign-in style: none, bearer, basic, header or query, and actions with method, path, query and body templates) in Settings. It then appears as "Custom connector" in the App Action node. Addresses go through the same public-host check as every other call. Tested against simulated servers only |
| **Signed webhooks, repeats and time zones** | A webhook can require an HMAC-SHA256 signature of the body (optionally of `timestamp.body` with a five-minute window, which blocks replays), can ignore repeated requests that carry the same `Idempotency-Key` for a day, and Daily and Cron schedules can run in any IANA time zone (daylight saving follows the zone). Checked with self-made signatures only, not against a real sender such as GitHub or Stripe |
| **Webhook authentication** | A webhook can require a secret header or Basic authentication, checked against a saved credential in constant time. Calls without it get a 401 and never run the workflow. A deleted credential or a broken setting locks the webhook instead of opening it. Not compatible with the public form, because browsers cannot send the header |
| **Smarter HTTP Request** | Query parameters as JSON, form (urlencoded) bodies, a per-node timeout (1 to 60 seconds) and pagination: follow a "next" address in each reply (same site only) or count pages with a parameter, up to 20 pages and 5000 items, returned as one list |
| **File uploads on webhooks** | A webhook can accept multipart file uploads, either from any sender (switch "Accept file uploads" on) or through its public form (a field of type `file`). Limits: 5 files, 5 MB each, 10 MB in total. Files are stored under `uploads/` in the files folder with safe names, described to the workflow as `{{body.files.0.filename}}`, `size`, `content_type` and `path` (the Files node can read the path), and deleted with the run-history cleanup. Without the switch, a multipart request is refused. Anyone who has the webhook address can upload, so keep it private |
| **AI Agent node** | A model that can call tools before it answers: a calculator (the same safe expression engine), the current time, a "read this web page" tool (public HTTPS only), and up to five workflows you choose. It makes at most 6 tool rounds, stops after 150 seconds, and returns the answer plus the list of tool calls it made. It needs a model that supports tool calling and the same credential format as the AI Prompt node, and it is tested only with a scripted stand-in for the model, not a real one. A web page can contain text that tries to steer the model, so do not combine the web page tool with workflows that send messages or change data |
| **Variables** | Shared, non-secret values (a website address, a rate) managed in Settings by admins and owners, and used anywhere as `{{vars.NAME}}`, including inside expressions. Everyone can read them, so secrets still belong on the Credentials page |
| **App Action node** | Sixty-four services behind one node: Slack, Discord, Notion, Airtable, Stripe, Twilio, Linear, HubSpot, SendGrid, Mailchimp, Trello, Asana, Jira, Todoist, GitLab, ntfy (free push notifications), Shopify, Zendesk, Pipedrive, ClickUp, monday.com, Dropbox, Resend, Postmark, WhatsApp (Cloud API), Pushover, Brevo, Mailgun, Telegram, GitHub, PagerDuty, Opsgenie, Intercom, Freshdesk, Supabase, Bitly, ConvertKit, Mattermost, Gotify, Cloudflare, PostHog, Mixpanel, Vonage, Microsoft Teams, Google Chat, Datadog, Segment, Webex, Rocket.Chat, Pushbullet, MessageBird, Mailjet, Loops, Buttondown, Baserow, Healthchecks.io, Uptime Kuma, Mastodon, Matrix, Telnyx, Zulip, IFTTT, and Google Calendar and Google Drive (which use a connected Google account). Several have more than one action: Slack (post, edit, react, list channels), Notion (create, query, read, update page), Airtable (list, create, update, read, delete), Stripe (customers, payments, invoices, refunds), HubSpot (contacts, deals), Trello (card, comment) and Todoist (create, list, complete). These are tested against simulated servers only, so try each one with a test account before relying on it |
| **Data tools** | Sort, Limit, Remove Duplicates, Split Out, Summarize (count, sum, average, min, max, collect), Rename Keys, Date and Time (add, subtract, compare, format), Crypto and Encoding, Data Table (small tables kept inside FlowPilot to remember rows between runs), and Convert Data (text to JSON and back, XML to JSON, pick text from a web page, URL encoding) (SHA, MD5, HMAC signatures, base64, random IDs) |
| **Live run view** | The Run button follows the run while it works: each node pulses while it runs and turns green or red as it finishes, and the run panel fills in step by step. Credential values are hidden in the live view as they are in finished runs. Only runs started from the editor are live, and a run is kept in memory for two minutes after it ends (it is stored in the history as usual) |
| **Data tables page** | The Data tables page in the sidebar lists every table and its rows (newest first) and lets editors remove a row or empty a table. Tables follow projects: a workflow reads and writes the tables of its own project, workflows without a project share one set, and people only see the tables of projects they belong to (owners and admins see all). Moving a workflow to another project makes it see that project's tables instead. Rows added before v0.25 count as belonging to no project |
| **Search inside workflows** | The Overview search also finds workflows by node type and the text typed into node settings, not just by name and tag |
| **Run settings per workflow** | An optional time limit (checked between steps) and a choice of which automatic runs to keep: every run, only failed runs, or none. Test runs from the Run button are always kept |
| **Replay a failed run** | One click on the Executions page runs the workflow again from the node that failed, with the data it received. It continues the part after that node, and it cannot replay a failure inside a loop |
| **Activity log** | The owner sees who changed what (every successful change, plus sign-ins and failed sign-ins by account name). Passwords and bodies are never recorded. The latest 5000 entries are kept |
| **Twenty-eight templates** | Ready-made workflows in five groups (Getting started, Lists and data, Webhooks and APIs, Reliability, Apps), each one created and run in the browser tests with its exact expected result, except the ones that need internet or an account |
| **First-run tour** | A four-step walkthrough shown once, and again from the Tour link in the sidebar |
| **Export runs to CSV** | The Executions page exports the run history (all, or only the failed or successful ones). Cells that could be read as spreadsheet formulas are neutralised |
| **Forty-nine node types** | Manual, Schedule, Webhook, Email and New Items triggers; JSON Input, Extract Field, Text Template, Set Fields, Filter List, Parse CSV, Files; Compare Value, Switch, Loop Over Items, Repeat Until, Merge, Run Workflow, Code, Stop and Fail, Wait, Wait for Response, Delay, Pass Through; Sort, Limit, Remove Duplicates, Split Out, Summarize, Rename Keys, Date and Time, Crypto and Encoding; Database Query (SQLite and Postgres), Read RSS Feed, Google Sheets; HTTP Request, Send Message, Send Email, Telegram Message, GitHub Issues, App Action (Slack, Notion, Airtable, Stripe, Twilio, Linear, HubSpot), Gmail, AI Prompt, AI Agent; Sticky Note, Result Output, Respond to Webhook |
| **Full HTTP requests** | GET, POST, PUT, PATCH and DELETE with custom headers and a JSON or text body. `{{field}}` placeholders work in the URL, headers and body |
| **Seven comparison operators** | equals, does not equal, contains, greater than, less than, exists, does not exist |
| **Run feedback** | Per-node success, error and skipped badges on the canvas, plus per-step timings in milliseconds |
| **Templates** | Eight ready-made workflows: greeting, order triage (branching), fetch-and-extract, webhook receiver, hourly API check, send-data-to-an-API (POST), process a list (loop) and CSV to filtered report |
| **Workflow management** | Create, rename, duplicate, delete, import and export as JSON, run directly from the dashboard |
| **Execution history** | Every run stored with output and steps, filterable by status, with one-click clear |
| **Look and feel** | Light and dark themes, branded browser tab title and icon |
| **Login** | Owner account created on first visit, salted PBKDF2 password hashing, 7-day sessions in an HttpOnly cookie, lockout after 5 failed attempts, every API route locked until you sign in |
| **Safe by default** | HTTPS-only public destinations (checked again after placeholders are filled in), private and metadata addresses blocked, redirects refused, forbidden headers rejected, 8 second timeout, 1 MB request and response caps. Webhooks are rate limited and size limited |

---

## <img src="https://img.shields.io/badge/-Quick%20start-F59E0B?style=flat-square" alt="Quick start"/>

### Windows

1. Install **Python 3.11 or newer** (tick *Add Python to PATH*) and **Node.js LTS**.
2. Extract or clone this project, for example to `C:\FlowPilot`.
3. Double-click **`start-windows.bat`**. The first launch installs dependencies and opens two terminal windows.
4. Open **http://127.0.0.1:5173** in your browser. On first visit you create your owner account (see below).

### macOS and Linux

```bash
./start-mac-linux.sh
```

### Manual start (any platform)

```powershell
# Terminal 1: API
cd backend
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: web app
cd frontend
npm install
npm run dev
```

Interactive API documentation is served at **http://127.0.0.1:8000/docs**.

### Run it for real: one window, Docker, or start at sign-in

The steps above are for building and developing. To simply use FlowPilot, run it in production mode, where the API serves the built interface itself on **one port** (8000):

- **Windows, one window:** double-click `start-production-windows.bat`. It builds the interface the first time, then starts FlowPilot at http://127.0.0.1:8000. After updating the code, run it with `--rebuild`.
- **Windows, start at sign-in:** after the step above, run `install-startup-windows.bat`. It adds a scheduled task for your own account (no administrator rights) that starts FlowPilot hidden whenever you sign in, so schedules, webhooks and email checks keep working. `uninstall-startup-windows.bat` removes it.
- **Docker:** `docker compose up -d --build`, then open http://127.0.0.1:8000. Everything lives in the `flowpilot-data` volume, so back that volume up. The port is published to this computer only; put an HTTPS reverse proxy in front before exposing it, and set `FLOWPILOT_PUBLIC_URL` so webhook addresses show your public name.
- **Any platform, by hand:** build once with `npm run build` in `frontend`, then start the API with `FLOWPILOT_SERVE_UI=1`.

The Docker image was built and run once with Docker Desktop 29.8 (WSL 2) on Windows 11: it started healthy, served the interface, created the owner, ran a workflow that wrote a Data Table row, and kept the login, workflow, table row and a saved credential after a container restart; it runs as a non-root user and `docker compose up -d --build` (with the port moved to 8100, because 8000 was in use) started it healthy and created the encryption key in the data volume with owner-only permissions. That was a manual check, not an automated test. The sign-in task has been registered on one Windows machine but not tested across a real logon. The production serving itself is tested.


---

## <img src="https://img.shields.io/badge/-Login%20and%20account-E9522A?style=flat-square" alt="Login"/>

- **First visit:** FlowPilot shows *Create your account*. Choose a username (3 to 40 characters) and a password of at least 8 characters. That single owner account is the only one; setup closes afterwards.
- **After that:** you sign in with that username and password. Sessions last 7 days or until you press **Sign out**.
- **Forgot the password?** Stop the API, then run this from the `backend` folder. It removes the owner account and sessions (workflows and history are kept) so the setup screen appears again:

```powershell
.\.venv\Scripts\python.exe reset_password.py
```

- **Upgrading from an earlier version:** existing workflows are kept. You will simply be asked to create the owner account on your next visit.

---

## <img src="https://img.shields.io/badge/-Schedules%20and%20webhooks-F43F5E?style=flat-square" alt="Triggers"/>

A workflow starts from exactly one trigger. Use the **Active** switch (editor bar or dashboard row) to turn a Schedule or Webhook workflow on. Manual workflows cannot be activated.

**Schedule trigger.** Choose *every N minutes* (1 to 10080), *every day at a time* (24-hour) or a **cron expression** such as `0 9 * * 1-5` (weekdays at 9:00). Cron supports `*`, lists, ranges and steps; when both day-of-month and day-of-week are set, either may match, as in standard cron. All times use this computer's time zone. The first run happens one interval after you activate it. If the API was off when a run was due, it runs once on the next start and then resumes its normal rhythm. The node receives `{{scheduled_at}}`.

**Webhook trigger.** Click the node to see its URL, for example `http://127.0.0.1:5173/api/hooks/<secret>`. Once the workflow is Active, any `POST` or `GET` to that URL starts it:

```bash
curl -X POST "http://127.0.0.1:5173/api/hooks/<secret>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada","message":"Hello"}'
```

Later nodes can read `{{body.name}}`, `{{query.x}}`, `{{method}}` and `{{content_type}}`. The call returns `{"status":"success","execution_id":"..."}` (HTTP 200), or HTTP 500 with `"status":"failed"` if the workflow failed. It never returns your workflow's data. Use **Generate new URL** in the node settings to replace a leaked URL. Unknown, inactive or wrong-type URLs all answer `404`.

**Testing before you activate.** *Run workflow* works on any workflow. A webhook workflow runs with the node's *Test payload*; a schedule workflow runs with a sample event.

---

## <img src="https://img.shields.io/badge/-Credentials-8B5CF6?style=flat-square" alt="Credentials"/>

Open **Credentials** in the sidebar to save a secret once and use it anywhere.

- **In any text field of a node:** write `{{cred.NAME}}` (URL, headers, body, message, template).
- **HTTP Request:** set *Authentication* to Bearer, Basic (`user:password`) or an API-key header, and pick the credential.
- **Send Email:** save an SMTP credential such as `smtp://user:password@smtp.example.com:587?from=you@example.com`. Use `smtps://` for SSL, add `&security=none` for plain, and URL-encode special characters in the password.
- **Send Message:** save the Slack or Discord webhook URL as a credential and enter `{{cred.SLACK_URL}}` as the URL.

How it is protected:

- Values are encrypted with Fernet (authenticated AES) using a key in `backend/flowpilot.key`, which is created automatically and never committed. **Back this file up with the database**: without it saved credentials cannot be decrypted.
- No endpoint ever returns a value. You can only replace or delete a credential.
- Any credential value that appears in a run's inputs, outputs or errors is replaced with `[hidden]` before it is stored or shown (values must be at least 4 characters so they can be hidden reliably).
- Webhook payloads and other data are never re-interpreted, so incoming data cannot ask for a credential.
- Email servers on private networks are blocked unless you start the API with `FLOWPILOT_ALLOW_PRIVATE_HOSTS=1`. That switch never applies to HTTP requests.

---

## <img src="https://img.shields.io/badge/-Loops%20and%20merging-0EA5E9?style=flat-square" alt="Loops"/>

- **Loop Over Items** takes a list (or a list inside the data via *List field*). Connect the **each** handle to the steps that run once per item and the **done** handle to what follows. *Done* receives the list of per-item results. A loop handles up to 100 items, may contain conditions and other loops, and the two paths cannot join.
- **Merge** accepts several incoming connections. *Append* gives a list of every input, *Combine* merges objects (later inputs win) and *First* keeps one. It waits for every branch that is actually running, so a Compare Value branch that was not taken never blocks it.
- **Filter List**, **Parse CSV** and **Set Fields** prepare data for loops. Set Fields escapes text for you, so quotes in the data cannot break your JSON.
- **Run Workflow** calls another workflow with the current data and continues with its result (up to a few levels deep, never itself).

## <img src="https://img.shields.io/badge/-Debugging-F59E0B?style=flat-square" alt="Debugging"/>

- Run a workflow, then **click any node**: the *Last run* panel shows its input, output, error and how many times it ran.
- **Pin last output** freezes a node. Pinned nodes are skipped and use the saved output, so you can build the rest of a flow without calling an API or sending a message again. Pinned nodes show a pin badge. **Unpin** to run it for real.
- Steps in the run panel show iteration numbers (`#2`), `pinned`, and the branch taken.

---

## <img src="https://img.shields.io/badge/-Your%20first%20workflow-38BDF8?style=flat-square" alt="First workflow"/>

1. On the **Overview** page choose the template **Order triage**.
2. Click **Run workflow**. The result is `PRIORITY: order A-1042 (250)` and the *false* branch is greyed out.
3. Select the **Compare Value** node and change *Compare to* from `100` to `1000`.
4. Run again. Now the *false* branch executes and you get `Standard: order A-1042 (250)`.

---

## <img src="https://img.shields.io/badge/-How%20it%20works-FB7185?style=flat-square" alt="How it works"/>

```mermaid
flowchart LR
    A[React + React Flow<br/>web app]:::ui -->|/api proxy, session cookie| B[FastAPI<br/>REST API + login]:::api
    B --> C[Execution engine<br/>branching, templates, HTTP]:::engine
    B --> D[(SQLite<br/>workflows and runs)]:::db
    C -->|HTTPS GET only| E[Public web APIs]:::ext

    classDef ui fill:#E9522A,stroke:#b13a18,color:#ffffff
    classDef api fill:#009688,stroke:#00695c,color:#ffffff
    classDef engine fill:#F472B6,stroke:#be185d,color:#ffffff
    classDef db fill:#38BDF8,stroke:#0369a1,color:#0a0f1c
    classDef ext fill:#F59E0B,stroke:#b45309,color:#0a0f1c
```

A workflow is plain JSON (`nodes` and `edges`). The engine starts at the workflow's single trigger and walks the graph. A small scheduler thread inside the API checks every 10 seconds for due schedules. A Compare Value node follows only the edge whose `sourceHandle` matches its result (`true` or `false`); every other node passes its output to all of its outgoing edges.

### Node reference

| Node | Settings | Output |
| :--- | :--- | :--- |
| Manual Trigger | none | nothing (starts the run) |
| Schedule Trigger | every N minutes, or daily at a time | `{scheduled_at, manual}` |
| Webhook Trigger | test payload for manual runs | `{method, query, body, content_type}` |
| JSON Input | JSON text | the parsed JSON |
| Extract Field | dotted path such as `user.items.0.name` | the value at that path, or `null` |
| Text Template | text with `{{path}}` placeholders | the rendered string |
| Compare Value | path, operator, comparison value | `{matched, value}`; routes to *true* or *false* |
| Delay | seconds, capped at 5 | the data it received, unchanged |
| HTTP Request | method, public HTTPS URL, authentication, headers (JSON), body | parsed JSON, or text if the response is not JSON |
| Set Fields | JSON template | the new object |
| Filter List | field, operator, value | the matching items |
| Parse CSV | text field, header on or off | list of rows |
| Loop Over Items | list field | `each`: runs the body per item. `done`: list of results |
| Merge | append, combine or first | merged data |
| Run Workflow | workflow to call | the other workflow's result |
| Send Message | Slack, Discord or generic, webhook URL, message | the incoming data continues |
| Send Email | SMTP credential, recipients, subject, body | the incoming data continues |
| Pass Through | none | the data it received, unchanged |
| Result Output | none | the final result shown in the run panel |

---

## <img src="https://img.shields.io/badge/-Testing-34D399?style=flat-square" alt="Testing"/>

**API, engine, security and connector tests** (1340 tests, use a temporary database and key):

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

**Browser end-to-end tests** (34 checks driving a real Chrome or Edge: account setup, sign in and out, activation, a webhook called from a separate session, schedules and cron, credentials, loops, pinning, undo and redo, copy and paste, and more). These create and delete workflows, so start the API on a throwaway database first:

```powershell
$env:FLOWPILOT_DB = "$env:TEMP\flowpilot-e2e.db"
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# in another terminal, with the web app running:
cd frontend
npm run test:e2e
```

**Type check and production build:**

```powershell
cd frontend
npx tsc --noEmit
npm run build
```

---

## <img src="https://img.shields.io/badge/-Project%20layout-8B7BFF?style=flat-square" alt="Project layout"/>

```text
flowpilot/
  start-windows.bat          one-click launcher for Windows (development)
  start-production-windows.bat   one-window production mode (API serves the built interface)
  install-startup-windows.bat    start FlowPilot at sign-in (uninstall-startup-windows.bat removes it)
  run-flowpilot.ps1          what the two scripts above run
  Dockerfile, docker-compose.yml
  start-mac-linux.sh         launcher for macOS and Linux
  backend/
    app/main.py              API, login, credentials vault, scheduler, webhooks and the execution engine
    app/worker.py            queue worker (python -m app.worker)
    rotate_key.py            rotate the credential encryption key
    reset_password.py        removes the owner account so you can set a new one
    tests/                   pytest suite
    requirements.txt
  frontend/
    src/App.tsx              auth gate, dashboard, editor, executions, credentials, settings
    src/NodeSettings.tsx     the settings form for every node type
    src/Login.tsx            sign in and first-run account setup
    src/MiniFlow.tsx         workflow previews on template cards
    public/favicon.svg       browser tab icon
    src/nodes.tsx            custom canvas nodes and run-state badges
    src/catalog.ts           node definitions and starter templates
    src/styles.css           design tokens, light and dark themes
    e2e/e2e.mjs              browser end-to-end tests
  examples/hello-world.json  sample workflow to import
  docs/                      banner and screenshots
  ROADMAP.md
```

---

## Running on Postgres

SQLite is the default and is right for one machine. For several API or worker processes on different machines, or a database you already back up, point FlowPilot at Postgres:

```
set FLOWPILOT_DATABASE_URL=postgresql://flowpilot:secret@localhost:5432/flowpilot
```

Create an empty database first. FlowPilot creates its tables on start. Workflows and credentials in an existing SQLite file are not moved automatically: export the workflows (Overview, Export) and import them, and add the credentials again. The credential key file (`flowpilot.key`) is still a file, so keep it with your backups and share it between machines.

To try it without installing anything: `pip install pixeltable-pgserver`, then `python pg_test.py -m pytest tests -q` (from `backend`) runs the tests against a throwaway Postgres, and `python e2e_pg.py` runs the browser tests against one.

## <img src="https://img.shields.io/badge/-Configuration-0EA5E9?style=flat-square" alt="Configuration"/>

Everything works with no configuration. These optional environment variables change behaviour (set them before starting the API):

| Variable | Default | What it does |
|---|---|---|
| `FLOWPILOT_DB` | `backend/flowpilot.db` | Where the database file lives (SQLite) |
| `FLOWPILOT_DATABASE_URL` | not set | Store everything in Postgres instead, for example `postgresql://user:password@host:5432/flowpilot`. The tables are created on first start. See "Running on Postgres" below |
| `FLOWPILOT_KEY_FILE` | next to the database | The credential encryption key file |
| `FLOWPILOT_SCHEDULER` | `1` | Set `0` to turn off schedules, email triggers and waiting-run resumption in this process |
| `FLOWPILOT_QUEUE` | off | Set `1` for queue mode (see Limits to know) |
| `FLOWPILOT_SERVE_UI` | off | Set `1` to make the API serve the built interface from `frontend/dist` (production mode) |
| `FLOWPILOT_UI_DIR` | `frontend/dist` | Where the built interface is, when serving it |
| `FLOWPILOT_WEBHOOK_RATE` | `60` | Webhook calls allowed per minute per URL |
| `FLOWPILOT_PUBLIC_URL` | `http://127.0.0.1:5173` | Address used for the OAuth redirect URL |
| `FLOWPILOT_ALLOW_PRIVATE_HOSTS` | off | Set `1` to allow SMTP, IMAP, Postgres and AI endpoints on private networks or `http` (never applies to the HTTP node) |
| `FLOWPILOT_FILES_DIR` | `backend/data/files` | The folder the Files node can use |
| `FLOWPILOT_SQLITE_DIR` | `backend/data` | The folder for SQLite databases used by Database Query |
| `FLOWPILOT_INBOX_ASYNC` | `1` | Set `0` to check inboxes inside the scheduler instead of in the background |

**Health check:** `GET /api/health` (no sign-in needed) returns the version, whether the database answers, whether the scheduler thread is alive and whether queue mode is on. Use it for uptime monitoring.

**Rotating the credential key:** stop the API, back up `flowpilot.db` and `flowpilot.key`, then run `python rotate_key.py` inside `backend` (use the virtual environment). It adds a new key and re-encrypts every credential, keeping the old key as a fallback so a crash part way loses nothing. Once you have checked that workflows still run, run it again with `--drop-old` to remove the old keys.

**Behind a reverse proxy with HTTPS:** the sign-in cookie gets the `Secure` flag when the request is HTTPS or the proxy sends `X-Forwarded-Proto: https`. Responses carry `nosniff`, `no-referrer`, `DENY` framing and `no-store` headers, and API requests over 5 MB are refused (backup restore allows 25 MB). FlowPilot is still designed for your own computer, not the open internet.

**Phones and tablets:** the interface adapts below 900 pixels wide, with a slide-out menu, stacked tables and a bottom-sheet node inspector. Building on a phone works but a larger screen is much easier.

## <img src="https://img.shields.io/badge/-Limits%20to%20know-FB7185?style=flat-square" alt="Limits"/>

- **Local use, with accounts.** There is an owner plus admin, editor and viewer accounts, and sessions use a cookie that is marked secure when served over HTTPS. FlowPilot is still built for one team on one computer or server, not for the open internet: if you expose it, put an HTTPS reverse proxy in front, keep strong passwords, and read the Configuration section.
- **Code node is restricted, not a sandbox.** It blocks imports, file access, dunder attribute tricks and runs in its own process with a time limit, but treat it as a convenience for the owner's own workflows, not as protection against hostile code.
- **Database credentials:** `sqlite:///shop.db` (a file inside `backend/data`) or `postgresql://user:password@host:5432/database?sslmode=require`. Postgres hosts on private networks are blocked unless `FLOWPILOT_ALLOW_PRIVATE_HOSTS=1`. Use a database user with only the rights the workflow needs.
- **Long waits:** a Wait over 60 seconds pauses the run and stores it in the database. It resumes only while the API is running, so a run due while the API was off continues at the next start. It is not allowed inside a loop or a sub-workflow, and a run can have one long wait. A run interrupted mid-resume is closed as failed rather than repeated.
- **OAuth connections:** open Credentials, choose Connect an account, create a free OAuth client with the service, add the shown redirect URL (set `FLOWPILOT_PUBLIC_URL` if you serve FlowPilot on another address), paste the client ID and secret and approve access. Google apps in testing mode need you added as a test user, and their refresh tokens can expire after 7 days until the app is published.
- **Projects control visibility, not secrets.** Members only see the workflows of their projects, but credentials and variables are shared by name with everyone who may use them.
- **AI nodes:** local models use plain http, so start the API with `FLOWPILOT_ALLOW_PRIVATE_HOSTS=1` for them (this never applies to the HTTP node). A reply can take up to 90 seconds.
- **Forms are public** to anyone who has the webhook URL, and are rate limited like any webhook. Do not put secrets in them.
- **Backups:** the workflow file holds no credentials. The database copy includes password hashes and encrypted credentials, so keep it private and keep `backend/flowpilot.key` with it.
- **Expressions** support arithmetic, comparisons, `and` / `or` / `not`, `a if cond else b`, indexing and slicing, `.field` access, lists and objects, the functions `len str int float round min max sum abs sorted bool list any all now today`, and the text methods `upper lower strip title capitalize replace split join startswith endswith find count zfill` plus `get keys values items` on objects. There are no loops, comprehensions, lambdas, imports or calls to anything else, `cred` is not reachable, and incoming data is never re-read as an expression. A name with no spaces such as `{{a-b}}` is still a plain field path, so existing workflows behave exactly as before. Missing fields are empty rather than errors.
- **Queue mode** is optional and off by default. Turn it on by setting `FLOWPILOT_QUEUE=1` before starting the API, then start workers on the same computer with the same `FLOWPILOT_DB` and `FLOWPILOT_KEY_FILE`. Webhooks reply `202 queued` straight away, except workflows with a Respond to Webhook node, which wait up to 25 seconds for a worker. Manual runs from the editor always run directly. A job whose worker disappeared is closed as failed instead of being run again, so side effects are never repeated. `GET /api/queue` shows the queue length.
- **What queue mode is for, honestly:** it keeps the API responsive, survives restarts and spreads work over processes. It is not faster on one computer. In a measurement of 400 webhook calls each waiting 0.3 seconds, the single-process default finished about 85 runs per second, while queue mode with workers finished about 20 per second, because SQLite allows one writer at a time and every job writes to the database. Use queue mode when runs are heavy or long, not to chase throughput.
- **Scale ceiling:** FlowPilot stores everything in one SQLite file (now in WAL mode, so the API and workers do not block each other for reads). That is plenty for one team on one computer. For several machines or much higher throughput it needs a client-server database, which is not built yet.
- **Webhook rate limit:** 60 calls per minute per URL by default. Raise it with `FLOWPILOT_WEBHOOK_RATE`.
- **Backups:** because of WAL mode, copying `flowpilot.db` alone while the API runs can miss recent changes. Use the Backup page in Settings, which makes a consistent copy.
- **Parallel branches:** only network, database, AI, code and file nodes run in parallel, so loops and branch logic stay predictable. If one parallel branch fails, the others in that group still finish.
- **Files** live in `backend/data/files` (override with `FLOWPILOT_FILES_DIR`). Binary data is passed around as base64 inside JSON. Webhook uploads are stored under `uploads/` there.
- **New Items trigger:** checks run while FlowPilot is running, and items that appear and disappear between two checks are missed. An API list without a stable identifier field is matched by its whole content, so an item that changes counts as new.
- **Email trigger:** a message is marked read when picked up, so a failed run is not retried (the failed run keeps the message). Gmail needs an app password.
- **Postgres storage** is supported but newer than SQLite: the whole test suite and the 70 browser checks pass against a real Postgres 16, but it has not been run under long, heavy production load, and moving existing SQLite data across is not automated (export workflows and import them). The whole-database download is SQLite only; on Postgres use `pg_dump`.
- **Not yet:** the OAuth flow was tested with simulated providers, not against live Google.
- **Triggers need the API running.** Schedules and webhooks only work while the API window is open, and you should run a single API process (two would run each schedule twice). Cron expressions, signed webhooks, OAuth connectors, loops and queues are on the [roadmap](ROADMAP.md).
- **Webhook URLs are local.** They work on this computer. To receive calls from other services you would have to expose the app yourself (for example with a tunnel). That also exposes the login page and API, so only do it if you understand the risk.
- **One incoming connection per node, except Merge.** Branches come back together through a Merge node, and cycles are rejected (use Loop Over Items or Repeat Until). One node may fan out to several.
- **HTTP checks are an MVP safeguard.** Public-HTTPS-only, private-address blocking and no redirects are not a hardened defence for untrusted multi-tenant use. Only run workflows you trust. Use the Credentials page for secrets instead of typing them into a node.
- **Run history can hold sensitive data.** Credentials are hidden from results, but data that passes through a workflow is stored. Use the retention settings, and the "keep only failed runs" workflow setting, to limit what is kept.

---

## <img src="https://img.shields.io/badge/-Roadmap-F59E0B?style=flat-square" alt="Roadmap"/>

Scheduled and webhook triggers, credentials, more connectors, loops, merge nodes and a Postgres option are planned. See [ROADMAP.md](ROADMAP.md).

## Costs and licences

Running locally costs nothing. React, React Flow, FastAPI, SQLite and the other dependencies are free and open source. The web app optionally loads the Inter and JetBrains Mono fonts from Google Fonts and falls back to system fonts when offline. Any third-party API you call from an HTTP node may have its own terms and costs.
