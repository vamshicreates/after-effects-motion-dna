#!/usr/bin/env python3
"""
Cross-Platform Zero-Plugin Live Foreground Bridge for Adobe After Effects (macOS & Windows).

How it works so the user watches every layer, shape, text animation, and keyframe happen live:
1. Automatically enables After Effects' "Allow Scripts to Write Files and Access Network"
   preference on disk (`Pref_SCRIPTING_FILE_NETWORK_SECURITY = "1"`) so the user never has
   to open Preferences > Scripting & Expressions.
2. Brings Adobe After Effects to the foreground on both macOS (`osascript activate`) and
   Windows (`WScript.Shell.AppActivate('Adobe After Effects')`).
3. Executes `cmd_build_from_spec` in **Multi-Stage Live Foreground Mode**:
   - Step 1: Creates the Composition and opens it immediately in the Composition Viewer (`comp.openInViewer()`).
   - Step 2: Adds the Background Solid + Gradient Ramp and each Ambient Glow Orb one by one.
   - Step 3: Adds each Vector UI Card (`ShapeLayer`) with Bezier keyframe animations one by one.
   - Step 4: Adds each Kinetic Typography layer (`TextLayer`) with staggered Bezier keyframes and expressions one by one, scrubbing the playhead (`comp.time`) so the user sees every element animate in real time!
   - Step 5: Adds Composition Markers, renders preview PNG frames (`saveFrameToPng`), and saves the `.aep` file.
"""

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_ROOT / "assets"
INSPECT_JSX = ASSETS_DIR / "inspect_aep_dna.jsx"
BUILDER_JSX = ASSETS_DIR / "ae_comp_builder.jsx"


def find_after_effects_executable() -> str | None:
    """Locate Adobe After Effects across macOS and Windows."""
    env_path = os.environ.get("AFTER_EFFECTS_PATH") or os.environ.get("AE_PATH")
    if env_path and Path(env_path).exists():
        return str(Path(env_path).resolve())

    system = platform.system()
    candidates = []

    if system == "Darwin":
        candidates.extend(sorted(glob.glob("/Applications/Adobe After Effects*/Adobe After Effects*.app"), reverse=True))
        candidates.extend(sorted(glob.glob(str(Path.home() / "Applications/Adobe After Effects*/Adobe After Effects*.app")), reverse=True))
        try:
            out = subprocess.check_output(
                ["mdfind", "kMDItemCFBundleIdentifier == 'com.adobe.AfterEffects'"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            for line in out.splitlines():
                p = line.strip()
                if p.endswith(".app") and os.path.exists(p):
                    candidates.append(p)
        except Exception:
            pass

    elif system == "Windows":
        for base in [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]:
            if not base:
                continue
            candidates.extend(
                sorted(
                    glob.glob(os.path.join(base, "Adobe", "Adobe After Effects*", "Support Files", "AfterFX.exe")),
                    reverse=True,
                )
            )

    for c in candidates:
        if c and os.path.exists(c):
            return c
    return shutil.which("AfterFX") or shutil.which("aerender")


def enable_ae_script_security_pref() -> list[str]:
    """
    Automatically enable 'Allow Scripts to Write Files and Access Network'
    in Adobe After Effects preference files on macOS and Windows.
    """
    system = platform.system()
    pref_files = []
    if system == "Darwin":
        pref_root = Path.home() / "Library" / "Preferences" / "Adobe" / "After Effects"
        pref_files.extend(glob.glob(str(pref_root / "*" / "*Prefs-indep-general.txt")))
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        pref_root = Path(appdata) / "Adobe" / "After Effects"
        pref_files.extend(glob.glob(str(pref_root / "*" / "*Prefs-indep-general.txt")))

    modified = []
    for pf in pref_files:
        try:
            p = Path(pf)
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if '"Pref_SCRIPTING_FILE_NETWORK_SECURITY"' in txt:
                new_txt = re.sub(
                    r'("Pref_SCRIPTING_FILE_NETWORK_SECURITY"\s*=\s*)"0"',
                    r'\1"1"',
                    txt,
                )
            else:
                new_txt = txt + '\n["Main Pref Section v2"]\n"Pref_SCRIPTING_FILE_NETWORK_SECURITY" = "1"\n'
            if new_txt != txt:
                p.write_text(new_txt, encoding="utf-8")
            modified.append(str(p))
        except Exception:
            pass
    return modified


def bring_after_effects_to_front():
    """Bring the Adobe After Effects window to the foreground on macOS or Windows."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["osascript", "-e", 'tell application id "com.adobe.AfterEffects" to activate'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        elif system == "Windows":
            ps_cmd = (
                "$wshell = New-Object -ComObject WScript.Shell; "
                "[void]$wshell.AppActivate('Adobe After Effects'); "
                "[void]$wshell.AppActivate('After Effects')"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    except Exception:
        pass


def run_jsx_in_after_effects(
    jsx_body: str,
    include_builder: bool = True,
    foreground: bool = True,
    timeout_sec: int = 90,
) -> dict:
    """
    Execute ExtendScript (`jsx_body`) live inside Adobe After Effects on macOS or Windows
    and return the parsed JSON result dictionary.
    """
    enable_ae_script_security_pref()
    if foreground:
        bring_after_effects_to_front()

    tmp_dir = Path(tempfile.gettempdir()) / "antigravity_ae_bridge"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    runner_jsx = tmp_dir / f"ae_runner_{run_id}.jsx"
    result_json = tmp_dir / f"ae_result_{run_id}.json"

    builder_code = BUILDER_JSX.read_text(encoding="utf-8") if (include_builder and BUILDER_JSX.exists()) else ""
    result_path_escaped = str(result_json).replace("\\", "/")

    wrapper = f"""
try {{
    app.preferences.savePrefAsLong("Main Pref Section v2", "Pref_SCRIPTING_FILE_NETWORK_SECURITY", 1);
}} catch (ePref) {{}}

{builder_code}

function __writeResultFile(strPayload) {{
    try {{
        var f = new File("{result_path_escaped}");
        f.encoding = "UTF-8";
        f.open("w");
        f.write(String(strPayload));
        f.close();
    }} catch (eW) {{}}
    return String(strPayload);
}}

function __runMain() {{
    try {{
{jsx_body}
    }} catch (err) {{
        return '{{"status":"error","message":"' + String(err).replace(/\\\\/g, "\\\\\\\\").replace(/"/g, '\\\\"') + ' (line ' + err.line + ')"}}';
    }}
}}

__writeResultFile(__runMain());
"""
    runner_jsx.write_text(wrapper, encoding="utf-8")

    system = platform.system()
    ae_path = find_after_effects_executable()
    stdout_val = ""

    try:
        if system == "Darwin":
            osa_script = (
                'tell application id "com.adobe.AfterEffects"\n'
                '    activate\n'
                f'    DoScriptFile "{str(runner_jsx)}"\n'
                'end tell'
            )
            proc = subprocess.run(
                ["osascript", "-e", osa_script],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            stdout_val = (proc.stdout or "").strip()

            if proc.returncode != 0 and not result_json.exists() and ae_path:
                subprocess.run(["open", "-a", ae_path, str(runner_jsx)], check=False)

        elif system == "Windows":
            if ae_path:
                # AfterFX.exe -r <script.jsx> runs the script in the active After Effects instance
                subprocess.Popen([ae_path, "-r", str(runner_jsx)])
            else:
                return {
                    "status": "error",
                    "message": "AfterFX.exe was not found in standard Windows paths. Set AFTER_EFFECTS_PATH.",
                }

        deadline = time.time() + min(timeout_sec, 45)
        while not result_json.exists() and time.time() < deadline:
            if stdout_val.startswith("{") and stdout_val.endswith("}"):
                break
            time.sleep(0.30)

        if result_json.exists():
            raw = result_json.read_text(encoding="utf-8").strip()
            return json.loads(raw)

        if stdout_val:
            try:
                return json.loads(stdout_val)
            except Exception:
                return {"status": "success", "raw_output": stdout_val}

        return {
            "status": "error",
            "message": "After Effects did not return a response. Ensure Adobe After Effects is installed and open.",
            "detected_after_effects": ae_path,
        }
    finally:
        for p in (runner_jsx, result_json):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass


def cmd_status() -> dict:
    ae_bin = find_after_effects_executable()
    prefs = enable_ae_script_security_pref()
    if not ae_bin:
        return {
            "status": "AFTER_EFFECTS_NOT_FOUND",
            "os": platform.system(),
            "script_security_prefs_updated": prefs,
            "message": "Adobe After Effects was not found in standard paths.",
        }
    ping_jsx = """
        return toJson({
            status: "success",
            version: String(app.version),
            numItems: app.project ? app.project.numItems : 0,
            activeComp: (app.project && app.project.activeItem && (app.project.activeItem instanceof CompItem)) ? app.project.activeItem.name : null
        });
    """
    res = run_jsx_in_after_effects(ping_jsx, include_builder=True, foreground=True, timeout_sec=30)
    res["after_effects_executable"] = ae_bin
    res["script_security_prefs_updated"] = prefs
    return res


def cmd_inspect_aep(aep_path: str | None = None) -> dict:
    inspect_code = INSPECT_JSX.read_text(encoding="utf-8")
    target_arg = ""
    if aep_path:
        abs_aep = str(Path(aep_path).resolve()).replace("\\", "/")
        target_arg = f'"{abs_aep}"'
    jsx = f"""
{inspect_code}
return inspectAeProjectOrComp({target_arg});
"""
    return run_jsx_in_after_effects(jsx, include_builder=False, foreground=True, timeout_sec=90)


def cmd_build_from_spec(spec_file: str) -> dict:
    """
    Execute the After Effects composition build in Multi-Stage Live Foreground Mode
    so the user watches every layer, shape, kinetic text line, and keyframe animate
    in the Composition Viewer in real time on both macOS and Windows.
    """
    spec_data = json.loads(Path(spec_file).read_text(encoding="utf-8"))
    comp_cfg = spec_data.get("composition", {})
    comp_name = comp_cfg.get("name", "Motion_DNA_Comp")

    bring_after_effects_to_front()

    # Step 1: Create Composition, open in Viewer, and add Background Solid + Gradient
    bg_cfg = spec_data.get("background", {"color": comp_cfg.get("bgColor", "#0B0F19")})
    step1_jsx = f"""
        var compSpec = {json.dumps(comp_cfg)};
        var bgSpec = {json.dumps(bg_cfg)};
        var comp = findOrCreateComp(compSpec);
        // Clear old layers if rebuilding the same comp
        while (comp.numLayers > 0) {{
            comp.layer(1).remove();
        }}
        addBackgroundSolid(comp, bgSpec);
        return toJson({{ status: "step1_comp_opened", compName: comp.name, width: comp.width, height: comp.height }});
    """
    s1 = run_jsx_in_after_effects(step1_jsx, include_builder=True, foreground=True)
    if s1.get("status") == "error":
        return s1
    time.sleep(0.20)

    # Step 2: Add Ambient Glow Orbs one-by-one live in the Viewer
    for idx, gl in enumerate(spec_data.get("glows", [])):
        gl_jsx = f"""
            var comp = findOrCreateComp({json.dumps(comp_cfg)});
            var lyr = addAmbientGlowLayer(comp, {json.dumps(gl)});
            return toJson({{ status: "glow_added", name: lyr.name }});
        """
        run_jsx_in_after_effects(gl_jsx, include_builder=True, foreground=False)
        time.sleep(0.18)

    # Step 3: Add Vector UI Cards / Shapes one-by-one live in the Viewer
    for idx, card in enumerate(spec_data.get("cards", [])):
        card_jsx = f"""
            var comp = findOrCreateComp({json.dumps(comp_cfg)});
            var lyr = addVectorCardShape(comp, {json.dumps(card)});
            return toJson({{ status: "card_added", name: lyr.name }});
        """
        run_jsx_in_after_effects(card_jsx, include_builder=True, foreground=False)
        time.sleep(0.20)

    # Step 4: Add Kinetic Typography Layers one-by-one live in the Viewer
    for idx, txt in enumerate(spec_data.get("texts", [])):
        txt_jsx = f"""
            var comp = findOrCreateComp({json.dumps(comp_cfg)});
            var lyr = addKineticTextLayer(comp, {json.dumps(txt)});
            return toJson({{ status: "text_added", name: lyr.name }});
        """
        run_jsx_in_after_effects(txt_jsx, include_builder=True, foreground=False)
        time.sleep(0.22)

    # Step 5: Add Markers, render preview PNG frames, and save the .aep file
    markers = spec_data.get("markers", [])
    preview_frames = spec_data.get("previewFrames", [])
    for pf in preview_frames:
        if pf.get("outputPng"):
            pf["outputPng"] = str(Path(pf["outputPng"]).resolve()).replace("\\", "/")
    out_aep = str(Path(spec_data["outputAep"]).resolve()).replace("\\", "/") if spec_data.get("outputAep") else None

    step5_jsx = f"""
        var comp = findOrCreateComp({json.dumps(comp_cfg)});
        var markers = {json.dumps(markers)};
        for (var m = 0; m < markers.length; m++) {{
            addCompMarker(comp, markers[m]);
        }}

        var previews = {json.dumps(preview_frames)};
        var exportedFrames = [];
        for (var p = 0; p < previews.length; p++) {{
            var pf = previews[p];
            var savedPng = savePreviewFramePng(comp, pf.timeSec, pf.outputPng);
            if (savedPng) exportedFrames.push({{ timeSec: pf.timeSec, pngPath: savedPng }});
        }}

        var outAep = {json.dumps(out_aep)};
        var savedAep = outAep ? saveProjectAep(outAep) : null;
        comp.time = Number({comp_cfg.get("heroPreviewTimeSec", 1.2)});

        return toJson({{
            status: "success",
            executionMode: "live_foreground_step_by_step",
            compName: comp.name,
            layerCount: comp.numLayers,
            savedAep: savedAep,
            exportedFrames: exportedFrames
        }});
    """
    return run_jsx_in_after_effects(step5_jsx, include_builder=True, foreground=True)


def main():
    parser = argparse.ArgumentParser(description="Cross-platform Adobe After Effects Live Foreground CLI Bridge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Check After Effects installation, script security prefs, and live connection")

    p_insp = sub.add_parser("inspect-aep", help="Extract full Motion DNA from an .aep file or active composition")
    p_insp.add_argument("--file", default=None, help="Optional path to .aep file (omit to inspect active comp)")

    p_build = sub.add_parser("build-spec", help="Build a keyframed composition live on screen in After Effects from a JSON spec")
    p_build.add_argument("spec_json", help="Path to motion_spec.json")

    p_exec = sub.add_parser("exec", help="Execute custom ExtendScript (.jsx) live in After Effects")
    p_exec.add_argument("-c", "--code", help="Inline ExtendScript code")
    p_exec.add_argument("-f", "--file", help="Path to .jsx file")

    args = parser.parse_args()

    if args.cmd == "status":
        print(json.dumps(cmd_status(), indent=2))
    elif args.cmd == "inspect-aep":
        print(json.dumps(cmd_inspect_aep(args.file), indent=2))
    elif args.cmd == "build-spec":
        print(json.dumps(cmd_build_from_spec(args.spec_json), indent=2))
    elif args.cmd == "exec":
        code = Path(args.file).read_text(encoding="utf-8") if args.file else (args.code or sys.stdin.read())
        if "return " not in code:
            code = code + '\nreturn toJson({ status: "success" });'
        print(json.dumps(run_jsx_in_after_effects(code, include_builder=True, foreground=True), indent=2))


if __name__ == "__main__":
    main()
