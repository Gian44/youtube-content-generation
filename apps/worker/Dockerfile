FROM python:3.12-slim

# Install FFmpeg and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for caching
COPY apps/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY apps/worker/ .

# Create data directories
RUN mkdir -p data/storage data/tts data/captions data/renders data/assets data/tmp logs

# Default command
CMD ["python", "-m", "storyfactory", "daily"]
