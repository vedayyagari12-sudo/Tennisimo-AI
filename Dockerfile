FROM python:3.13-slim

# mediapipe.tasks.python.vision -> sounddevice -> libportaudio2.
# Linux wheels do not bundle PortAudio (Windows/macOS wheels do). Host-OS fact,
# not a platform fact: required on any Linux runtime, Cloud Run included.
# --no-install-recommends because libportaudio2's recommends pull ALSA tooling
# we never call; the apt lists are dead weight in the final layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements-mediapipe.txt ./backend/

# Two steps, in this order. Reasoning lives in the requirements files, not here:
#   backend/requirements.txt            (why mediapipe is absent; deps hand-enumerated)
#   backend/requirements-mediapipe.txt  (why --no-deps and why an exact pin)
# Platform-independent: this is a pip resolution fact, unchanged by the move
# from Render to Cloud Run.
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN pip install --no-deps --no-cache-dir -r backend/requirements-mediapipe.txt

COPY backend/ ./backend/

# Cloud Run injects $PORT (8080). Shell form so $PORT expands.
# --workers 1 IS LOAD-BEARING: a second uvicorn worker is a second process with
# its own ThreadPoolExecutor and its own queue-depth counter inside one
# container -- the identical correctness defect that --max-instances=1 exists
# to prevent. Concurrency comes from the async event loop, never from workers.
ENV PYTHONUNBUFFERED=1
CMD exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
