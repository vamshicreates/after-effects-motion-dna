#!/usr/bin/env python3
"""
Zero-Dependency Stdio MCP Server for Adobe After Effects (`scripts/after_effects_mcp_server.py`).

Implements the Model Context Protocol (JSON-RPC 2.0 over stdio) using 100% Python
standard library so it runs out of the box on Windows and macOS without pip or uv.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from after_effects_cli import (  # noqa: E402
    cmd_build_from_spec,
    cmd_inspect_aep,
    cmd_status,
    run_jsx_in_after_effects,
)

from laya_decision_gate import evaluate_decision as _laya_eval  # noqa: E402

TOOLS = [
    {'name': 'after_effects_laya_decide', 'description': 'Evaluate a creative brief or decision for Adobe After Effects using the embedded Laya model (https://github.com/NandhaKishorM/laya) with strict complexity gating. CALL ONLY WHEN NECESSARY for complex/ambiguous multi-branch tasks; for basic tasks, execute directly without calling Laya.', 'inputSchema': {'type': 'object', 'properties': {'state': {'type': 'string', 'description': 'The complex user brief or decision state to evaluate.'}, 'force_laya': {'type': 'boolean', 'description': 'Optional override to force Laya Router evaluation (default: false).'}}, 'required': ['state']}},
    {
        "name": "after_effects_status",
        "description": "Check Adobe After Effects installation, script security prefs, and live connection on macOS or Windows.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "after_effects_inspect_aep",
        "description": "Extract full Motion DNA (comp resolution, fps, layer tree, exact PostScript fonts, hex palette, Bezier keyframe easing influence %, expressions, and effects) from an .aep file or active composition.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Optional path to an .aep file. Omit to inspect the active composition in After Effects.",
                }
            },
        },
    },
    {
        "name": "after_effects_extract_motion_dna",
        "description": "Scan a folder of the motion designer's previous approved projects (.aep) and/or exported renders (.mp4, .mov) and cache the Motion DNA scan to .motion-dna/raw_motion_scan.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_dir": {"type": "string", "description": "Directory or file path of previous approved motion designs."},
                "state_dir": {"type": "string", "description": "Cache directory (default: .motion-dna)."},
            },
            "required": ["source_dir"],
        },
    },
    {
        "name": "after_effects_build_from_spec",
        "description": "Build a complete, fully keyframed After Effects composition LIVE on screen in the foreground from a declarative motion_spec.json file (background solid + gradient, ambient glow orbs, vector shape cards, kinetic typography with Bezier easing curves, markers, PNG preview frames, and saved .aep).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_json_path": {"type": "string", "description": "Path to motion_spec.json."}
            },
            "required": ["spec_json_path"],
        },
    },
    {
        "name": "after_effects_execute_jsx",
        "description": "Execute custom ExtendScript (.jsx) live inside Adobe After Effects with all ae_comp_builder.jsx helpers pre-loaded.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "ExtendScript (.jsx) code to execute inside After Effects."}
            },
            "required": ["code"],
        },
    },
]


def handle_tool_call(name: str, arguments: dict) -> dict:
    try:
        if name == "after_effects_laya_decide":
            res = _laya_eval(
                state_text=arguments.get("state", ""),
                force_laya=bool(arguments.get("force_laya", False)),
            )
            return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}
        if name == "after_effects_status":
            res = cmd_status()
        elif name == "after_effects_inspect_aep":
            res = cmd_inspect_aep(arguments.get("file_path"))
        elif name == "after_effects_extract_motion_dna":
            src = arguments.get("source_dir", ".")
            state = arguments.get("state_dir", ".motion-dna")
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "extract_motion_dna.py"), src, "--state-dir", state],
                capture_output=True,
                text=True,
                timeout=180,
            )
            try:
                res = json.loads(proc.stdout)
            except Exception:
                res = {"status": "completed", "stdout": proc.stdout, "stderr": proc.stderr}
        elif name == "after_effects_build_from_spec":
            res = cmd_build_from_spec(arguments["spec_json_path"])
        elif name == "after_effects_execute_jsx":
            code = arguments["code"]
            if "return " not in code:
                code += '\nreturn toJson({ status: "success" });'
            res = run_jsx_in_after_effects(code, include_builder=True, foreground=True)
        else:
            return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}

        return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}], "isError": res.get("status") == "error"}
    except Exception as exc:
        return {"content": [{"type": "text", "text": json.dumps({"status": "error", "message": str(exc)})}], "isError": True}


def main():
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "after-effects-motion-dna", "version": "1.1.0"},
                },
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}}) + "\n")
            sys.stdout.flush()
        elif method == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            params = msg.get("params", {})
            tool_res = handle_tool_call(params.get("name", ""), params.get("arguments", {}) or {})
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": tool_res}) + "\n")
            sys.stdout.flush()
        elif msg_id is not None:
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}) + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
