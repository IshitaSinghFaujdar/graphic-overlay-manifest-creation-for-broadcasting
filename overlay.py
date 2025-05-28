import os
import json
import subprocess
from datetime import datetime
import logging
from rich.logging import RichHandler
from rich.console import Console
import time
# Setup Logging
console = Console()
os.makedirs("logs", exist_ok=True)
log_file = os.path.join("logs", f"overlay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        RichHandler(console=console, show_time=False, show_level=True),
        logging.FileHandler(log_file)
    ]
)
log = logging.getLogger("overlay_logger")

def section_header(title: str):
    console.rule(f"[bold cyan]{title}")
    log.info(f"====== {title.upper()} ======")

def build_filter_and_inputs(video_conf):
    base_input = ["-i", video_conf["input"]]
    filter_parts = []
    input_cmds = []
    overlay_idx = 1

    for overlay in video_conf["overlays"]:
        input_cmds += ["-stream_loop", "-1", "-i", overlay["graphic"]]
        mode = overlay["mode"]
        position = overlay["position"]

        if mode == "always":
            enable = ""
        elif mode == "periodic":
            interval = overlay["interval"]
            duration = overlay["duration"]
            enable = f":enable='lt(mod(t\\,{interval})\\,{duration})'"
        elif mode == "flash":
            start = overlay["start"]
            duration = overlay["duration"]
            enable = f":enable='between(t,{start},{start + duration})'"
        else:
            log.warning(f"Unknown mode {mode}, skipping overlay.")
            continue

        filter_parts.append(
            f"[{overlay_idx}:v]format=rgba[ol{overlay_idx}];"
            f"[v{overlay_idx - 1}][ol{overlay_idx}]overlay={position}{enable}[v{overlay_idx}]"
        )
        overlay_idx += 1

    filter_complex = ";".join(filter_parts)
    return base_input + input_cmds, filter_complex, f"[v{overlay_idx - 1}]"

def apply_overlays(video_conf):
    section_header(f"Processing {video_conf['input']}")
    inputs, filter_complex, last_output = build_filter_and_inputs(video_conf)
    output_path = video_conf["output"]
    command = [
        "ffmpeg",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", last_output,
        "-map", "0:a?",
        "-c:a", "copy",
        "-shortest",
        output_path,
        "-y"
    ]
    
    log.info(f"Running FFmpeg for {video_conf['input']}")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )
    last_log_time = 0
    log_interval = 60 
    full_output = []
    try:
        for line in process.stdout:
            line = line.strip()
            full_output.append(line)

            if "frame=" in line:
                now=time.time()
                if now - last_log_time >= log_interval:
                    log.info(f"[ffmpeg] {line}")
                    last_log_time = now

        process.wait()
        if process.returncode != 0:
            logging.error("❌ FFmpeg failed with return code %s", process.returncode)
            for line in full_output:
                logging.error(line)
        else:
            logging.info("✅ FFmpeg finished successfully.")

    except Exception as e:
        logging.exception("Exception occurred during FFmpeg execution.")

def main():
    section_header("OVERLAY PIPELINE STARTED")
    with open("input/config.json", "r") as f:
        config = json.load(f)

    for video_conf in config["videos"]:
        apply_overlays(video_conf)

    section_header("PIPELINE COMPLETE")

if __name__ == "__main__":
    main()
