FROM python:3.13-slim

# System shared libraries the Linux wheels expect to find already on the host.
# Derived, not guessed: every .so in every wheel this image installs was read
# for its ELF DT_NEEDED entries, and the three below are the only SONAMEs left
# unsatisfied by python:3.13-slim (libc/libm/libdl/libpthread/librt/libmvec/
# ld-linux come from glibc; libstdc++.so.6 and libgcc_s.so.1 come in with apt
# itself, which is C++; libz.so.1 comes in with CPython's zlib module).
# --no-install-recommends throughout, and the apt lists are dead weight in the
# final layer.
#
#   libportaudio2  mediapipe.tasks.python.vision -> sounddevice -> PortAudio.
#                  The sounddevice wheel is pure Python and dlopen()s
#                  "portaudio" AT IMPORT TIME; Linux wheels bundle no copy
#                  (Windows/macOS ones do), so without this the very first
#                  `import mediapipe...` dies on
#                  OSError: PortAudio library not found. Host-OS fact, not a
#                  platform fact: required on any Linux runtime, Cloud Run
#                  included. Its recommends pull ALSA tooling we never call.
#   libgles2       Provides libGLESv2.so.2. mediapipe 0.10.35 ships exactly one
#                  native object, mediapipe/tasks/c/libmediapipe.so, and that
#                  object hard-links against OpenGL ES -- MediaPipe's GPU
#                  calculator layer is compiled in whether or not we ask for a
#                  GPU delegate. It is ctypes-loaded lazily, which is why the
#                  failure surfaced at PoseLandmarker.create_from_options()
#                  rather than at import. Debian slim ships no GL stack at all.
#   libegl1        Provides libEGL.so.1, the second DT_NEEDED of that same
#                  libmediapipe.so. libgles2 does NOT depend on it (both come
#                  from libglvnd and are siblings), so it must be named here or
#                  it simply becomes the next missing .so after libgles2 lands.
#
# Deliberately NOT here: libGL.so.1 / GTK / X11 / Qt. opencv-python-headless is
# the pinned cv2 (see backend/requirements.txt) and its bundled objects need
# nothing beyond glibc and libz -- the GUI wheels are what would have dragged a
# desktop stack into a server image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libportaudio2 \
      libgles2 \
      libegl1 \
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
# DEVIATION FROM docs/PIPELINE.md 1.20.1 (documented CMD is stale, see commit msg):
# every module under backend/app/ imports absolutely from `app.` (e.g.
# `from app.api import routes_analyses`), so `backend.app.main` raises
# ModuleNotFoundError: No module named 'app'. /app/backend on PYTHONPATH and the
# `app.main:app` target are the real importable ASGI path.
ENV PYTHONPATH=/app/backend
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
