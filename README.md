# Lazy Notes

A mobile app for turning one classroom recording into topic-wise, editable study notes. Transcript sources are retained internally, not shown as links. Each upload is a standalone class. The repository contains an Expo SDK 58 / TypeScript app and a Python FastAPI / Celery backend.

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
4. The first real class downloads the configured faster-whisper model to the model cache. Default: `distil-small.en`, CPU, `int8`, 12 CPU threads, greedy decoding (`WHISPER_BEAM_SIZE=1`). `distil-*` models are English-only; for other languages set `WHISPER_MODEL=small` (or another multilingual model) and raise `WHISPER_BEAM_SIZE` if accuracy matters more than speed. Allow network access and disk space for the first download. Subsequent runs use the cache.

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
- Study notes with a lecture title, topic/subtopic headings, short paragraphs, bullet lists and optional comparison tables. Local edits support headings, paragraphs and bullets; tables are read-only. No timestamp links, playback, transcript navigation or reliability warnings in the notes UI. Previously downloaded paragraph-only notes remain readable.
- Protected job status, result retrieval and deletion; configurable size/duration limits; generated server filenames; retention and failure cleanup.
- An explicitly labelled development mock mode.

## Pipeline and trust boundaries

The API streams bounded uploads into generated job directories, writes metadata to SQLite, then publishes a Celery task. Processing happens outside HTTP requests. One worker slot and a filesystem lock enforce one processing job at a time on the machine. Redis carries tasks, not authoritative job status. A scheduled recovery task republishes persisted queued jobs if the API crashed before marking dispatch complete. Duplicate task delivery cannot overwrite a completed job.

FFmpeg decodes to mono 16 kHz WAV without trimming silence. faster-whisper uses Silero VAD and restores timestamps to the original recording timeline. The raw transcript is stored separately from notes. Transcription diagnostics have a small replaceable interface in `backend/app/transcription.py`; current flags are heuristics, not calibrated correctness scores.

Transcript chunks have a bounded character budget and at most one overlapping segment. Topic/syllabus excerpts are context only. Ollama receives structured untrusted data under explicit instructions to use only transcript evidence. Output is checked against Pydantic schemas, and every source ID must exist in that chunk. Invalid JSON/schema/references get one retry. Deterministic Python consolidation groups exact topic headings and merges exact duplicate sections and their references without another model call. Distinct explanations, bullets, tables, equations and examples are retained, not summarized again. The schema uses flat sections (`heading`, optional `subheading`, `body`, `bullets`, optional `comparison`, internal `source_segment_ids`) rather than recursive blocks.

Chunk drafts are checkpointed under each job's `drafts/` directory. Fingerprints include the system/task prompts, schema, transcript chunk, selected context, suggested title, model and output budget; the redesign invalidates old drafts automatically. Cached references are revalidated before reuse. Note-generation failures retain inputs, extracted PDF context, duration and the raw transcript. `POST /jobs/{id}/resume`, with the same bearer token, manually resumes a failed class; it reuses compatible completed chunks without retranscribing. New prompts invalidate drafts, so resuming an older failed class regenerates its notes. Already completed/downloaded notes are not automatically regenerated.

First-round generation optimization: Gemma receives segment IDs/text only, with previously processed overlap in `context_only`. Every generated section must reference at least one new segment; references may also include boundary context. Overlap-only sections fail validation with the existing bounded retry rather than being silently discarded. Full timestamped transcripts and original chunk boundaries remain unchanged. Gemma returns sections only; Python restores the supplied lecture title, keeping the saved result and frontend schema compatible. No `refs` alias is used. Topic phrases and syllabus paragraphs are selected by non-generic term matches (including short abbreviations); matching passages retain their definitions/expansions. This is lexical filtering, not semantic understanding: synonyms or transcription errors can hide relevant context. Topics/syllabus remain interpretation context, never evidence.

Checkpoint fingerprints now also cover the generation-only schema, exact wire payload and overlap ownership. Older drafts will be regenerated on manual resume, but complete saved notes remain readable. No inference settings were changed. `PYTHONPATH=backend python backend/scripts/audit_generation_tokens.py` compares the implemented changes with recorded pre-change fixture sizes without inference. Character-based estimates show about 18% less prompt text for the synthetic 240-segment case at four calls, approximately 7% less minimal mock output from title removal, and essentially no prompt savings for the tiny fixtures because the overlap instructions add overhead. These are not Gemma tokenizer or latency measurements; real-data completeness, boundary repetition, truncation and runtime still need an explicitly approved benchmark.

### Bullet-first study notes

Generated notes now default to concise but complete bullets under topic/subtopic headings; `body` remains optional for essential prose and older saved notes. `bullets` accepts either a plain string or `{"text": "parent", "children": ["supporting explanation", "lecturer example"]}`. One nested level keeps Gemma's JSON small; deeper organization uses subheadings, not a recursive schema. The previous 30-bullet and 2,000-character-per-bullet limits are removed. No fixed word/bullet count is requested; the existing overall inference budget still applies and truncated output is rejected, not silently accepted.

Python deduplicates identical parent bullets under matching topic/subtopic scopes and merges their unique children and source references. Child deduplication is restricted to the same parent; differently worded bullets, differing parents and context-dependent steps are retained. Nested children participate in internal faithfulness screening with their parent context. The phone renders indented details and can edit/add/remove parent and child bullets without flattening multiline equations or examples. No source references or internal findings are displayed.

The prompt and schema changes invalidate generation checkpoints; existing downloaded notes remain readable, but older mobile builds should be updated before receiving nested-bullet results. Saved classes are not automatically regenerated. The offline size audit preserves identical fixture words: paragraph-plus-bullet JSON is 381 characters, flat bullets 374 (about 2% smaller), and nested bullets 396 (about 4% larger). These correspond to rough estimates of 96, 94 and 99 tokens using characters/4, not Gemma tokenizer counts. Filler reduction may help actual output length, but output-token savings, completeness and speed need an approved real-model benchmark; nesting alone is not a speed optimization.

### Internal reliability (not student-facing)

Conservative organization now preserves lecture sequence instead of moving every later return to a topic beside its first occurrence. Explicit heading forms (`Introduction to X`, `Examples of X`, `Applications of X`, `Discussion of X`) become topic `X` with the corresponding subheading; other relationships are not guessed. Consecutive matching topics share the displayed heading, with existing subheadings or a neutral “Continued discussion” label. Generation instructions request consistent topics and meaningful subheadings; later chunks get a short continuation instruction that explicitly preserves new material. The instruction and continuation status participate in checkpoint fingerprints.

Within matching topic **and subtopic** scopes, Python removes exact complete-sentence/bullet repetitions (whitespace-normalized only) and unions their section-level source references into the retained occurrence. Short or context-dependent fragments/steps are conservatively retained; equations, differing numbers/negations, and distinct explanations are not paraphrased. Paragraph boundaries and comparison tables remain intact. Near-duplicate introductory openings are compared with a bounded word-level lexical check and stored only in `reliability.json` under `organization`; they are never automatically deleted or rewritten. These findings require editorial judgment, particularly when similar introductions diverge into unique examples. Section-level references remain coarse provenance, not proof of sentence-level support. No additional model calls or embedding dependencies are introduced. Existing saved results/drafts are not modified in place.

`reliability.json` is a server-only artifact with separate transcription and note-faithfulness findings; it is not part of the result endpoint. Whisper log probability, compression ratio and no-speech probability are screening heuristics. Raw diagnostics and transcripts stay separate and unchanged.

`backend/app/reliability.py` screens each note claim against its cited transcript text (never syllabus): low content-word overlap, numbers absent from evidence, and possible negation mismatches. Findings identify a section/block and its internal sources; no claim is silently rewritten or deleted. Overlap is a retrieval signal, **not a probability of truth**. A clean report does not establish factual correctness. The English-oriented rules can miss unsupported claims and flag valid paraphrases, formulas, unit conversions or multilingual notes. Titles are compared against the full transcript. The replaceable `FaithfulnessChecker` interface can later accept an evaluated classifier/NLI model without changing note presentation.

Sentence embeddings were considered: [Sentence Transformers similarity](https://www.sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html) can retrieve semantically related evidence, but similarity does not establish entailment. [NLI](https://sbert.net/examples/cross_encoder/training/nli/README.html) explicitly distinguishes entailment, contradiction and neutral text pairs. Neither is installed/enabled here: model downloads and CPU inference would add cost before calibration on labelled lecture claims. The initial screen uses only Python, with no extra Gemma calls and no displayed hallucination percentage.

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

Server audio, decoded audio and syllabus inputs are removed after success (including successful resume) or failures before note generation. Note-generation failures retain recovery inputs until resume or expiry. Results, raw transcripts, checkpoints and internal diagnostics expire together. Cleanup runs every five minutes when the worker can service the task; active processing is not deleted underneath the worker. Orphaned pre-commit upload folders are removed after 24 hours. Metadata and result files share a persistent Docker volume. Redis has its own persistent volume. A forced machine/process kill can delay physical cleanup until recovery/expiry. Logs intentionally exclude raw recordings, transcripts and syllabus text.

Downloaded notes and edits remain local until removed; there is no account, sync or backup service. Local recordings remain in app documents for retry; there is no playback interface. Access tokens and class data use the app's private SQLite storage, without additional database encryption. Device/OS backup policies may apply. Removing the app also removes its local data. The **Remove server copy** action preserves downloaded notes and local recordings. **Remove from this phone** deletes the local recording, notes and edits; the server copy still expires automatically.

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
node --test tests/*.test.cjs
npx expo install --check
npx expo-doctor
npx expo export --platform android --platform ios
```

The smoke script launches isolated loopback-only services in a temporary directory, uploads a generated one-second WAV, retrieves explicitly mocked notes after a real worker executes FFmpeg, restarts the API, checks authorization/idempotency/deletion, and shuts down its processes. It never downloads models or touches an existing Redis database.

Manual phone acceptance: import audio and a text PDF; leave/reopen during processing; interrupt/retry an upload; inspect topic/subtopic hierarchy, paragraphs, bullets and horizontally scrollable comparison tables; edit and restart offline; open an older saved class; remove the server copy; test large text, VoiceOver/TalkBack and keyboard avoidance. Confirm no source IDs, timestamps or reliability warnings appear. Also test an unreadable audio file and a scanned PDF. Real-model acceptance requires explicit approval to use a recording and checks against what was actually said.

Study-notes redesign verification: 43 backend tests passed (one optional Whisper decoder test skipped because the local test environment lacks faster-whisper); Ruff and TypeScript passed; all 8 frontend tests passed; Android and iOS Hermes bundles exported successfully. Tests use mocked generation, synthetic audio and native-component stubs. They verify structure, source integrity, checkpoint invalidation/reuse, internal-only diagnostics, PDF-context resume and legacy saved notes. They do not prove real-model coverage/faithfulness or physical-phone layout. No real recordings or model inference were run for this redesign; no running backend services were restarted. The backend tests emit one upstream Starlette/httpx deprecation warning.

To deploy, wait until current processing finishes, rebuild the backend with `docker compose up -d --build`, and restart Expo. Use `docker --context desktop-linux compose ...` if using Docker Desktop rather than the system daemon. Completed saved notes remain unchanged; a new class or explicitly resumed failed class uses the new generator. Native development builds must be rebuilt to remove the unused audio plugin; Expo Go needs only a reload.

## Implementation references

SDK versions follow [Expo SDK 58](https://expo.dev/changelog/sdk-58). File persistence uses [Expo FileSystem](https://docs.expo.dev/versions/v58.0.0/sdk/filesystem/). Notes use [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs). Internal original-time VAD handling follows [faster-whisper's transcription implementation](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py).
