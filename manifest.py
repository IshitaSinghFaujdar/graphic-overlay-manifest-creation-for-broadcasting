import os
import json
import subprocess
import logging
from datetime import datetime, timedelta
from rich.logging import RichHandler
from rich.console import Console

console = Console()
log_dir = "logs"
output_dir = "output"
os.makedirs(log_dir, exist_ok=True)
os.makedirs(output_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        RichHandler(console=console, show_time=False, show_level=True),
        logging.FileHandler(log_file)
    ]
)
log = logging.getLogger("manifest_logger")

def section_header(title):
    console.rule(f"[bold cyan]{title}")
    log.info(f"====== {title.upper()} ======")

def safe_output_path(*paths):
    path = os.path.join(*paths)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

def write_manifest(base_folder, base_name, segments, target_duration):
    manifest_path = safe_output_path(base_folder, f"{base_name}.m3u8")
    with open(manifest_path, "w") as f:
        f.write("#EXTM3U\n#EXT-X-VERSION:3\n")
        f.write(f"#EXT-X-TARGETDURATION:{target_duration}\n#EXT-X-MEDIA-SEQUENCE:0\n")
        for seg in segments:
            f.write(f"#EXTINF:{seg['duration']:.3f},\n{seg['filename']}\n")
        f.write("#EXT-X-ENDLIST\n")
    return manifest_path

def ffmpeg_segment(input_file, output_pattern, segment_time):
    cmd = [
        "ffmpeg", "-y", "-i", input_file,
        "-c", "copy", "-map", "0", "-f", "segment",
        "-segment_time", str(segment_time),
        "-reset_timestamps", "1",
        output_pattern
    ]
    log.info(f"Running FFmpeg: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def get_ts_durations(folder, prefix=""):
    segments = []
    for fname in sorted(os.listdir(folder)):
        if fname.endswith(".ts"):
            path = os.path.join(folder, fname)
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                duration = float(result.stdout.strip())
                segments.append({"filename": f"{prefix}{fname}", "duration": duration})
            except subprocess.CalledProcessError:
                log.error(f"Failed ffprobe on {path}")
    return segments

def generate_hls_for_video(label, input_path, segment_time=6):
    folder = os.path.join(output_dir, label)
    segment_output = os.path.join(folder, "seg%03d.ts")

    section_header(f"Segmenting {label}")
    ffmpeg_segment(input_path, segment_output, segment_time)
    
    section_header(f"Getting segment durations for {label}")
    segments = get_ts_durations(folder)
    target_duration = max(int(seg['duration']) + 1 for seg in segments)
    
    manifest_path = write_manifest(folder, label, segments, target_duration)
    return label, os.path.relpath(manifest_path, output_dir)

def write_master_manifest(master_name, entries):
    master_path = safe_output_path(output_dir, f"{master_name}.m3u8")
    with open(master_path, "w") as f:
        f.write("#EXTM3U\n")
        for label, path in entries:
            f.write(f"#EXT-X-STREAM-INF:BANDWIDTH=500000\n{path}\n")
    return master_path

def main():
    with open("/home/ishitasinghfaujdar/pmsltask/manifest.json") as f:
        config = json.load(f)

    master_entries = []

    # Process main videos
    for video in config['videos']:
        video_label = video['id']
        video_input = video['file_path']
        video_entry = generate_hls_for_video(video_label, video_input)
        master_entries.append(video_entry)


    # Process ads
    for ad in config['ads']:
        ad_label = ad['id']
        ad_path = ad['file_path']
        ad_entry = generate_hls_for_video(ad_label, ad_path)
        master_entries.append(ad_entry)

    section_header("Writing master manifest")
    master_name = config.get('master_manifest_name', f"master_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    master_path = write_master_manifest(master_name, master_entries)

    log.info(f"Master manifest written to {master_path}")

if __name__ == "__main__":
    main()
