import os
import subprocess
import logging
from datetime import datetime, timedelta
from rich.logging import RichHandler
from rich.console import Console
import json
console = Console()
os.makedirs("logs", exist_ok=True)
log_file = os.path.join("logs", f"manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        RichHandler(console=console, show_time=False, show_level=True),
        logging.FileHandler(log_file)
    ]
)
log = logging.getLogger("manifest_logger")


def load_config(path):
    with open(path, "r") as f:
        config = json.load(f)

    required_keys = ["videos", "ads", "segment_duration_sec", "cue_interval_sec", "min_last_segment_sec"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required config key: '{key}'")

    return config


def section_header(title: str):
    console.rule(f"[bold cyan]{title}")
    log.info(f"====== {title.upper()} ======")

def seconds_to_hhmmss(seconds):
    return str(timedelta(seconds=seconds))

def ffmpeg_segment(input_file, output_pattern, segment_time=420):
    """
    Run FFmpeg to segment the input video into .ts files.
    input_file: path to input video (e.g., 'videos/show1_video.mp4')
    output_pattern: output segment pattern (e.g., 'output/show1_video_seg%03d.ts')
    """
    cmd = [
        "ffmpeg",
        "-y",  # overwrite output files if exist
        "-i", input_file,
        "-c", "copy",
        "-map", "0",
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-reset_timestamps", "1",
        output_pattern
    ]
    log.info(f"Running FFmpeg segmentation: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def write_manifest(output_dir, base_name, segments, target_duration=10):
    """Write a simple HLS manifest for given segments."""
    manifest_path = os.path.join(output_dir, f"{base_name}.m3u8")
    log.info(f"Writing manifest: {manifest_path} with {len(segments)} segments")
    
    with open(manifest_path, "w") as f:
        f.write("#EXTM3U\n")
        f.write("#EXT-X-VERSION:3\n")
        f.write(f"#EXT-X-TARGETDURATION:{target_duration}\n")
        f.write(f"#EXT-X-MEDIA-SEQUENCE:0\n")
        
        for seg in segments:
            f.write(f"#EXTINF:{seg['duration']:.3f},\n")
            f.write(f"{seg['filename']}\n")
        
        f.write("#EXT-X-ENDLIST\n")
    return manifest_path

def segment_video(filename, duration, segment_length=420):
    """
    Create segment info for a video given duration in seconds.
    Segments are chunked at `segment_length` seconds.
    Returns list of dicts: {filename, duration}
    """
    segments = []
    seg_num = 0
    pos = 0
    while pos < duration:
        seg_num += 1
        remaining = duration - pos
        seg_dur = min(segment_length, remaining)
        seg_filename = f"{filename}_seg{seg_num:03d}.ts"
        segments.append({"filename": seg_filename, "duration": seg_dur})
        pos += seg_dur
    return segments

def build_show_segments(show, segment_length=420, skip_ad_if_last_leq=240):
    """
    Build segments and manifest segments for a show, inserting ads cyclically.
    - show: dict with 'name', 'filename', 'duration'
    - segment_length: segment length in seconds (default 7 minutes)
    - skip_ad_if_last_leq: if last segment length <= this (in seconds), skip mid-ad, put ad at end
    Returns segments list and flags about ad positions.
    """
    log.info(f"Segmenting show '{show['id']}'")
    segments = segment_video(show['file_path'], segment_length)

    # Logic for ad cue points every 7 minutes, except last if <=4min
    mid_ad_positions = []
    total_segments = len(segments)
    last_seg_duration = segments[-1]['duration']
    for i, seg in enumerate(segments[:-1]):  # exclude last segment
        if (i + 1) * segment_length < show['duration'] - skip_ad_if_last_leq:
            mid_ad_positions.append(i)  # cue after this segment index

    log.info(f"Mid-show ad cue points after segments: {mid_ad_positions}")
    return segments, mid_ad_positions

def build_master_manifest(output_dir, videos, ads, segment_length=420):
    """
    Create manifests for shows and ads, and build a master manifest with ads cyclically inserted.
    """
    section_header("STARTING MANIFEST CREATION")

    ad_idx = 0
    total_ads = len(ads)
    log.info(f"Number of ads to cycle: {total_ads}")
    master_segments = []
    manifest_paths = []

    # Create segment files and ad manifests
    ad_manifests = []
    for ad in ads:
        input_video_path = f"{ad['filename']}"
        output_pattern = os.path.join(output_dir, f"{ad['filename']}_seg%03d.ts")
        ffmpeg_segment(input_video_path, output_pattern, segment_length)

        ad_segments = segment_video(ad['filename'], ad['duration'], segment_length=segment_length)
        ad_manifest_path = write_manifest(output_dir, ad['name'], ad_segments, target_duration=segment_length)
        ad_manifests.append({'name': ad['name'], 'manifest': ad_manifest_path, 'segments': ad_segments})
        log.info(f"Created ad manifest for {ad['name']}")

    # Process each show: segment and manifest
    for show in videos:
        input_video_path = f"{show['file_path']}"
        output_pattern = os.path.join(output_dir, f"{show['file_path']}_seg%03d.ts")
        ffmpeg_segment(input_video_path, output_pattern, segment_length)

        show_segments, mid_ad_positions = build_show_segments(show, segment_length=segment_length)
        show_manifest_path = write_manifest(output_dir, show['id'], show_segments, target_duration=segment_length)
        manifest_paths.append(show_manifest_path)
        log.info(f"Created show manifest for {show['id']}")

        # Build master playlist entries
        seg_count = len(show_segments)
        for i in range(seg_count):
            # Append segment from show
            seg = show_segments[i]
            master_segments.append({'filename': seg['filename'], 'duration': seg['duration'], 'type': 'show', 'show': show['id']})

            # After segment i, if it is a mid ad cue, insert an ad
            if i in mid_ad_positions:
                current_ad = ad_manifests[ad_idx % total_ads]
                log.info(f"Inserting ad '{current_ad['name']}' after segment {i+1} of show '{show['name']}'")
                for ad_seg in current_ad['segments']:
                    master_segments.append({'filename': ad_seg['filename'], 'duration': ad_seg['duration'], 'type': 'ad', 'show': current_ad['name']})
                ad_idx += 1

    # After all shows, insert one last ad if any ads exist
    if ads:
        last_ad = ad_manifests[ad_idx % total_ads]
        log.info(f"Inserting final ad '{last_ad['name']}' at end of master playlist")
        for ad_seg in last_ad['segments']:
            master_segments.append({'filename': ad_seg['filename'], 'duration': ad_seg['duration'], 'type': 'ad', 'show': last_ad['name']})

    # Write master manifest
    master_manifest_path = os.path.join(output_dir, "master_playlist.m3u8")
    log.info(f"Writing master manifest: {master_manifest_path} with {len(master_segments)} segments")
    with open(master_manifest_path, "w") as f:
        f.write("#EXTM3U\n")
        f.write("#EXT-X-VERSION:3\n")
        f.write(f"#EXT-X-TARGETDURATION:{segment_length}\n")
        f.write(f"#EXT-X-MEDIA-SEQUENCE:0\n")
        for seg in master_segments:
            f.write(f"#EXTINF:{seg['duration']:.3f},\n")
            f.write(f"{seg['filename']}\n")
        f.write("#EXT-X-ENDLIST\n")

    section_header("MANIFEST CREATION COMPLETE")
    return master_manifest_path, manifest_paths, ad_manifests

def main():
    section_header("LOAD CONFIG")
    config = load_config("/home/ishitasinghfaujdar/pmsltask/manifest.json")

    # Extract config items
    videos = config["videos"]
    ads = config["ads"]
    segment_duration = config["segment_duration_sec"]
    cue_interval = config["cue_interval_sec"]
    min_last_segment = config["min_last_segment_sec"]

    # Call the manifest builder
    output_dir = "/home/ishitasinghfaujdar/pmsltask/output"
    master_manifest_path, manifest_paths, ad_manifests = build_master_manifest(
        output_dir=output_dir,
        videos=videos,
        ads =ads,

        segment_length=segment_duration
    )

    console.print(f"[bold green]Master manifest created at: {master_manifest_path}[/bold green]")



if __name__ == "__main__":
    main()
