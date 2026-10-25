# Lazy Notes

A mobile app for turning one classroom recording into editable notes with transcript sources. Each upload is a standalone class. The repository contains an Expo SDK 58 / TypeScript app and a Python FastAPI / Celery backend.

## Start with the development example

Prerequisites: Node.js 22.13+ or 24.3+, Python 3.11+ (3.12 recommended), Docker with Compose. The mobile app is aligned with Expo Go SDK 58. For production-oriented development, use an SDK 58 development build.

```sh
cp backend/.env.example backend/.env
cp mobile/.env.example mobile/.env
```

Set `MOCK_MODE=true` in `backend/.env`. Set `EXPO_PUBLIC_API_URL` in `mobile/.env` to your computer's LAN address, for example `http://192.168.1.10:8000`.

```sh
docker compose up --build
```

In another terminal:

```sh
cd mobile
npm ci
npm start
```

Open the app on your phone, choose **New class**, add a short recording and topics, then **Make notes**. Mock mode still uploads, validates, decodes, queues, persists and downloads through the actual backend. Its transcript and notes are clearly labelled examples; it does not transcribe your audio. No models are downloaded in mock mode. Changing modes requires restarting the API and worker; the mode is recorded per job.

The app needs its backend even in mock mode. It never replaces a real processing failure with example notes.

## Real processing

1. Install [Ollama](https://ollama.com/download) on the host and start it with `ollama serve` if it is not already running.
2. Run `ollama pull gemma3:4b`.
3. Set `MOCK_MODE=false` in `backend/.env`, then restart backend services.
4. The first real class downloads the configured faster-whisper model to the model cache. Default: `small`, CPU, `int8`, four CPU threads. Allow network access and disk space for the first download. Subsequent runs use the cache.

Compose connects to host Ollama at `http://host.docker.internal:11434`. On Linux, Ollama must listen on an interface reachable from the Docker bridge: set `OLLAMA_HOST=0.0.0.0:11434` for the Ollama process/service and restart it. Restrict port 11434 to the host/Docker network with your firewall. Do not expose an unauthenticated Ollama server to the internet. `extra_hosts` supplies the Linux host-gateway mapping. Change the `OLLAMA_URL` environment entry in `compose.yaml` if your Ollama runs elsewhere; that entry overrides the env file inside containers.

The provided image runs transcription on CPU and requires no CUDA. Host Ollama can use a supported host GPU independently. For GPU transcription, use a compatible NVIDIA CUDA/cuDNN container or a host Python environment with the libraries required by your installed CTranslate2 version, expose the GPU to the worker with NVIDIA Container Toolkit, then set `WHISPER_DEVICE=cuda` and `WHISPER_COMPUTE_TYPE=float16`. Changing only these variables does not add GPU libraries to the supplied CPU image. Consult [faster-whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu) before configuring this optional path.

## Without Docker

Install FFmpeg (both `ffmpeg` and `ffprobe`) and Redis with your OS package manager. For example, macOS: `brew install ffmpeg redis`; Debian/Ubuntu: `sudo apt install ffmpeg redis-server`. Start a local Redis instance with `redis-server`. This backend targets Linux/macOS; use Docker or WSL2 on Windows.

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e './backend[transcription,test]'
cp backend/.env.example backend/.env
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

In separate terminals, activate the same environment, change to `backend/`, and run:

```sh
celery -A app.tasks.celery worker --loglevel=WARNING --concurrency=1
```

```sh
celery -A app.tasks.celery beat --loglevel=WARNING --schedule=./data/celerybeat-schedule
```

For model-free development, install `./backend[test]` instead and set `MOCK_MODE=true`. FFmpeg and Redis are still required. API, worker and scheduler must use the same configuration and storage directory. Run exactly one scheduler. Keep the scheduler running for retention cleanup and dispatch recovery.

## Connect a phone

- Put the phone and computer on the same Wi-Fi. The backend must listen on `0.0.0.0:8000`, and the host firewall must allow the phone to connect.
- Set `EXPO_PUBLIC_API_URL` to the computer's LAN IP, **not** `localhost`. Restart Expo after changing it. Verify `http://COMPUTER_IP:8000/health` in the phone's browser first.
- Android emulator: usually `http://10.0.2.2:8000`. iOS simulator: usually `http://localhost:8000`.
- Grant iOS local-network access when prompted. A Metro tunnel only tunnels the development bundle, not this backend.
- Use HTTP only for trusted local development. For an installed release build, configure HTTPS and an appropriate network policy. The native app is the target; browser/web builds are not implemented for this SQLite/file-upload flow.

## What is implemented

- Home with locally saved classes, pending jobs, dates and an empty state.
- Recording import, required free-text topics, optional title, and either syllabus text or PDF. A durable local copy is made before upload.
- Real upload progress when the transport reports byte counts. Processing shows stages, never invented percentages or completion times.
- SQLite persistence for pending upload identities, server job IDs, tokens, downloaded notes, transcript and section edits. Polling resumes on launch/foreground; the processing screen can be left while the app remains running.
- Editable note sections, separate original transcript, source-time jumps, highlighted review passages, local timestamp playback, and a recording-unavailable explanation.
- Protected job status, result retrieval and deletion; configurable size/duration limits; generated server filenames; retention and failure cleanup.
- An explicitly labelled development mock mode.

## Pipeline and trust boundaries

The API streams bounded uploads into generated job directories, writes metadata to SQLite, then publishes a Celery task. Processing happens outside HTTP requests. One worker slot and a filesystem lock enforce one processing job at a time on the machine. Redis carries tasks, not authoritative job status. A scheduled recovery task republishes persisted queued jobs if the API crashed before marking dispatch complete. Duplicate task delivery cannot overwrite a completed job.

FFmpeg decodes to mono 16 kHz WAV without trimming silence. faster-whisper uses Silero VAD and restores timestamps to the original recording timeline. The raw transcript is stored separately from notes. Transcription diagnostics have a small replaceable interface in `backend/app/transcription.py`; current flags are heuristics, not calibrated correctness scores.

Transcript chunks have a bounded character budget and at most one overlapping segment. Topic/syllabus excerpts are context only. Ollama receives structured untrusted data under explicit instructions to use only transcript evidence. Output is checked against Pydantic schemas, and every source ID must exist in that chunk. Invalid JSON/schema/references get one retry. Bounded pairwise consolidation merges draft notes, preserves all referenced segment IDs and retains review flags. Excessively large individual drafts fail with a plain message rather than silently truncating material. Displayed timestamps always come from referenced transcript segments.

These checks establish structural/source-ID integrity; they cannot prove every generated sentence is supported or detect every transcription mistake. Prompt-injection resistance is instruction-based, not a guarantee. No tools are exposed to the model. No outside explanations or syllabus-based transcript rewriting are included. Scanned/image-only PDFs produce an explicit request for a text-based PDF or pasted text; OCR is not simulated.

## API

Interactive documentation: `http://localhost:8000/docs`.

The client generates a random 256-bit token **before** uploading, persists it locally, and sends `Authorization: Bearer <64 lowercase hex characters>`. Only the SHA-256 token hash is stored on the server. A UUID is not authorization.

| Endpoint | Behavior |
| --- | --- |
| `POST /jobs` | Multipart `request_id` (client UUID), `audio`, `topics`, optional `title`, `syllabus_text` or `syllabus` PDF. Returns job metadata with 202. |
| `GET /jobs/{id}` | Current persisted state, timestamps, expiry and safe error. |
| `GET /jobs/{id}/result` | Structured notes and timestamped transcript; 409 until ready. |
| `DELETE /jobs/{id}` | Hides the job immediately, removes files when no worker holds them. |
| `GET /health` | API availability, development mode and file/duration limits. This is not a model/worker readiness check. |

Repeat `POST /jobs` with the same `request_id` **and token** after an ambiguous upload failure. It returns the existing job instead of creating another. Keep the payload unchanged for such retries. A deliberate new attempt after a failed job uses a new identity. Expired/deleted jobs return 410 until their metadata is cleaned; afterward they return 404.

## Retention, privacy and interruptions

Defaults are configurable in `backend/.env.example`: audio 250 MB, PDF 10 MB, duration three hours, pending-job retention 24 hours, completed-result retrieval 72 hours, processing time limit four hours. Completed result retention starts when the job finishes, not when upload began. Files are inaccessible after expiry even before cleanup runs.

Server audio, decoded audio and syllabus inputs are removed after success or handled failure. Results, raw transcript and diagnostics expire together. Cleanup runs every five minutes when the worker can service the task; active processing is not deleted underneath the worker. Orphaned pre-commit upload folders are removed after 24 hours. Metadata and result files share a persistent Docker volume. Redis has its own persistent volume. A forced machine/process kill can delay physical cleanup until recovery/expiry. Logs intentionally exclude raw recordings, transcripts and syllabus text.

Downloaded notes and edits remain local until removed; there is no account, sync or backup service. Local recordings remain in app documents for playback and retry. Access tokens and class data use the app's private SQLite storage, without additional database encryption. Device/OS backup policies may apply. Removing the app also removes its local data. The **Remove server copy** action preserves downloaded notes and local recordings. **Remove from this phone** deletes the local recording, notes and edits; the server copy still expires automatically.

Uploads are **not background uploads**. Keep the app open until the server confirms acceptance. Termination or OS suspension can interrupt them; reopening offers a safe retry using the retained file and identity. Already accepted server jobs continue while the app is closed. If an accepted response was lost, retrying recovers its ID. No resumable byte-range upload protocol is implemented: a retry may resend the entire file. If local storage fails after server acceptance, the earlier durable identity still permits recovery. Once server retention has elapsed, a class must be uploaded again if its result was never downloaded.

This is a single-machine, trusted-network MVP. Per-job capabilities protect retrieval/deletion but do not authenticate who can submit new jobs. Public hosting needs admission/rate limits, TLS, storage quotas and operational hardening. Long recordings may process slowly on CPU. Keep a phone acceptance test in the release process.

## Checks

```sh
# From repository root, with the Python environment activated
pytest backend/tests -q
ruff check backend

# Real HTTP/Redis/Celery/FFmpeg integration, example output only
python backend/scripts/smoke_mock.py --redis-server /path/to/redis-server

cd mobile
npm run typecheck
npx expo install --check
npx expo-doctor
npx expo export --platform android --platform ios
```

The smoke script launches isolated loopback-only services in a temporary directory, uploads a generated one-second WAV, retrieves explicitly mocked notes after a real worker executes FFmpeg, restarts the API, checks authorization/idempotency/deletion, and shuts down its processes. It never downloads models or touches an existing Redis database.

Manual phone acceptance: import audio and a text PDF; leave/reopen during processing; interrupt/retry an upload; follow a late-lecture source link; play from its timestamp; edit and restart offline; remove the server copy; test large text, VoiceOver/TalkBack and keyboard avoidance. Also test an unreadable audio file and a scanned PDF. Real-model acceptance needs a short intelligible recording and checks against what was actually said.

Verification performed during implementation: 22 backend tests passed; Ruff passed; TypeScript passed; Expo Doctor passed all 20 checks; Android and iOS Hermes bundles exported successfully. The actual Redis/Celery/HTTP smoke test passed with real FFmpeg decoding and explicitly mocked transcript/notes. A generated silent WAV tests decoding, not speech recognition. Real Whisper/Gemma inference was not run: faster-whisper and model weights were not installed, and Ollama was unavailable. No physical phone/emulator or Docker engine was available, so native interaction/playback, native binary builds, and the Compose image build remain unverified. Node and Redis were provisioned temporarily for checks; no system service was installed. The backend test dependency emits one upstream Starlette/httpx deprecation warning.

## Implementation references

SDK versions follow [Expo SDK 58](https://expo.dev/changelog/sdk-58). File persistence and playback use [Expo FileSystem](https://docs.expo.dev/versions/v58.0.0/sdk/filesystem/) and [Expo Audio](https://docs.expo.dev/versions/v58.0.0/sdk/audio/). Notes use [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs). Original-time VAD handling follows [faster-whisper's transcription implementation](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py).
