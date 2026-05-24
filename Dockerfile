FROM python:3.11-slim

COPY --from=mwader/static-ffmpeg:7.1 /ffmpeg /ffmpeg
COPY --from=mwader/static-ffmpeg:7.1 /ffprobe /ffprobe
ENV FFMPEG_PATH=/ffmpeg
ENV FFPROBE_PATH=/ffprobe

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .

# volume mount these
ENV DATA_DIR=/data
ENV MEDIA_DIR=/media
ENV TEMP_DIR=/tmp

ENTRYPOINT ["python", "run.py"]
