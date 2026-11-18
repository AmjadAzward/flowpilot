"""Best-effort conversion of an n8n workflow export into a FlowPilot workflow.

Nothing here runs workflows or touches the database. `convert()` returns the new nodes and edges plus a list of
warnings: things that could not be carried over and need a person's attention. Unknown nodes become Pass Through nodes
so the shape of the workflow survives, and expressions that cannot be translated are left as they were so they fail
loudly at run time instead of silently producing wrong data.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_NODES = 60

# A reference to the incoming item in n8n expressions: $json.a.b, $json["a"], $json['a'][0] ...
PATH_PART = r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\s*'[^']*'\s*\]|\[\s*\"[^\"]*\"\s*\]|\[\s*\d+\s*\])"
JSON_REF = re.compile(r"\$(?:json|input\.item\.json)(" + PATH_PART + r"*)")
CROSS_REF = re.compile(r"\$\(\s*['\"][^'\"]+['\"]\s*\)|\$node\[|\$items\(|\$input\.all|\$prevNode|\$workflow|\$execution|\$now|\$today|\$env|\$vars|\$binary")
PART = re.compile(r"\.([A-Za-z_]\w*)|\[\s*'([^']*)'\s*\]|\[\s*\"([^\"]*)\"\s*\]|\[\s*(\d+)\s*\]")

OP_MAP = {
    "equals": "equals", "equal": "equals", "is": "equals", "notEquals": "not_equals", "notEqual": "not_equals", "isNot": "not_equals", "notequal": "not_equals",
    "contains": "contains", "gt": "gt", "larger": "gt", "gte": "gt", "largerEqual": "gt", "lt": "lt", "smaller": "lt", "lte": "lt", "smallerEqual": "lt",
    "exists": "exists", "notExists": "not_exists", "empty": "not_exists", "isEmpty": "not_exists", "notEmpty": "exists", "isNotEmpty": "exists",
}
APPROX_OPS = {"gte": "gt", "largerEqual": "gt", "lte": "lt", "smallerEqual": "lt"}
UNIT_MAP = {"seconds": "seconds", "second": "seconds", "minutes": "minutes", "minute": "minutes", "hours": "hours", "hour": "hours", "days": "days", "day": "days", "weeks": "weeks", "week": "weeks"}


class Converter:
    def __init__(self) -> None:
        self.warnings: list[dict] = []
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.ids: dict[str, str] = {}      # n8n node name -> FlowPilot id
        self.types: dict[str, str] = {}    # n8n node name -> FlowPilot type
        self.placeholders = 0
        self.credentials: set[str] = set()

    # ---------------------------------------------------------------- helpers
    def warn(self, node: str, message: str) -> None:
        self.warnings.append({"node": node, "message": message})

    def expr_inner(self, inner: str, node: str) -> str:
        """Translate the inside of one {{ ... }} from n8n to FlowPilot syntax."""
        text = inner.strip()
        if CROSS_REF.search(text):
            self.warn(node, f"The expression {{{{ {text[:70]} }}}} refers to another node or a built-in value that FlowPilot cannot read. Rewrite it by hand.")
            return inner
        whole = JSON_REF.fullmatch(text)
        if whole is not None:  # a plain path: the simple form works everywhere, as long as every key is a simple word
            names = [next(g for g in m if g) for m in PART.findall(whole.group(1))]
            if all(re.fullmatch(r"[A-Za-z0-9_\-]+", x) for x in names): return ".".join(names)

        def sub(m: re.Match) -> str:
            out = ""
            for dot, single, double, index in PART.findall(m.group(1)):
                if dot: out += f".{dot}"
                elif index: out += f"[{index}]"
                else: out += f"['{single or double}']"
            return ("data" + out) if out else "data"

        converted = JSON_REF.sub(sub, text)
        if "$" in converted or "=>" in converted:
            self.warn(node, f"The expression {{{{ {text[:70]} }}}} uses JavaScript that FlowPilot cannot run. Rewrite it by hand.")
            return inner
        if "data" in converted:
            self.warn(node, f"The expression {{{{ {text[:70]} }}}} was translated but should be checked: FlowPilot expressions are Python-style (for example name.upper() and 'a' if x else 'b').")
        return converted

    def text(self, value: Any, node: str) -> str:
        """An n8n parameter value as FlowPilot text. Strings starting with = are expressions."""
        if value is None: return ""
        if not isinstance(value, str): return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if not value.startswith("="): return value
        body = value[1:]
        return re.sub(r"\{\{(.*?)\}\}", lambda m: "{{" + self.expr_inner(m.group(1), node) + "}}", body, flags=re.S)

    def path_of(self, value: Any, node: str, what: str = "field") -> str:
        """A condition's left side as a plain field path (FlowPilot compares one field)."""
        converted = self.text(value, node).strip()
        m = re.fullmatch(r"\{\{\s*([A-Za-z0-9_.\-]*)\s*\}\}", converted)
        if m: return m.group(1)
        if converted and re.fullmatch(r"[A-Za-z0-9_.\-]*", converted) and not str(value).startswith("="): return converted
        self.warn(node, f"The {what} {str(value)[:60]!r} is not a simple field, so it was left empty. Choose the field by hand.")
        return ""

    # ---------------------------------------------------------------- node conversions
    def convert_node(self, n: dict) -> tuple[str, dict]:
        full = str(n.get("type") or "")
        kind = full.rsplit(".", 1)[-1]
        p = n.get("parameters") or {}
        name = str(n.get("name") or kind)
        if not isinstance(p, dict): p = {}
        handler = getattr(self, "n_" + re.sub(r"[^A-Za-z0-9]", "_", kind), None)
        if handler is None:
            self.placeholders += 1
            self.warn(name, f"The n8n node type '{full}' has no FlowPilot equivalent yet. It became a Pass Through node so the rest of the workflow keeps its shape.")
            return "note", {}
        return handler(name, p, n)

    def n_manualTrigger(self, name, p, n): return "trigger", {}
    n_start = n_manualTrigger

    def n_scheduleTrigger(self, name, p, n):
        rules = ((p.get("rule") or {}).get("interval")) or []
        r = rules[0] if rules and isinstance(rules[0], dict) else {}
        field = r.get("field")
        if len(rules) > 1: self.warn(name, "Only the first schedule rule was kept.")
        if field == "minutes": return "schedule", {"mode": "interval", "minutes": str(r.get("minutesInterval") or 5)}
        if field == "hours": return "schedule", {"mode": "interval", "minutes": str(int(float(r.get("hoursInterval") or 1)) * 60)}
        if field == "days": return "schedule", {"mode": "daily", "time": f"{int(float(r.get('triggerAtHour') or 0)):02d}:{int(float(r.get('triggerAtMinute') or 0)):02d}"}
        if field == "cronExpression": return "schedule", {"mode": "cron", "cron": str(r.get("expression") or "0 * * * *")}
        self.warn(name, "This schedule rule is not supported. It was set to run every 60 minutes.")
        return "schedule", {"mode": "interval", "minutes": "60"}

    def n_cron(self, name, p, n):
        times = ((p.get("triggerTimes") or {}).get("item")) or []
        t = times[0] if times else {}
        mode = t.get("mode")
        if mode == "everyX" and t.get("unit") in ("minutes", "hours"):
            return "schedule", {"mode": "interval", "minutes": str(int(float(t.get("value") or 1)) * (60 if t.get("unit") == "hours" else 1))}
        if mode == "custom": return "schedule", {"mode": "cron", "cron": str(t.get("cronExpression") or "0 * * * *")}
        self.warn(name, "This cron setting is not supported. It was set to run every 60 minutes.")
        return "schedule", {"mode": "interval", "minutes": "60"}

    def n_webhook(self, name, p, n):
        self.warn(name, "FlowPilot creates its own webhook address. Activate the workflow, then copy the new URL into whatever calls it. The path and method from n8n are not kept.")
        return "webhook", {"sample": "{}"}

    def n_rssFeedReadTrigger(self, name, p, n):
        return "poll_in", {"source": "rss", "url": self.text(p.get("feedUrl"), name), "minutes": "15"}

    def n_set(self, name, p, n):
        pairs: list[tuple[str, Any, str]] = []
        for a in ((p.get("assignments") or {}).get("assignments")) or []:
            pairs.append((str(a.get("name") or ""), a.get("value"), str(a.get("type") or "string")))
        values = p.get("values") or {}
        for typ in ("string", "number", "boolean"):
            for v in values.get(typ) or []: pairs.append((str(v.get("name") or ""), v.get("value"), typ))
        for v in ((p.get("fields") or {}).get("values")) or []:
            pairs.append((str(v.get("name") or ""), v.get("stringValue", v.get("numberValue", v.get("booleanValue"))), "string"))
        if p.get("includeOtherFields") is True or p.get("keepOnlySet") is False:
            self.warn(name, "n8n kept the other fields as well. FlowPilot's Set Fields builds a new object, so add the fields you still need.")
        parts = []
        for key, value, typ in pairs:
            if not key: continue
            converted = self.text(value, name)
            whole_expr = isinstance(value, str) and value.startswith("=") and re.fullmatch(r"\{\{.*\}\}", converted.strip(), flags=re.S)
            if typ in ("number", "boolean") and whole_expr: parts.append(f"{json.dumps(key)}: {converted.strip()}")
            elif typ == "number" and isinstance(converted, str) and re.fullmatch(r"-?\d+(\.\d+)?", converted): parts.append(f"{json.dumps(key)}: {converted}")
            elif typ == "boolean" and str(converted).lower() in ("true", "false"): parts.append(f"{json.dumps(key)}: {str(converted).lower()}")
            else: parts.append(f"{json.dumps(key)}: {json.dumps(converted)}")
        return "set", {"json": "{" + ", ".join(parts) + "}"}

    def first_condition(self, name, p):
        conds = ((p.get("conditions") or {}).get("conditions")) if isinstance(p.get("conditions"), dict) else None
        if conds:
            c = conds[0]
            op = c.get("operator") or {}
            return c.get("leftValue"), str(op.get("operation") or "equals"), c.get("rightValue"), len(conds)
        legacy = p.get("conditions") or {}
        for typ in ("number", "string", "boolean"):
            if legacy.get(typ):
                c = legacy[typ][0]
                return c.get("value1"), str(c.get("operation") or "equal"), c.get("value2"), len(legacy[typ])
        return None, "equals", None, 0

    def condition_fields(self, name, p) -> dict:
        left, op, right, count = self.first_condition(name, p)
        if count == 0:
            self.warn(name, "No condition could be read. Set the comparison by hand.")
            return {"path": "", "op": "equals", "equals": ""}
        if count > 1: self.warn(name, "Only the first of several conditions was kept.")
        if op not in OP_MAP:
            self.warn(name, f"The comparison '{op}' is not available in FlowPilot. It was set to 'equals'; change it by hand.")
        elif op in APPROX_OPS:
            self.warn(name, f"'{op}' (or equal) was approximated as strictly {OP_MAP[op]}. Adjust the value if equal values matter.")
        return {"path": self.path_of(left, name), "op": OP_MAP.get(op, "equals"), "equals": self.text(right, name)}

    def n_if(self, name, p, n): return "condition", self.condition_fields(name, p)

    def n_filter(self, name, p, n): return "filter", self.condition_fields(name, p)

    def n_switch(self, name, p, n):
        rules = ((p.get("rules") or {}).get("values")) or ((p.get("rules") or {}).get("rules")) or []
        data = {"path": "", "case1": "", "case2": "", "case3": "", "case4": ""}
        for i, rule in enumerate(rules[:4]):
            sub = self.condition_fields(name, {"conditions": rule.get("conditions")}) if isinstance(rule, dict) and rule.get("conditions") else {"path": "", "equals": rule.get("outputKey", "") if isinstance(rule, dict) else ""}
            if i == 0: data["path"] = sub["path"]
            data[f"case{i + 1}"] = str(sub.get("equals") or "")
            if sub.get("op") not in (None, "equals"): self.warn(name, f"Switch output {i + 1} compared with '{sub['op']}'. FlowPilot's Switch only tests 'equals'.")
        if len(rules) > 4: self.warn(name, "FlowPilot's Switch has four outputs. Outputs 5 and above were dropped.")
        return "switch", data

    def n_httpRequest(self, name, p, n):
        data: dict[str, str] = {"url": self.text(p.get("url"), name), "method": str(p.get("method") or "GET").upper(), "headers": "", "body": ""}
        headers = {}
        for h in ((p.get("headerParameters") or {}).get("parameters")) or []:
            if h.get("name"): headers[str(h["name"])] = self.text(h.get("value"), name)
        if headers: data["headers"] = json.dumps(headers)
        if p.get("sendBody"):
            if p.get("jsonBody") is not None and p.get("specifyBody", "json") == "json": data["body"] = self.text(p.get("jsonBody"), name)
            else:
                params = ((p.get("bodyParameters") or {}).get("parameters")) or []
                data["body"] = json.dumps({str(x.get("name")): self.text(x.get("value"), name) for x in params if x.get("name")})
        if p.get("sendQuery"):
            self.warn(name, "Query parameters were not carried over. Add them to the URL.")
        if p.get("authentication") not in (None, "none") or n.get("credentials"):
            self.warn(name, "Authentication is not transferred. Save a credential and choose it in the node's Authentication setting.")
        return "http", data

    def n_code(self, name, p, n):
        lang = str(p.get("language") or "javaScript")
        src = str(p.get("jsCode") or p.get("pythonCode") or p.get("code") or "")
        self.warn(name, "A Code node cannot be converted automatically. The original code is kept as comments; FlowPilot's Code node runs Python and reads 'input' and 'result'.")
        commented = "\n".join("# " + line for line in src.splitlines()[:200])
        return "code", {"code": f"# Converted from an n8n {lang} Code node. Original code:\n{commented}\n\nresult = input\n"}

    def n_merge(self, name, p, n):
        mode = str(p.get("mode") or "append")
        if mode in ("combine", "mergeByPosition", "mergeByFields"): return "merge", {"mode": "combine"}
        if mode in ("chooseBranch",): return "merge", {"mode": "first"}
        if mode != "append": self.warn(name, f"The merge mode '{mode}' was set to append.")
        return "merge", {"mode": "append"}

    def n_noOp(self, name, p, n): return "note", {}

    def n_stickyNote(self, name, p, n): return "sticky", {"text": str(p.get("content") or "")}

    def n_wait(self, name, p, n):
        unit = UNIT_MAP.get(str(p.get("unit") or "hours"), "hours")
        return "wait", {"seconds": str(p.get("amount") or 1), "unit": unit}

    def n_respondToWebhook(self, name, p, n):
        body = p.get("responseBody") if p.get("respondWith") in ("json", "text") else ""
        code = ((p.get("options") or {}).get("responseCode")) or 200
        return "respond", {"status": str(code), "body": self.text(body, name)}

    def n_sort(self, name, p, n):
        fields = ((p.get("sortFieldsUi") or {}).get("sortField")) or []
        f = fields[0] if fields else {}
        if len(fields) > 1: self.warn(name, "Only the first sort field was kept.")
        return "sort", {"path": str(f.get("fieldName") or ""), "order": "desc" if f.get("order") == "descending" else "asc"}

    def n_limit(self, name, p, n):
        return "limit", {"count": str(p.get("maxItems") or 1), "from": "last" if p.get("keep") == "lastItems" else "first"}

    def n_removeDuplicates(self, name, p, n):
        fields = [x.strip() for x in str(p.get("fieldsToCompare") or "").split(",") if x.strip()]
        if len(fields) > 1: self.warn(name, "Only the first comparison field was kept.")
        return "dedupe", {"path": fields[0] if fields else ""}

    def n_splitOut(self, name, p, n):
        return "split", {"path": str(p.get("fieldToSplitOut") or "")}

    def n_summarize(self, name, p, n):
        v = ((p.get("fieldsToSummarize") or {}).get("values")) or []
        first = v[0] if v else {}
        ag = {"sum": "sum", "average": "avg", "min": "min", "max": "max", "count": "count", "append": "collect"}.get(str(first.get("aggregation") or "count"), "count")
        if len(v) > 1 or p.get("fieldsToSplitBy"): self.warn(name, "Only the first summary was kept, and grouping is not supported.")
        return "aggregate", {"operation": ag, "path": str(first.get("field") or "")}

    def n_dateTime(self, name, p, n):
        action = str(p.get("action") or p.get("operation") or "format")
        value = self.text(p.get("date") or p.get("value") or "", name)
        if action in ("addToDate", "subtractFromDate"):
            return "datetime", {"operation": "add" if action == "addToDate" else "subtract", "value": self.text(p.get("magnitude") or p.get("date") or "", name),
                                "amount": self.text(p.get("duration") or 1, name), "unit": UNIT_MAP.get(str(p.get("timeUnit") or "days"), "days"), "other": "", "format": "%Y-%m-%d"}
        if action == "formatDate":
            fmt = str(p.get("format") or "yyyy-MM-dd")
            for a, b in (("yyyy", "%Y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")): fmt = fmt.replace(a, b)
            self.warn(name, "The date format was translated. Check it, because n8n and FlowPilot use different format codes.")
            return "datetime", {"operation": "format", "value": value, "amount": "1", "unit": "days", "other": "", "format": fmt}
        self.warn(name, f"The date action '{action}' is not supported. It became a 'format a date' node.")
        return "datetime", {"operation": "format", "value": value, "amount": "1", "unit": "days", "other": "", "format": "%Y-%m-%d"}

    def n_crypto(self, name, p, n):
        action = str(p.get("action") or "hash")
        if action == "hash":
            algo = {"SHA256": "sha256", "SHA512": "sha512", "SHA1": "sha1", "MD5": "md5"}.get(str(p.get("type") or "SHA256").upper(), "sha256")
            return "crypto", {"operation": algo, "value": self.text(p.get("value"), name), "key": ""}
        if action == "hmac":
            self.warn(name, "Put the secret on the Credentials page and use {{cred.NAME}} as the key.")
            return "crypto", {"operation": "hmac_sha256", "value": self.text(p.get("value"), name), "key": ""}
        self.warn(name, f"The crypto action '{action}' is not supported. It became a SHA-256 hash.")
        return "crypto", {"operation": "sha256", "value": self.text(p.get("value"), name), "key": ""}

    def n_slack(self, name, p, n):
        ch = p.get("channelId")
        channel = ch.get("value") if isinstance(ch, dict) else ch
        self.warn(name, "Choose a Slack bot token credential in the node. n8n's saved connections are never exported.")
        fields = {"channel": self.text(channel, name), "text": self.text(p.get("text"), name)}
        return "app", {"service": "slack", "operation": "post_message", "credential": "", "fields": json.dumps(fields)}

    def n_telegram(self, name, p, n):
        self.warn(name, "Save the bot token on the Credentials page and choose it in the node.")
        return "telegram", {"credential": "", "chat_id": self.text(p.get("chatId"), name), "text": self.text(p.get("text"), name)}

    def n_github(self, name, p, n):
        owner = p.get("owner"); repo = p.get("repository")
        owner = owner.get("value") if isinstance(owner, dict) else owner
        repo = repo.get("value") if isinstance(repo, dict) else repo
        op = "create_issue" if str(p.get("operation") or "create") == "create" else "list_issues"
        if p.get("resource") not in (None, "issue"): self.warn(name, "Only issues are supported by FlowPilot's GitHub node.")
        self.warn(name, "Save a GitHub token on the Credentials page and choose it in the node.")
        return "github", {"credential": "", "repo": f"{self.text(owner, name)}/{self.text(repo, name)}", "operation": op, "title": self.text(p.get("title"), name), "body": self.text(p.get("body"), name), "state": "open"}

    def n_emailSend(self, name, p, n):
        self.warn(name, "Save an SMTP credential and choose it in the node.")
        return "email", {"credential": "", "to": self.text(p.get("toEmail"), name), "subject": self.text(p.get("subject"), name), "body": self.text(p.get("text") or p.get("html") or "", name)}

    def n_rssFeedRead(self, name, p, n): return "rss", {"url": self.text(p.get("url"), name), "limit": "10"}

    def n_gmail(self, name, p, n):
        self.warn(name, "Connect a Google account on the Credentials page and choose it in the node.")
        return "gmail", {"credential": "", "to": self.text(p.get("sendTo"), name), "subject": self.text(p.get("subject"), name), "body": self.text(p.get("message"), name)}

    def n_googleSheets(self, name, p, n):
        self.warn(name, "Connect a Google account, then check the spreadsheet ID and range. Only reading and appending rows are supported.")
        doc = p.get("documentId"); sheet = p.get("sheetName")
        doc = doc.get("value") if isinstance(doc, dict) else doc
        sheet = sheet.get("cachedResultName") or sheet.get("value") if isinstance(sheet, dict) else sheet
        op = "append" if str(p.get("operation") or "read") in ("append", "appendOrUpdate") else "read"
        return "sheets", {"credential": "", "spreadsheet_id": self.text(doc, name), "range": self.text(sheet or "Sheet1", name), "operation": op, "header": "true", "values": '["{{name}}"]'}

    def n_executeWorkflow(self, name, p, n):
        self.warn(name, "Sub-workflows are chosen by ID, and the IDs are different in FlowPilot. It became a Pass Through node; use Run Workflow and pick the workflow.")
        return "note", {}

    def n_splitInBatches(self, name, p, n):
        self.warn(name, "n8n loops with a connection back to this node. FlowPilot's Loop Over Items works differently (no cycles), so this became a Pass Through node. Rebuild the loop with Loop Over Items.")
        return "note", {}


def out_handle(kind: str, fp_type: str, index: int) -> tuple[str | None, bool]:
    """The FlowPilot source handle for output `index` of an n8n node, and whether that output can be kept."""
    if fp_type == "condition": return ("true" if index == 0 else "false"), index < 2
    if fp_type == "switch": return (str(index + 1) if index < 4 else None), index < 4
    return None, index == 0 or fp_type == "note"


def convert(doc: Any) -> dict:
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list) or not isinstance(doc.get("connections", {}), dict):
        raise ValueError("This does not look like an n8n workflow export. It needs a 'nodes' list and a 'connections' object.")
    src_nodes = [n for n in doc["nodes"] if isinstance(n, dict)]
    if not src_nodes: raise ValueError("The n8n workflow has no nodes.")
    if len(src_nodes) > MAX_NODES: raise ValueError(f"The workflow has {len(src_nodes)} nodes. FlowPilot allows {MAX_NODES}.")
    c = Converter()
    used_ids: set[str] = set()
    seen_trigger = False

    for i, n in enumerate(src_nodes):
        name = str(n.get("name") or f"Node {i + 1}")
        base = re.sub(r"[^A-Za-z0-9_-]", "_", str(n.get("id") or f"n{i + 1}"))[:30] or f"n{i + 1}"
        nid = base
        while nid in used_ids: nid += "x"
        used_ids.add(nid)
        if name in c.ids: name = f"{name} {i + 1}"
        n = {**n, "name": name}
        kind = str(n.get("type") or "").rsplit(".", 1)[-1]
        if n.get("disabled") is True:
            fp_type, data = "note", {}
            c.warn(name, "This node was disabled in n8n, so it became a Pass Through node.")
        else:
            fp_type, data = c.convert_node(n)
        if fp_type in ("trigger", "schedule", "webhook", "poll_in", "email_in"):
            if seen_trigger:
                c.warn(name, "FlowPilot allows one trigger per workflow. This extra trigger became a Pass Through node. Make a separate workflow for it.")
                fp_type, data = "note", {}
            seen_trigger = seen_trigger or fp_type != "note"
        creds = n.get("credentials")
        if isinstance(creds, dict):
            for cname in creds: c.credentials.add(str(cname))
        pos = n.get("position") if isinstance(n.get("position"), list) and len(n["position"]) == 2 else [i * 240, 120]
        label = name[:60]
        c.nodes.append({"id": nid, "type": fp_type, "position": {"x": float(pos[0]), "y": float(pos[1])}, "data": {"label": label, **{k: v for k, v in data.items()}}})
        c.ids[name] = nid
        c.types[name] = fp_type
        c.nodes[-1]["_kind"] = kind

    seen_edges: set[tuple] = set()
    incoming: dict[str, int] = {n["id"]: 0 for n in c.nodes}
    for src_name, outputs in (doc.get("connections") or {}).items():
        if src_name not in c.ids or not isinstance(outputs, dict): continue
        for conn_type, groups in outputs.items():
            if conn_type != "main" or not isinstance(groups, list): continue
            for index, targets in enumerate(groups):
                handle, ok = out_handle("", c.types[src_name], index)
                for t in targets or []:
                    tname = t.get("node") if isinstance(t, dict) else None
                    if tname not in c.ids: continue
                    if not ok:
                        c.warn(src_name, f"Output {index + 1} of this node has no FlowPilot equivalent, so its connection to '{tname}' was dropped.")
                        continue
                    key = (c.ids[src_name], c.ids[tname], handle)
                    if key in seen_edges: continue
                    seen_edges.add(key)
                    edge = {"id": f"{key[0]}-{key[1]}" + (f"-{handle}" if handle else ""), "source": key[0], "target": key[1]}
                    if handle: edge["sourceHandle"] = handle
                    c.edges.append(edge)
                    incoming[key[1]] += 1

    for n in c.nodes: n.pop("_kind", None)
    trigger_ids = [n["id"] for n in c.nodes if n["type"] in ("trigger", "schedule", "webhook", "poll_in", "email_in")]
    if not trigger_ids:
        roots = [n for n in c.nodes if incoming[n["id"]] == 0 and n["type"] != "sticky"]
        x = min((n["position"]["x"] for n in c.nodes), default=0) - 240
        c.nodes.insert(0, {"id": "start", "type": "trigger", "position": {"x": x, "y": roots[0]["position"]["y"] if roots else 120}, "data": {"label": "Manual Trigger"}})
        for r in roots: c.edges.append({"id": f"start-{r['id']}", "source": "start", "target": r["id"]})
        for r in roots: incoming[r["id"]] += 1
        c.warn("(workflow)", "The n8n workflow had no trigger FlowPilot understands, so a Manual Trigger was added at the start.")

    for n in c.nodes:
        if incoming.get(n["id"], 0) > 1 and n["type"] != "merge":
            c.warn(n["data"]["label"], "Several branches feed into this node. FlowPilot only allows that for a Merge node, so add a Merge node before it.")
    if _has_cycle(c.nodes, c.edges):
        c.warn("(workflow)", "The workflow contains a loop back to an earlier node. FlowPilot does not support cycles, so it must be rebuilt (see Loop Over Items and Repeat Until).")
    if c.credentials:
        c.warn("(workflow)", "These n8n credentials were not exported and must be recreated on the Credentials page: " + ", ".join(sorted(c.credentials)) + ".")

    return {"name": str(doc.get("name") or "Imported from n8n")[:120], "description": "Imported from n8n. Check the warnings before activating.", "nodes": c.nodes, "edges": c.edges,
            "warnings": c.warnings, "summary": {"nodes": len(src_nodes), "placeholders": c.placeholders, "warnings": len(c.warnings)}}


def _has_cycle(nodes: list[dict], edges: list[dict]) -> bool:
    out: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges: out[e["source"]].append(e["target"])
    state: dict[str, int] = {}

    def visit(start: str) -> bool:
        stack = [(start, iter(out[start]))]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            for nxt in it:
                if state.get(nxt) == 1: return True
                if nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(out[nxt])))
                    break
            else:
                state[node] = 2
                stack.pop()
        return False

    return any(n["id"] not in state and visit(n["id"]) for n in nodes)
