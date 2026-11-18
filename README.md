<div align="center">

<img src="docs/banner.svg" alt="FlowPilot" width="100%"/>

<br/>

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Tests](https://img.shields.io/badge/tests-126%20API%20%2B%2027%20browser-34D399?style=for-the-badge)
![Auth](https://img.shields.io/badge/login-built%20in-E9522A?style=for-the-badge)
![Triggers](https://img.shields.io/badge/triggers-schedule%20%2B%20webhook-F43F5E?style=for-the-badge)
![Cost](https://img.shields.io/badge/cost-free-F59E0B?style=for-the-badge)

**Design workflows visually. Run them on a schedule or from a webhook. Branch on conditions. Call APIs. Inspect every run.**
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
| **Run on their own** | Schedule trigger (every N minutes or daily at a time) and Webhook trigger (a secret URL that starts the workflow), with an Active switch per workflow |
| **Eleven node types** | Manual, Schedule and Webhook triggers, JSON Input, Extract Field, Text Template, Compare Value, Delay, HTTP Request, Pass Through, Result Output |
| **Full HTTP requests** | GET, POST, PUT, PATCH and DELETE with custom headers and a JSON or text body. `{{field}}` placeholders work in the URL, headers and body |
| **Seven comparison operators** | equals, does not equal, contains, greater than, less than, exists, does not exist |
| **Run feedback** | Per-node success, error and skipped badges on the canvas, plus per-step timings in milliseconds |
| **Templates** | Six ready-made workflows: greeting, order triage (branching), fetch-and-extract, webhook receiver, hourly API check and send-data-to-an-API (POST) |
| **Workflow management** | Create, rename, duplicate, delete, import and export as JSON, run directly from the dashboard |
| **Execution history** | Every run stored with output and steps, filterable by status, with one-click clear |
| **Editor comfort** | Unsaved-change indicator, Ctrl+S to save, light and dark themes, branded browser tab title and icon |
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

**Schedule trigger.** Choose *every N minutes* (1 to 10080) or *every day at a time* (24-hour, in this computer's time zone). The first run happens one interval after you activate it. If the API was off when a run was due, it runs once on the next start and then resumes its normal rhythm. The node receives `{{scheduled_at}}`.

**Webhook trigger.** Click the node to see its URL, for example `http://127.0.0.1:5173/api/hooks/<secret>`. Once the workflow is Active, any `POST` or `GET` to that URL starts it:

```bash
curl -X POST "http://127.0.0.1:5173/api/hooks/<secret>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada","message":"Hello"}'
```

Later nodes can read `{{body.name}}`, `{{query.x}}`, `{{method}}` and `{{content_type}}`. The call returns `{"status":"success","execution_id":"..."}` (HTTP 200), or HTTP 500 with `"status":"failed"` if the workflow failed. It never returns your workflow's data. Use **Generate new URL** in the node settings to replace a leaked URL. Unknown, inactive or wrong-type URLs all answer `404`.

**Testing before you activate.** *Run workflow* works on any workflow. A webhook workflow runs with the node's *Test payload*; a schedule workflow runs with a sample event.

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
| HTTP Request | method, public HTTPS URL, headers (JSON), body | parsed JSON, or text if the response is not JSON |
| Pass Through | none | the data it received, unchanged |
| Result Output | none | the final result shown in the run panel |

---

## <img src="https://img.shields.io/badge/-Testing-34D399?style=flat-square" alt="Testing"/>

**API, engine, login, trigger and HTTP tests** (126 tests, use a temporary database):

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

**Browser end-to-end tests** (27 checks driving a real Chrome or Edge: account setup, sign in and out, activation, a webhook called from a separate session, schedules, HTTP node fields and more). These create and delete workflows, so start the API on a throwaway database first:

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
  start-windows.bat          one-click launcher for Windows
  start-mac-linux.sh         launcher for macOS and Linux
  backend/
    app/main.py              API, login and sessions, SQLite storage, execution engine
    reset_password.py        removes the owner account so you can set a new one
    tests/                   pytest suite
    requirements.txt
  frontend/
    src/App.tsx              auth gate, dashboard, editor, executions, settings
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

## <img src="https://img.shields.io/badge/-Limits%20to%20know-FB7185?style=flat-square" alt="Limits"/>

- **Single user, local use.** There is one owner account and sessions run over plain HTTP on localhost. It is not hardened for the public internet: do not expose port 8000 or forward it on your router.
- **Triggers need the API running.** Schedules and webhooks only work while the API window is open, and you should run a single API process (two would run each schedule twice). Cron expressions, signed webhooks, OAuth connectors, loops and queues are on the [roadmap](ROADMAP.md).
- **Webhook URLs are local.** They work on this computer. To receive calls from other services you would have to expose the app yourself (for example with a tunnel). That also exposes the login page and API, so only do it if you understand the risk.
- **One incoming connection per node.** Branches cannot merge back together yet, and cycles are rejected. One node may fan out to several.
- **HTTP checks are an MVP safeguard.** Public-HTTPS-only, private-address blocking and no redirects are not a hardened defence for untrusted multi-tenant use. Only run workflows you trust. There is no credential store yet, so API keys typed into headers are saved in the workflow.
- **Run history can hold sensitive data.** Use sample data for now.
- **Backups:** stop the API, then copy `backend/flowpilot.db`.

---

## <img src="https://img.shields.io/badge/-Roadmap-F59E0B?style=flat-square" alt="Roadmap"/>

Scheduled and webhook triggers, credentials, more connectors, loops, merge nodes and a Postgres option are planned. See [ROADMAP.md](ROADMAP.md).

## Costs and licences

Running locally costs nothing. React, React Flow, FastAPI, SQLite and the other dependencies are free and open source. The web app optionally loads the Inter and JetBrains Mono fonts from Google Fonts and falls back to system fonts when offline. Any third-party API you call from an HTTP node may have its own terms and costs.
