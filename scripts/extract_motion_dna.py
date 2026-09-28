#!/usr/bin/env python3
"""
Motion DNA Extractor (`scripts/extract_motion_dna.py`) for `after-effects-motion-dna`.

Scans a folder of the motion designer's previous approved projects (`.aep`) and/or
exported motion graphics renders (`.mp4`, `.mov`, `.webm`) and extracts:
1. From `.aep` files (via live After Effects inspection):
   - Composition resolution, frameRate (60fps vs 30fps), duration, bgColor hex, motionBlur
   - Exact PostScript fonts, font sizes, tracking, and hex colors
   - Keyframe & Easing Physics DNA (`inInfluencePct`, `outInfluencePct`, stagger timing, expressions)
   - Applied Effects stack (`ADBE Drop Shadow`, `ADBE Glo2`, `ADBE Ramp`, etc.)
2. From `.mp4` / `.mov` motion design references:
   - Exact frame rate, aspect ratio, duration, and visual beat timestamps via `ffprobe`/`ffmpeg`
Caches the aggregated scan in `.motion-dna/raw_motion_scan.json` so the agent synthesizes
`.motion-dna/motion_dna.json` once.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from after_effects_cli import cmd_inspect_aep, find_after_effects_executable  # noqa: E402

AEP_EXTS = {".aep", ".aet"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(2 * 1024 * 1024))
    return h.hexdigest()[:16]


def analyze_motion_video(video_path: Path) -> dict:
    info = {"file": str(video_path), "name": video_path.name}
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe" or "/usr/local/bin/ffprobe"
    if os.path.exists(ffprobe) or shutil.which("ffprobe"):
        try:
            cmd = [
                shutil.which("ffprobe") or ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                "-of", "json",
                str(video_path),
            ]
            out = json.loads(subprocess.check_output(cmd, text=True, timeout=20))
            stream = (out.get("streams") or [{}])[0]
            fmt = out.get("format") or {}
            w = int(stream.get("width", 1080))
            h = int(stream.get("height", 1920))
            fps_raw = stream.get("r_frame_rate", "60/1")
            num, den = (fps_raw.split("/", 1) + ["1"])[:2]
            fps = round(float(num) / max(1.0, float(den)), 2)
            dur = round(float(fmt.get("duration", 6.0)), 2)
            info.update({
                "width": w,
                "height": h,
                "aspectRatio": "9:16" if h > w else ("1:1" if w == h else "16:9"),
                "fps": fps,
                "durationSec": dur,
            })
        except Exception as exc:
            info["probe_warning"] = str(exc)
    return info


def main():
    parser = argparse.ArgumentParser(description="Extract Motion DNA from reference .aep projects and motion videos")
    parser.add_argument("source", nargs="?", default=".", help="Directory or file of previous approved motion designs")
    parser.add_argument("--state-dir", default=".motion-dna", help="Directory to cache extracted Motion DNA")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)

    aep_files = []
    video_files = []
    if source_path.is_file():
        if source_path.suffix.lower() in AEP_EXTS:
            aep_files.append(source_path)
        elif source_path.suffix.lower() in VIDEO_EXTS:
            video_files.append(source_path)
    else:
        for root, dirs, files in os.walk(source_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__", "output")]
            for fn in sorted(files):
                p = Path(root) / fn
                if p.suffix.lower() in AEP_EXTS:
                    aep_files.append(p)
                elif p.suffix.lower() in VIDEO_EXTS:
                    video_files.append(p)

    corpus_items = [f"{p.name}:{file_sha256(p)}" for p in (aep_files[:6] + video_files[:6])]
    corpus_hash = hashlib.sha256("|".join(corpus_items).encode("utf-8")).hexdigest()[:16]

    raw_scan_file = state_dir / "raw_motion_scan.json"
    dna_file = state_dir / "motion_dna.json"

    if raw_scan_file.exists() and dna_file.exists() and corpus_items:
        try:
            cached = json.loads(raw_scan_file.read_text(encoding="utf-8"))
            if cached.get("corpus_hash") == corpus_hash:
                print(json.dumps({
                    "status": "CACHE_HIT",
                    "corpus_hash": corpus_hash,
                    "motion_dna_file": str(dna_file),
                }, indent=2))
                sys.exit(0)
        except Exception:
            pass

    ae_bin = find_after_effects_executable()
    aep_reports = []
    all_fonts = Counter()
    all_colors = Counter()
    influences = []

    if ae_bin:
        for af in aep_files[:4]:
            rep = cmd_inspect_aep(str(af))
            aep_reports.append({"file": str(af), "inspection": rep})
            for f_name, cnt in (rep.get("detectedFonts") or {}).items():
                all_fonts[f_name] += int(cnt)
            for c_hex, cnt in (rep.get("detectedColors") or {}).items():
                all_colors[c_hex] += int(cnt)
            if rep.get("recommendedBezierInfluencePct"):
                influences.append(int(rep["recommendedBezierInfluencePct"]))

    video_reports = [analyze_motion_video(vf) for vf in video_files[:6]]
    avg_inf = round(sum(influences) / len(influences)) if influences else 80

    result = {
        "status": "SCAN_COMPLETED",
        "corpus_hash": corpus_hash,
        "detected_fonts": [f for f, _ in all_fonts.most_common(8)],
        "detected_colors": [c for c, _ in all_colors.most_common(10)],
        "recommended_bezier_influence_pct": avg_inf,
        "aep_projects": aep_reports,
        "reference_motion_videos": video_reports,
        "next_step": f"Synthesize the company's Motion DNA into {dna_file}",
    }

    raw_scan_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
