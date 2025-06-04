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
    segments = get_ts_durations(folder, prefix=f"{label}/")
    total_duration = sum(seg["duration"] for seg in segments)
    target_duration = max(int(seg['duration']) + 1 for seg in segments)
    
    write_manifest(folder, label, segments, target_duration)
    return label, segments, total_duration


def write_master_manifest(master_name, entries):
    master_path = safe_output_path(output_dir, f"{master_name}.m3u8")
    with open(master_path, "w") as f:
        f.write("#EXTM3U\n")
        for label, path in entries:
            f.write(f"#EXT-X-STREAM-INF:BANDWIDTH=500000\n{path}\n")
    return master_path
def write_timeline_manifest(output_name, stitched_segments):
    manifest_path = safe_output_path(output_dir, f"{output_name}.m3u8")
    with open(manifest_path, "w") as f:
        f.write("#EXTM3U\n#EXT-X-VERSION:3\n")
        f.write(f"#EXT-X-TARGETDURATION:10\n")  # conservative default
        f.write("#EXT-X-MEDIA-SEQUENCE:0\n")
        for i, part in enumerate(stitched_segments):
            if i != 0:
                f.write("#EXT-X-DISCONTINUITY\n")
            for seg in part:
                f.write(f"#EXTINF:{seg['duration']:.3f},\n{seg['filename']}\n")
        f.write("#EXT-X-ENDLIST\n")
    return manifest_path


def main():
    with open("/home/ishitasinghfaujdar/pmsltask/manifest.json") as f:
        config = json.load(f)

    stitched_segments = []

    ad_segments_cache = []
    for ad in config['ads']:
        ad_label = ad['id']
        ad_path = ad['file_path']
        _, ad_segs, ad_dur = generate_hls_for_video(ad_label, ad_path)
        ad_segments_cache.append((ad_segs, ad_dur))

    ad_index = 0
    for video in config['videos']:
        show_label = video['id']
        show_input = video['file_path']
        _, show_segs, show_dur = generate_hls_for_video(show_label, show_input)

        # Split logic: insert ad at 7 min if 13+ min remain
        split_time = 420  # 7 minutes
        show_before_ad, show_after_ad = [], []
        acc_time = 0
        split_index = 0

        for i, seg in enumerate(show_segs):
            if acc_time < split_time:
                show_before_ad.append(seg)
                acc_time += seg["duration"]
                split_index = i
            else:
                break
        show_after_ad = show_segs[split_index + 1:]

        stitched_segments.append(show_before_ad)

        if show_dur - acc_time > 780:  # >13 min remaining
            ad_segs, _ = ad_segments_cache[ad_index % len(ad_segments_cache)]
            stitched_segments.append(ad_segs)
            stitched_segments.append(show_after_ad)
        else:
            stitched_segments.append(show_after_ad)
            ad_segs, _ = ad_segments_cache[ad_index % len(ad_segments_cache)]
            stitched_segments.append(ad_segs)

        ad_index += 1

    section_header("Writing stitched master manifest")
    manifest_path = write_timeline_manifest("stitched_master", stitched_segments)
    log.info(f"Timeline manifest written to {manifest_path}")

if __name__ == "__main__":
    main()
