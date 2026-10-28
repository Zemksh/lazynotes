# LazyNotes

Less writing. More listening.

Turn recorded university lectures into topic-wise study notes, with clear headings, bullets, and lecturer-provided examples. Spend class following the explanation, then use the notes to revise—not as a substitute for checking the original material.

## Features

- Import recordings with topic hints and optional syllabus text or PDF.
- Generate bullet-first notes with subtopics, nested details, and comparison tables.
- Save notes locally and edit headings, explanations, and bullets offline.
- Recover interrupted uploads and resume failed generation using compatible chunk checkpoints.
- Explore rule-based transcription flags and unsupervised concept groups in **Lecture Insights**, separate from Study Notes.

**Workflow:** Record → Upload → Transcribe → Generate notes → Study.

**Demo:** _Screenshot or video coming soon—add your own here._

## Stack

| Component | Technology |
| --- | --- |
| Mobile app and local storage | React Native, Expo SDK 58, TypeScript, SQLite |
| API and background jobs | FastAPI, Celery, Redis |
| Audio transcription | FFmpeg, faster-whisper |
| Notes | Gemma 3:4B through Ollama; Python consolidation |
| Lecture insights | scikit-learn TF-IDF and hierarchical clustering; Whisper diagnostic rules |

Locally runnable AI keeps processing on your own backend without requiring a paid AI API. Model downloads still require internet access; model licenses apply separately.

## Quick start

Install Docker with Compose, Ollama, Node.js 22.13+ or 24.3+, and Expo Go compatible with SDK 58 (or a matching development build).

### 1. Configure

From the repository root, copy the templates **only if you do not already have `.env` files**:

```sh
cp backend/.env.example backend/.env
cp mobile/.env.example mobile/.env
```

Key settings:

| File | Variable | Value / purpose |
| --- | --- | --- |
| `backend/.env` | `MOCK_MODE` | `false` for real notes; `true` for labelled model-free examples |
| `backend/.env` | `OLLAMA_MODEL` | `gemma3:4b` |
| `backend/.env` | `WHISPER_MODEL` | `distil-small.en` (English); use a multilingual model such as `small` for other languages |
| `backend/.env` | `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `cpu` / `int8` for the supplied image |
| `mobile/.env` | `EXPO_PUBLIC_API_URL` | `http://YOUR_COMPUTER_LAN_IP:8000`, not `localhost` on a physical phone |

All limits, timeouts, and tuning defaults are in [backend/.env.example](backend/.env.example); the mobile template is [mobile/.env.example](mobile/.env.example). No AI API key is required. Keep `.env` files private; do not place secrets in `EXPO_PUBLIC_*` variables.

**Docker overrides** `STORAGE_PATH`, `MODEL_CACHE_PATH`, `REDIS_URL`, and `OLLAMA_URL` using [compose.yaml](compose.yaml). Changing those keys in `.env` alone does not change container values.

### 2. Start Ollama and the backend

If Ollama is not already running, start it in a separate terminal:

```sh
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

For an existing Ollama service, configure `OLLAMA_HOST` in that service and restart it instead. This is an **Ollama setting**, not a LazyNotes `.env` setting. Docker must reach it at `host.docker.internal:11434`; restrict that port to your host/Docker network, never the public internet.

```sh
ollama pull gemma3:4b
docker compose up -d --build
```

Docker Desktop users may need `docker --context desktop-linux compose` instead of `docker compose`. The image includes FFmpeg and transcription dependencies. The first real job downloads Whisper into the persistent model cache; allow disk space and internet access.

For model-free development, set `MOCK_MODE=true` and skip Ollama setup. Docker services are still required. After backend configuration changes, recreate services with `docker compose up -d`; after code changes, include `--build`. Wait for active jobs to finish first.

### 3. Open the mobile app

```sh
cd mobile
npm ci
npm start
```

Put your phone and computer on the same Wi-Fi, allow ports 8000 and 8081 through the local firewall, and scan Expo's QR code. First check `http://YOUR_COMPUTER_LAN_IP:8000/health` in the phone browser. Restart Expo after editing `mobile/.env`.

Choose **New class**, import a recording, enter topics, and tap **Make notes**. Keep the app open until upload is confirmed; accepted server jobs continue after the app closes. Starting Expo does **not** start the backend, and an Expo tunnel does not tunnel the API.

Android emulator: usually `http://10.0.2.2:8000`; iOS simulator: `http://localhost:8000`. Browser/web support is not implemented for this native storage flow. API documentation is available at `http://localhost:8000/docs`.

## Optional: backend without Docker

Install Python 3.11+, FFmpeg/ffprobe, and Redis on Linux/macOS (use WSL2 on Windows). Start Redis and Ollama, then:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e './backend[transcription,test]'
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

In two more terminals, activate the same environment, enter `backend/`, and run one command in each:

```sh
celery -A app.tasks.celery worker --loglevel=WARNING --concurrency=1
celery -A app.tasks.celery beat --loglevel=WARNING --schedule=./data/celerybeat-schedule
```

Use the same `.env` and storage directory for all three processes. Run exactly one scheduler for cleanup and queue recovery. The template's localhost Redis/Ollama URLs apply to this setup.

## Demo: Lecture Insights

Open a saved class and select **Lecture Insights**. The authenticated `/jobs/{id}/insights` endpoint analyzes existing notes and diagnostics, caches the result on the server, and saves it on the phone for offline viewing. It does not rerun Whisper or Gemma; local edits do not change this analysis. Insights version 2 excludes inline transcript IDs from word features and uses unigram TF-IDF; older caches refresh when the server copy is reachable. The app shows concise results; the methodology is documented here instead.

- **Heuristic, not supervised:** transcription flags use existing Whisper log-probability, repetition, and no-speech rules. No suitable human-labelled dataset or evaluated classifier is included. Counts and excerpts come from actual saved diagnostics; missing diagnostics are marked unavailable, not reliable.
- **Unsupervised:** TF-IDF learns vocabulary from note sections, then average-link hierarchical clustering groups them by cosine distance. The group count is not fixed. A distance threshold of 0.7 controls grouping; it is not an accuracy score. Standalone sections and empty vocabulary are handled explicitly. English stopwords, lexical similarity, and the untuned threshold limit interpretation.
- **Unchanged:** Whisper transcription, Gemma generation, source validation, internal faithfulness screening, and chunk checkpoints. No additional LLM calls or model downloads are needed for insights.

To demonstrate: rebuild the backend (`docker compose up -d --build`), restart Expo, then open Insights while the server copy is still available. Open it once before presenting to cache it offline. Expired/deleted server copies cannot supply missing diagnostics; already-cached insights still work. Mock classes are explicitly labelled examples, not real recording analysis. No new environment settings are required.

## Troubleshooting

| Problem | Check |
| --- | --- |
| Backend unreachable | Phone's `/health` URL, LAN IP, firewall, and `docker compose ps`. Health checks API availability, not model readiness. |
| Ollama unreachable | Run `ollama list`; confirm `gemma3:4b` exists and the service listens on a Docker-accessible interface. |
| Missing audio dependencies | Rebuild the image; for host installs, install FFmpeg and `./backend[transcription]`. The project pins PyAV below 19 for compatibility. |
| Slow processing | CPU generation runs chunks sequentially and may take many minutes. First-use model downloads add time; no fixed completion time is guaranteed. |
| Generation failed | Read `docker compose logs --tail=100 worker`. Resume the failed job while retained inputs exist; compatible drafts avoid repeated work. |
| Upload interrupted | Retry using the saved recording and upload identity; accepted uploads are recovered without a second class. |

The backend's authenticated `POST /jobs/{id}/resume` resumes a failed job without retranscription. A new upload is a new job; prompt/schema changes invalidate older drafts. Completed notes are not automatically regenerated.

## Privacy and limitations

- Obtain permission to record and upload lectures. Processing occurs on the configured backend, not on the phone.
- AI can miss details or produce unsupported claims. Internal checks do not prove correctness; syllabus context is not evidence of what was taught. Default transcription is English-only; scanned PDFs require pasted text or a text-based PDF.
- Defaults: 250 MB audio, 10 MB PDF, three-hour recordings, four-hour job limit; pending jobs expire after 24 hours and completed results after 72 hours.
- Server audio and syllabus inputs are removed after success. Failed note-generation jobs retain recovery inputs until resume or expiry; transcripts, drafts, and diagnostics expire with results. Keep the scheduler running for cleanup.
- Downloaded notes, edits, tokens, and retry recordings remain on the phone until removed. There is no account sync, backup service, or additional database encryption. Removing a server copy does not remove local data.
- This is a trusted-network, single-machine MVP. Public deployment needs HTTPS, upload admission/rate limits, quotas, and operational hardening. The supplied transcription image is CPU-only; setting CUDA environment variables alone does not add GPU support.

## Contributing

Bug reports and focused improvements are welcome. Include reproduction steps and sanitized logs—not private recordings, transcripts, or tokens. With test dependencies installed:

```sh
pytest backend/tests -q
ruff check backend
cd mobile
npm run typecheck
node --test tests/*.test.cjs
```

Tests use fixtures and mocked generation; they do not establish real-model accuracy. Use recordings only with their owner's permission.

## License

No project license file is currently included. A license must be added before reuse permissions are clear.
