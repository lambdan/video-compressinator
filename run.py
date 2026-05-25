import sys
import datetime
import json
import subprocess
import os
import shutil
import time
from tqdm import tqdm

DATA_DIR = os.getenv("DATA_DIR", "./data")
MEDIA_DIR = os.getenv("MEDIA_DIR", "./media")
TEMP_DIR = os.getenv("TEMP_DIR", "./temp")

ITERATION_INTERVAL_SECONDS = int(os.getenv("ITERATION_INTERVAL_SECONDS", "3600"))
PASSTHROUGH_VIDEO_CODECS = os.getenv("PASSTHROUGH_VIDEO_CODECS", "hevc,av1").split(",")
PASSTHROUGH_AUDIO_CODECS = os.getenv(
    "PASSTHROUGH_AUDIO_CODECS", "aac,mp3,opus,vorbis"
).split(",")
KEEP_AUDIO_LANGUAGES = os.getenv("KEEP_AUDIO_LANGUAGES", "eng").split(",")
VIDEO_ENCODER = os.getenv("VIDEO_ENCODER", "libx265")
AUDIO_ENCODER = os.getenv("AUDIO_ENCODER", "libopus")
PIX_FMT = os.getenv("PIX_FMT", "yuv420p10le")
CRF = os.getenv("CRF", "27")
VIDEO_SCALE = os.getenv("VIDEO_SCALE", None)
X_PRESET = os.getenv("X_PRESET", "slow")
INPUT_VIDEO_EXTS = os.getenv("INPUT_VIDEO_EXTS", "mp4,mkv,avi,mov,flv,webm").split(",")
OUTPUT_VIDEO_EXT = os.getenv("OUTPUT_VIDEO_EXT", "mkv").lstrip(".")
DELETE_ORIGINAL_AFTER_DAYS = float(os.getenv("DELETE_ORIGINAL_AFTER_DAYS", "7"))


print("Using settings:")
print(f"  DATA_DIR: {DATA_DIR}")
print(f"  MEDIA_DIR: {MEDIA_DIR}")
print(f"  TEMP_DIR: {TEMP_DIR}")
print(f"  ITERATION_INTERVAL_SECONDS: {ITERATION_INTERVAL_SECONDS}")
print(f"  PASSTHROUGH_VIDEO_CODECS: {PASSTHROUGH_VIDEO_CODECS}")
print(f"  PASSTHROUGH_AUDIO_CODECS: {PASSTHROUGH_AUDIO_CODECS}")
print(f"  KEEP_AUDIO_LANGUAGES: {KEEP_AUDIO_LANGUAGES}")
print(f"  VIDEO_ENCODER: {VIDEO_ENCODER}")
print(f"  AUDIO_ENCODER: {AUDIO_ENCODER}")
print(f"  PIX_FMT: {PIX_FMT}")
print(f"  CRF: {CRF}")
print(f"  VIDEO_SCALE: {VIDEO_SCALE}")
print(f"  X_PRESET: {X_PRESET}")
print(f"  INPUT_VIDEO_EXTS: {INPUT_VIDEO_EXTS}")
print(f"  OUTPUT_VIDEO_EXT: {OUTPUT_VIDEO_EXT}")
print(f"  DELETE_ORIGINAL_AFTER_DAYS: {DELETE_ORIGINAL_AFTER_DAYS}")
print()

PROBES_FILE = os.path.join(DATA_DIR, "probes.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")
PROBES = {}
STATS = {}
BLACKLIST = {}

STATUS_UNPROBED = "unprobed"
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_ENCODED = "encoded"
STATUS_ERROR = "error"

FFMPEG_PATH = os.getenv("FFMPEG_PATH", shutil.which("ffmpeg")) or ""
FFPROBE_PATH = os.getenv("FFPROBE_PATH", shutil.which("ffprobe")) or ""


def test_write_permission(folder):
    test_file = os.path.join(folder, "test_write_permission.tmp")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        print(f"Write permission to '{folder}' is OK")
    except Exception as e:
        print(f"Error: No write permission to '{folder}': {e}")
        sys.exit(1)


# check for ffprobe and ffmpeg
def check_ffmpeg():
    if FFMPEG_PATH:
        print(f"Using ffmpeg at {FFMPEG_PATH}")
        ffmpeg_version = subprocess.run(
            [FFMPEG_PATH, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if ffmpeg_version.returncode == 0:
            print("Checking version...")
            print(ffmpeg_version.stdout.decode().splitlines()[0])
        else:
            print(f"Error checking ffmpeg version: {ffmpeg_version.stderr.decode()}")
            sys.exit(1)
    else:
        print("ffmpeg is not installed")
        sys.exit(1)

    print("ffmpeg seems ok!")
    print()

    if FFPROBE_PATH:
        print(f"Using ffprobe at {FFPROBE_PATH}")

        ffprobe_version = subprocess.run(
            [FFPROBE_PATH, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if ffprobe_version.returncode == 0:
            print("Checking version...")
            print(ffprobe_version.stdout.decode().splitlines()[0])
        else:
            print(f"Error checking ffprobe version: {ffprobe_version.stderr.decode()}")
            sys.exit(1)
    else:
        print("ffprobe is not installed")
        sys.exit(1)

    print("ffprobe seems ok!")
    print()


def load_data():
    global PROBES
    global STATS
    global BLACKLIST
    if os.path.exists(PROBES_FILE):
        print("Loading probes...")
        with open(PROBES_FILE, "r") as f:
            PROBES = json.load(f)
        print(f"Loaded probes for {len(PROBES)} files")
    else:
        PROBES = {}

    if os.path.exists(STATS_FILE):
        print("Loading stats...")
        with open(STATS_FILE, "r") as f:
            STATS = json.load(f)
        print("Loaded stats")
    else:
        STATS = {}

    if os.path.exists(BLACKLIST_FILE):
        print("Loading blacklist...")
        with open(BLACKLIST_FILE, "r") as f:
            BLACKLIST = json.load(f)
        print(f"Loaded blacklist with {len(BLACKLIST)} entries")
    else:
        BLACKLIST = {}


def save_probes():
    with open(PROBES_FILE, "w") as f:
        json.dump(PROBES, f, indent=4)
    print("Saved probes")


def save_stats():
    with open(STATS_FILE, "w") as f:
        json.dump(STATS, f, indent=4)
    print("Saved stats")


def save_blacklist():
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(BLACKLIST, f, indent=4)
    print("Saved blacklist")


def probe(video_path: str):
    ffprobe_out = None
    try:
        # ffprobe json output: ffprobe -v quiet -print_format json -show_format -show_streams
        result = subprocess.run(
            [
                FFPROBE_PATH,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            print(f"ffprobe error: {result.stderr.decode()}")
        else:
            ffprobe_out = json.loads(result.stdout.decode())
    except Exception as e:
        print(f"Error probing video: {e}")

    return ffprobe_out


def crawl_media_dir():
    paths = []
    for root, dirs, files in tqdm(os.walk(MEDIA_DIR), desc="Scanning media directory"):
        for file in tqdm(files):
            ext = os.path.splitext(file)[1].lower()
            if ext.lstrip(".") in INPUT_VIDEO_EXTS:
                path = os.path.join(root, file)
                paths.append(path)
    return paths


def get_video_info(ffprobe_data):
    if not ffprobe_data or "streams" not in ffprobe_data:
        return None

    video_streams = [s for s in ffprobe_data["streams"] if s["codec_type"] == "video"]
    if not video_streams:
        return None

    stream = video_streams[0]  # take the first video stream
    info = {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": float(stream.get("duration", 0)),
        "codec": stream.get("codec_name"),
    }
    return info


def get_audio_info(ffprobe_data):
    if not ffprobe_data or "streams" not in ffprobe_data:
        return None

    audio_streams = [s for s in ffprobe_data["streams"] if s["codec_type"] == "audio"]
    if not audio_streams:
        return None

    streams = []
    for stream in audio_streams:
        info = {
            "codec": stream.get("codec_name"),
            "language": stream.get("tags", {}).get("language", "und"),
        }
        streams.append(info)
    return streams


def get_subtitle_info(ffprobe_data):
    if not ffprobe_data or "streams" not in ffprobe_data:
        return None

    subtitle_streams = [
        s for s in ffprobe_data["streams"] if s["codec_type"] == "subtitle"
    ]
    if not subtitle_streams:
        return None

    streams = []
    for stream in subtitle_streams:
        info = {
            "codec": stream.get("codec_name"),
            "language": stream.get("tags", {}).get("language", "und"),
        }
        streams.append(info)
    return streams


def build_ffmpeg_command(input_path, output_path, info) -> list[str]:

    cmd = [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error"]
    cmd += ["-i", input_path]

    # video
    # copy if source codec is in passthrough
    # encode if not, or if VIDEO_SCALE is set (since scaling requires re-encoding)
    cmd += ["-map", "0:v:0"]  # always include video stream
    vcodec = VIDEO_ENCODER
    if info["video"]["codec"] in PASSTHROUGH_VIDEO_CODECS and not VIDEO_SCALE:
        print("Video can be copied :)")
        vcodec = "copy"
    else:
        print("Video cannot be passedthrough")

    cmd += ["-c:v", vcodec]
    if vcodec != "copy":
        if CRF:
            cmd += [
                "-crf",
                CRF,
            ]
        if VIDEO_SCALE:
            cmd += ["-vf", f"scale={VIDEO_SCALE}"]
        if PIX_FMT:
            cmd += ["-pix_fmt", PIX_FMT]
        if X_PRESET:
            cmd += ["-preset", X_PRESET]

    # audio
    # check what languages we want to keep, and if the codec is in passthrough list
    audio_streams = info["audio_streams"]
    if audio_streams:
        any_audio = False
        for i, stream in enumerate(audio_streams):
            lang = stream["language"]
            acodec = (
                "copy" if stream["codec"] in PASSTHROUGH_AUDIO_CODECS else AUDIO_ENCODER
            )
            if lang in KEEP_AUDIO_LANGUAGES:
                print(
                    f"Keeping audio stream {i} with language '{lang}' and codec '{stream['codec']}'"
                )
                cmd += ["-map", f"0:a:{i}"]
                cmd += [f"-c:a:{i}", acodec]
                any_audio = True
            else:
                print(
                    f"Skipping audio stream {i} with language '{lang}' and codec '{stream['codec']}'"
                )
        if not any_audio:
            print(
                "Kept none of the audio streams... keeping the first one as a fallback"
            )
            cmd += ["-map", "0:a:0"]
            cmd += ["-c:a:0", "copy"]
    else:
        cmd += ["-an"]  # no audio streams, disable audio

    # subtitles
    if info["subtitle_streams"]:
        cmd += ["-map", "0:s?"]  # include all subtitle streams if they exist
        cmd += ["-c:s", "copy"]  # just passthrough
    else:
        cmd += ["-sn"]  # no subtitle streams, disable subtitles

    cmd += [output_path]
    return cmd


def should_encode(info):
    good = True
    vc = info["video"]["codec"]
    if vc not in PASSTHROUGH_VIDEO_CODECS:
        # print(f"Video codec '{vc}' not in passthrough list, needs encoding")
        good = False
    if info["audio_streams"]:
        for audio in info["audio_streams"]:
            ac = audio["codec"]
            if (
                # wrong codec but right language
                ac not in PASSTHROUGH_AUDIO_CODECS
                and audio["language"] in KEEP_AUDIO_LANGUAGES
            ):
                # print(f"Audio codec '{ac}' not in passthrough list, needs encoding")
                good = False
    return not good


def build_dest_path(source_path):
    base, _ = os.path.splitext(source_path)
    return base + "[reenc]." + OUTPUT_VIDEO_EXT


def encode(source_path, info) -> str | None:
    """
    Returns output path or None on error
    """
    temp_path = os.path.join(TEMP_DIR, "temp.mkv")
    try:
        cmd = build_ffmpeg_command(source_path, temp_path, info)
        print("🔥 Running ffmpeg command: " + " ".join(cmd))
        started = datetime.datetime.now()
        print("Started at: " + started.strftime("%Y-%m-%d %H:%M:%S"))
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print("Finished at: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        duration = (datetime.datetime.now() - started).total_seconds()
        print(f"Encoding took {duration:.2f} seconds")
        add_encoding_time(duration)

        if result.returncode != 0:
            print(f"ffmpeg error: {result.stderr.decode()}")
            return None
        else:
            print(f"Encoded {source_path} successfully!")
            return temp_path
    except Exception as e:
        print(f"Error encoding video: {e}")
        return None


def print_line(msg: str):
    print()
    print("################################################")
    print(msg)
    print("################################################")
    print()


def build_info(probe):
    info = {}
    info["video"] = get_video_info(probe)
    info["audio_streams"] = get_audio_info(probe)
    info["subtitle_streams"] = get_subtitle_info(probe)
    return info


def add_to_blacklist(source_path, reason):
    print_line(f"Adding {source_path} to blacklist: {reason}")
    BLACKLIST[source_path] = reason
    save_blacklist()


def add_to_savings(savings_mb):
    if "savings_mb" not in STATS:
        STATS["savings_mb"] = 0
    STATS["savings_mb"] += savings_mb
    save_stats()


def add_encoding_time(time_seconds):
    if "encoding_time_seconds" not in STATS:
        STATS["encoding_time_seconds"] = 0
    if "encoded_files" not in STATS:
        STATS["encoded_files"] = 0
    STATS["encoded_files"] += 1  # sneak this in while we're here...
    STATS["encoding_time_seconds"] += time_seconds
    save_stats()


def maybe_cleanup(source_path, dest_path) -> str:
    if DELETE_ORIGINAL_AFTER_DAYS <= 0:
        # if the setting is 0 or negative, we never delete original files
        return "nope, deleting original is disabled"

    if not os.path.exists(source_path) or not os.path.exists(dest_path):
        print("Source or destination file does not exist, avoiding cleanup")
        return "nope, source or destination file missing"

    # if destination file has existed for n days, we assume the user is happy with it
    dest_age_days = (
        datetime.datetime.now()
        - datetime.datetime.fromtimestamp(os.path.getmtime(dest_path))
    ).total_seconds() / (3600 * 24)
    if dest_age_days >= DELETE_ORIGINAL_AFTER_DAYS:
        os.remove(source_path)
        return f"yep! The new version is {dest_age_days:.2f} days old. Deleted the original 🗑️"
    else:
        return f"nope, the new version is only {dest_age_days:.2f} days old"


ITERATIONS = 0
STARTED_AT = datetime.datetime.now()


def iteration():
    print_line(
        f"Starting iteration at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    paths = crawl_media_dir()

    # first probe all videos, and save results
    need_to_save = False
    for source_path in tqdm(paths, desc="Probing videos"):
        if source_path not in PROBES:
            PROBES[source_path] = probe(source_path)
            need_to_save = True
    # get rid of deleted files from probes
    for source_path in tqdm(list(PROBES.keys()), desc="Checking for deleted files"):
        if source_path not in paths:
            print(f"{source_path}: no longer exists, removing from probes")
            del PROBES[source_path]
            need_to_save = True
    # save if anything changed
    if need_to_save:
        save_probes()

    for source_path in tqdm(paths, desc="Processing videos"):
        print_line(f"Processing {source_path}...")

        if source_path in BLACKLIST:
            print(f"Skipped because it's blacklisted: {BLACKLIST[source_path]}")
            continue

        dest_path = build_dest_path(source_path)
        print(f"Destination would be:\n  {dest_path}")
        if os.path.exists(dest_path):
            print("Destination already exists")
            print(
                "Can we delete the original?\n  "
                + maybe_cleanup(source_path, dest_path),
            )
            print("Skipped because destination exists")
            continue

        info = build_info(PROBES[source_path])
        print(json.dumps(info, indent=4))

        if should_encode(info):
            print("This video should be encoded!")
            output = encode(source_path, info)
            if output:
                print("Encode was successful!")
                orig_size_mb = os.path.getsize(source_path) / (1024 * 1024)
                new_size_mb = os.path.getsize(output) / (1024 * 1024)
                print(f"Original size: {orig_size_mb:.2f} MB")
                print(f"New size: {new_size_mb:.2f} MB")
                if new_size_mb >= orig_size_mb:
                    print(
                        "New file is larger than original, skipping move and keeping original"
                    )
                    os.remove(output)
                    add_to_blacklist(
                        source_path, "encoded file was larger than original"
                    )
                    continue
                savings_mb = orig_size_mb - new_size_mb
                print(f"You saved {savings_mb:.2f} MB by re-encoding this video!")
                add_to_savings(savings_mb)
                shutil.move(output, dest_path)
                print(f"Moved encoded file to {dest_path}")
            else:
                print(f"Failed to encode {source_path}")
                add_to_blacklist(source_path, "encoding failed")
        else:
            # this also triggers on our own encodes...
            print("Skipped (this video does not need encoding)")

    msg = f"Iteration {ITERATIONS} complete!\n\n"
    msg += f"Uptime: {(datetime.datetime.now() - STARTED_AT).total_seconds() / 3600:.2f} hours\n\n"
    msg += "Stats: " + json.dumps(STATS, indent=4)
    print_line(msg)


if __name__ == "__main__":
    test_write_permission(DATA_DIR)
    test_write_permission(MEDIA_DIR)
    test_write_permission(TEMP_DIR)
    check_ffmpeg()
    load_data()
    print("Stats", STATS)
    print(f"Blacklist count: {len(BLACKLIST)}")
    # create temp, data, and media directories if they don't exist
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    while True:
        iteration()
        for _ in tqdm(range(ITERATION_INTERVAL_SECONDS), desc="Sleeping"):
            time.sleep(1)
