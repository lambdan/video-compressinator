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

ITERATION_INTERVAL_SECONDS = int(os.getenv("ITERATION_INTERVAL_SECONDS", "86400"))

PASSTHROUGH_VIDEO_CODECS = os.getenv("PASSTHROUGH_VIDEO_CODECS", "hevc,av1").split(",")
PASSTHROUGH_AUDIO_CODECS = os.getenv("PASSTHROUGH_AUDIO_CODECS", "aac,opus,mp3").split(
    ","
)
KEEP_AUDIO_LANGUAGES = os.getenv("KEEP_AUDIO_LANGUAGES", "eng").split(",")
VIDEO_ENCODER = os.getenv("VIDEO_ENCODER", "libx265")
AUDIO_ENCODER = os.getenv("AUDIO_ENCODER", "libopus")
PIX_FMT = os.getenv("PIX_FMT", "yuv420p10le")
CRF = os.getenv("CRF", "27")
VIDEO_SCALE = os.getenv("VIDEO_SCALE", None)
X_PRESET = os.getenv("X_PRESET", "slow")
VIDEO_EXTS = [".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm"]

print("Using settings:")
print(f"  DATA_DIR: {DATA_DIR}")
print(f"  MEDIA_DIR: {MEDIA_DIR}")
print(f"  PASSTHROUGH_VIDEO_CODECS: {PASSTHROUGH_VIDEO_CODECS}")
print(f"  PASSTHROUGH_AUDIO_CODECS: {PASSTHROUGH_AUDIO_CODECS}")
print(f"  KEEP_AUDIO_LANGUAGES: {KEEP_AUDIO_LANGUAGES}")
print(f"  VIDEO_ENCODER: {VIDEO_ENCODER}")
print(f"  AUDIO_ENCODER: {AUDIO_ENCODER}")
print(f"  PIX_FMT: {PIX_FMT}")
print(f"  CRF: {CRF}")
print(f"  VIDEO_SCALE: {VIDEO_SCALE}")
print(f"  X_PRESET: {X_PRESET}")
print(f"  VIDEO_EXTS: {VIDEO_EXTS}")

DATA = {}
STATUS_UNPROBED = "unprobed"
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_ENCODED = "encoded"
STATUS_ERROR = "error"


# check for ffprobe and ffmpeg
def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        print("ffmpeg is not installed")
        sys.exit(1)
    if not shutil.which("ffprobe"):
        print("ffprobe is not installed")
        sys.exit(1)


def load_data():
    global DATA
    if os.path.exists(os.path.join(DATA_DIR, "data.json")):
        print("Loading data...")
        with open(os.path.join(DATA_DIR, "data.json"), "r") as f:
            DATA = json.load(f)
    else:
        DATA = {}


def save_data():
    with open(os.path.join(DATA_DIR, "data.json"), "w") as f:
        json.dump(DATA, f, indent=4)
    print("Saved!")


def probe(video_path: str):
    ffprobe_out = None
    try:
        # ffprobe json output: ffprobe -v quiet -print_format json -show_format -show_streams
        result = subprocess.run(
            [
                "ffprobe",
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

    filesize_MB = os.path.getsize(video_path) / (1024 * 1024)
    return {
        "ffprobe": ffprobe_out,
        "filesize_MB": filesize_MB,
    }


def scan():
    # crawl MEDIA_DIR for video files
    for root, dirs, files in tqdm(os.walk(MEDIA_DIR)):
        for file in tqdm(files):
            ext = os.path.splitext(file)[1].lower()
            if ext in VIDEO_EXTS:
                path = os.path.join(root, file)
                if path not in DATA:
                    DATA[path] = {
                        "path": path,
                        "metadata": None,
                        "status": STATUS_UNPROBED,
                    }


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
    print(ffprobe_data)
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

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
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
        print("Video needs encoding :(")

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
            else:
                print(
                    f"Skipping audio stream {i} with language '{lang}' and codec '{stream['codec']}'"
                )
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
        print(f"Video codec '{vc}' not in passthrough list, needs encoding")
        good = False
    if info["audio_streams"]:
        for audio in info["audio_streams"]:
            ac = audio["codec"]
            if (
                # wrong codec but right language
                audio["codec"] not in PASSTHROUGH_AUDIO_CODECS
                and audio["language"] in KEEP_AUDIO_LANGUAGES
            ):
                print(f"Audio codec '{ac}' not in passthrough list, needs encoding")
                good = False
    return not good


def maybe_encode(source_path, info):
    if not should_encode(info):
        return STATUS_SKIPPED
    temp_path = os.path.join(TEMP_DIR, "temp.mkv")
    dest_path = os.path.splitext(source_path)[0] + "[reenc].mkv"
    if os.path.exists(dest_path):
        print(
            f"Destination file {dest_path} already exists, I guess we encoded this one already?"
        )
        return STATUS_SKIPPED
    try:
        cmd = build_ffmpeg_command(source_path, temp_path, info)
        orig_size_MB = os.path.getsize(source_path) / (1024 * 1024)
        print("Original size: {:.2f} MB".format(orig_size_MB))
        print("Running ffmpeg command: " + " ".join(cmd))
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
        new_size = os.path.getsize(temp_path) / (1024 * 1024)
        print("New size: {:.2f} MB".format(new_size))

        if new_size >= orig_size_MB:
            print(
                "Encoded file is larger than original, skipping replacement and keeping original"
            )
            os.remove(temp_path)
            return STATUS_SKIPPED

        print("You saved: {:.2f} MB!".format(orig_size_MB - new_size))
        if result.returncode != 0:
            print(f"ffmpeg error: {result.stderr.decode()}")
            return STATUS_ERROR
        else:
            print(f"Encoded {source_path} successfully!")
            # remove ext from original file and suffix
            shutil.move(temp_path, dest_path)
            return STATUS_ENCODED
    except Exception as e:
        print(f"Error encoding video: {e}")
        return STATUS_ERROR


def print_line(msg: str):
    print()
    print("######################################")
    print(msg)
    print("######################################")
    print()


def iteration():
    print("Starting iteration...")
    # scan for files
    scan()
    # probe unprobed files
    for path, info in tqdm(DATA.items(), desc="Scanning files"):
        if info["status"] == STATUS_UNPROBED:
            print_line(f"Probing {path}...")
            probe_data = probe(path)
            if probe_data["ffprobe"]:
                info = {}
                info["video"] = get_video_info(probe_data["ffprobe"])
                info["audio_streams"] = get_audio_info(probe_data["ffprobe"])
                info["subtitle_streams"] = get_subtitle_info(probe_data["ffprobe"])
                DATA[path]["info"] = info
                DATA[path]["status"] = STATUS_PENDING
            else:
                DATA[path]["status"] = STATUS_ERROR
                print(f"Error probing {path}")
    save_data()
    # maybe encode
    for path, info in tqdm(DATA.items(), desc="Processing files"):
        if info["status"] == STATUS_PENDING:
            print_line(f"Processing {path}...")
            outcome = maybe_encode(path, info["info"])
            print("Outcome: " + outcome)
            info["status"] = outcome
    save_data()


if __name__ == "__main__":
    check_ffmpeg()
    load_data()
    # create temp, data, and media directories if they don't exist
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    while True:
        print_line(
            "Starting iteration at "
            + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        iteration()
        print_line(
            f"Iteration complete, sleeping for {ITERATION_INTERVAL_SECONDS} seconds..."
        )
        for _ in tqdm(range(ITERATION_INTERVAL_SECONDS), desc="Sleeping"):
            time.sleep(1)
