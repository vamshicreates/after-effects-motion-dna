#!/usr/bin/env python3
"""
Zero-Touch Cross-Platform Setup Script for `after-effects-motion-dna` (macOS & Windows).

1. Locates the installed Adobe After Effects executable on macOS or Windows.
2. Automatically enables `"Allow Scripts to Write Files and Access Network"`
   (`Pref_SCRIPTING_FILE_NETWORK_SECURITY = "1"`) in After Effects' user preferences files.
3. Registers the zero-dependency `"after-effects"` MCP server in `~/.gemini/config/mcp_config.json`.
"""

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
MCP_SERVER_SCRIPT = SCRIPTS_DIR / "after_effects_mcp_server.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from after_effects_cli import (  # noqa: E402
    cmd_status,
    enable_ae_script_security_pref,
    find_after_effects_executable,
)


def update_mcp_config() -> str:
    config_dir = Path.home() / ".gemini" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "mcp_config.json"

    data = {"mcpServers": {}}
    if config_file.exists():
        try:
            shutil.copy2(config_file, config_dir / "mcp_config.json.backup")
            data = json.loads(config_file.read_text(encoding="utf-8"))
            if not isinstance(data.get("mcpServers"), dict):
                data["mcpServers"] = {}
        except Exception:
            data = {"mcpServers": {}}

    python_bin = sys.executable or ("python" if platform.system() == "Windows" else "python3")
    data["mcpServers"]["after-effects"] = {
        "command": python_bin,
        "args": [str(MCP_SERVER_SCRIPT.resolve())],
    }

    config_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(config_file)


def main():
    parser = argparse.ArgumentParser(description="Zero-touch setup for After Effects Motion DNA skill & MCP")
    parser.add_argument("--ping", action="store_true", help="Launch/ping Adobe After Effects immediately to verify connection")
    args = parser.parse_args()

    ae_bin = find_after_effects_executable()
    prefs_updated = enable_ae_script_security_pref()
    mcp_config_path = update_mcp_config()

    summary = {
        "os": platform.system(),
        "after_effects_executable": ae_bin,
        "script_security_prefs_updated": prefs_updated,
        "mcp_server_script": str(MCP_SERVER_SCRIPT.resolve()),
        "mcp_config_updated": mcp_config_path,
        "status": "READY" if ae_bin else "MCP_REGISTERED_INSTALL_AFTER_EFFECTS",
    }

    if args.ping and ae_bin:
        summary["live_ping"] = cmd_status()

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
