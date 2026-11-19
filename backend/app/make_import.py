"""Best-effort conversion of a Make (Integromat) scenario blueprint into a FlowPilot workflow.

Written from Make's documented blueprint layout ({"name", "flow": [modules], "metadata"}); it has not been tried against a
large set of real exports, so every unknown module becomes a Pass Through node and everything uncertain is reported as a
warning. Nothing here runs workflows or touches the database.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_MODULES = 60
REF = re.compile(r"^(\d+)((?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*)$")


class Maker:
    def __init__(self) -> None:
        self.warnings: list[dict] = []
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.placeholders = 0
        self.allowed: set[str] = set()   # ids of modules whose output the current module may reference

    def warn(self, node: str, message: str) -> None:
        self.warnings.append({"node": node, "message": message})

    def text(self, value: Any, node: str) -> str:
        """A mapper value as FlowPilot text. {{12.field}} becomes {{field}} when module 12 feeds this one."""
        if value is None: return ""
        if not isinstance(value, str): return json.dumps(value) if isinstance(value, (dict, list)) else str(value)

        def sub(m: re.Match) -> str:
            inner = m.group(1).strip()
            ref = REF.fullmatch(inner)
            if ref and ref.group(1) in self.allowed:
                path = ref.group(2).lstrip(".")
                return "{{" + path + "}}" if path else "{{.}}"
            self.warn(node, f"The value {{{{{inner[:60]}}}}} refers to another module or uses a Make function, which FlowPilot cannot read. Rewrite it by hand.")
            return m.group(0)
        return re.sub(r"\{\{(.*?)\}\}", sub, value, flags=re.S)

    def pairs(self, items: Any, node: str) -> dict:
        out = {}
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("name"): out[str(it["name"])] = self.text(it.get("value"), node)
        return out

    def convert(self, mod: dict, label: str) -> tuple[str, dict]:
        kind = str(mod.get("module") or "")
        m = mod.get("mapper") if isinstance(mod.get("mapper"), dict) else {}
        if kind in ("gateway:CustomWebHook", "gateway:CustomMailhook"):
            self.warn(label, "FlowPilot creates its own webhook address. Activate the workflow, then copy the new URL into whatever calls it.")
            return "webhook", {"sample": "{}"}
        if kind == "builtin:BasicRouter": return "note", {}
        if kind in ("http:ActionSendData", "http:ActionSendDataAPIKeyAuth", "http:ActionSendDataBasicAuth"):
            data = {"url": self.text(m.get("url"), label), "method": str(m.get("method") or "get").upper(), "headers": "", "body": ""}
            headers = self.pairs(m.get("headers"), label)
            if headers: data["headers"] = json.dumps(headers)
            if m.get("data"): data["body"] = self.text(m.get("data"), label)
            elif m.get("bodyType") in ("x_www_form_urlencoded", "multipart_form_data") and m.get("fields"): data["body"] = json.dumps(self.pairs(m.get("fields"), label))
            if m.get("qs"): self.warn(label, "Query parameters were not carried over. Add them to the URL.")
            if kind != "http:ActionSendData" or m.get("authUser"): self.warn(label, "Authentication is not transferred. Save a credential and choose it in the node's Authentication setting.")
            return "http", data
        if kind in ("util:SetVariable", "util:SetVariable2", "util:SetVariables"):
            pairs = {}
            if m.get("name"): pairs[str(m["name"])] = self.text(m.get("value"), label)
            for v in m.get("variables") or []:
                if isinstance(v, dict) and v.get("name"): pairs[str(v["name"])] = self.text(v.get("value"), label)
            return "set", {"json": json.dumps(pairs)}
        if kind == "util:FunctionSleep": return "wait", {"seconds": str(m.get("duration") or 1), "unit": "seconds"}
        if kind in ("slack:CreateMessage", "slack:PostMessage"):
            self.warn(label, "Choose a Slack bot token credential in the node. Make's saved connections are never exported.")
            return "app", {"service": "slack", "operation": "post_message", "credential": "", "fields": json.dumps({"channel": self.text(m.get("channel"), label), "text": self.text(m.get("text"), label)})}
        if kind == "email:ActionSendEmail":
            to = m.get("to")
            first = to[0].get("email") if isinstance(to, list) and to and isinstance(to[0], dict) else to
            if isinstance(to, list) and len(to) > 1: self.warn(label, "Only the first recipient was kept.")
            self.warn(label, "Save an SMTP credential and choose it in the node.")
            return "email", {"credential": "", "to": self.text(first, label), "subject": self.text(m.get("subject"), label), "body": self.text(m.get("html") or m.get("text") or "", label)}
        self.placeholders += 1
        self.warn(label, f"The Make module '{kind or '(unknown)'}' has no FlowPilot equivalent yet. It became a Pass Through node so the rest of the scenario keeps its shape.")
        return "note", {}


def convert(doc: Any) -> dict:
    if not isinstance(doc, dict) or not isinstance(doc.get("flow"), list):
        raise ValueError("This does not look like a Make blueprint. It needs a 'flow' list (in Make: scenario menu, Export Blueprint).")
    mk = Maker()
    count = 0

    def walk(flow: list, parent: str | None, parent_allowed: set[str], depth: int) -> None:
        nonlocal count
        prev, allowed = parent, parent_allowed
        for mod in flow:
            if not isinstance(mod, dict): continue
            count += 1
            if count > MAX_MODULES: raise ValueError(f"The scenario has more than {MAX_MODULES} modules. FlowPilot allows {MAX_MODULES}.")
            mid = str(mod.get("id") if mod.get("id") is not None else f"m{count}")
            nid = "m" + re.sub(r"[^A-Za-z0-9_-]", "_", mid)[:28]
            while any(n["id"] == nid for n in mk.nodes): nid += "x"
            kind = str(mod.get("module") or "")
            label = f"{kind.split(':')[-1] or 'Module'} {mid}"[:60]
            mk.allowed = allowed
            if mod.get("disabled") is True:
                typ, data = "note", {}
                mk.warn(label, "This module was disabled in Make, so it became a Pass Through node.")
            else: typ, data = mk.convert(mod, label)
            design = ((mod.get("metadata") or {}).get("designer")) or {}
            dx, dy = design.get("x"), design.get("y")
            x = float(dx) if isinstance(dx, (int, float)) else count * 240.0
            y = float(dy) if isinstance(dy, (int, float)) else 120.0
            mk.nodes.append({"id": nid, "type": typ, "position": {"x": x, "y": y}, "data": {"label": label, **data}})
            if prev: mk.edges.append({"id": f"{prev}-{nid}", "source": prev, "target": nid})
            if mod.get("filter"): mk.warn(label, "A filter on this module (a Make route condition) was not carried over. Add a Condition node before it.")
            if kind == "builtin:BasicRouter":
                if depth >= 3: raise ValueError("Routers are nested too deeply to import.")
                for route in mod.get("routes") or []:
                    if isinstance(route, dict) and isinstance(route.get("flow"), list): walk(route["flow"], nid, allowed | {mid}, depth + 1)
                mk.warn(label, "A router splits the scenario into routes. Each route starts after this node; conditions on routes must be rebuilt with Condition nodes.")
                return
            allowed = {mid}
            prev = nid

    walk(doc["flow"], None, set(), 0)
    if not mk.nodes: raise ValueError("The Make scenario has no modules.")
    if not any(n["type"] == "webhook" for n in mk.nodes):
        scheduling = ((doc.get("metadata") or {}).get("scheduling")) or {}
        interval = scheduling.get("interval") if isinstance(scheduling, dict) else None
        has_in = {e["target"] for e in mk.edges}
        roots = [n for n in mk.nodes if n["id"] not in has_in]
        x, y = min(n["position"]["x"] for n in mk.nodes) - 240, roots[0]["position"]["y"]
        if isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval >= 60 and scheduling.get("type") == "indefinitely":
            start = {"id": "start", "type": "schedule", "position": {"x": x, "y": y}, "data": {"label": "Schedule", "mode": "interval", "minutes": str(max(1, int(interval // 60)))}}
            mk.warn("Schedule", "Added from the scenario's schedule setting. If Make's first module watched for new items, FlowPilot has no equivalent trigger for it.")
        else:
            start = {"id": "start", "type": "trigger", "position": {"x": x, "y": y}, "data": {"label": "Manual Trigger"}}
            mk.warn("(scenario)", "No trigger FlowPilot understands was found, so a Manual Trigger was added at the start.")
        mk.nodes.insert(0, start)
        for r in roots: mk.edges.append({"id": f"start-{r['id']}", "source": "start", "target": r["id"]})
    return {"name": str(doc.get("name") or "Imported from Make")[:120], "description": "Imported from Make. Check the warnings before activating.", "nodes": mk.nodes, "edges": mk.edges,
            "warnings": mk.warnings, "summary": {"nodes": count, "placeholders": mk.placeholders, "warnings": len(mk.warnings)}}
