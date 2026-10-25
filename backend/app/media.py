import json
import subprocess
from pathlib import Path

from pypdf import PdfReader

from .config import settings

# Reject playlists / external-reference demuxers as well as network protocols.
AUDIO_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,mp3,wav,flac,ogg,aac,amr,asf,matroska,webm,aiff,caf,au,w64,ac3"


class ProcessingError(Exception):
    """A safe, user-facing error; never include model output or uploaded content."""


def decode_audio(source: Path, target: Path) -> float:
    cfg = settings()
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                AUDIO_FORMATS,
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        if not 0 < duration <= cfg.max_duration_seconds:
            raise ProcessingError(f"Choose a recording under {cfg.max_duration_seconds // 60} minutes.")
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                AUDIO_FORMATS,
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-t",
                str(cfg.max_duration_seconds + 1),
                "-c:a",
                "pcm_s16le",
                "-y",
                str(target),
            ],
            capture_output=True,
            timeout=600,
            check=True,
        )
        # Check decoded duration too; container metadata can be incorrect.
        import wave

        with wave.open(str(target)) as wav:
            actual = wav.getnframes() / wav.getframerate()
        if not 0 < actual <= cfg.max_duration_seconds:
            raise ProcessingError("The recording is empty or exceeds the duration limit.")
        return actual
    except ProcessingError:
        raise
    except FileNotFoundError:
        raise ProcessingError(
            "Audio tools are unavailable on the server. Ask the server operator to install FFmpeg."
        ) from None
    except (subprocess.SubprocessError, ValueError, KeyError, OSError):
        raise ProcessingError(
            "This recording could not be read. Export it as M4A, MP3, or WAV and try again."
        ) from None


def extract_syllabus(path: Path) -> str:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 200:
            raise ProcessingError("Use an unlocked syllabus PDF with at most 200 pages, or paste its text.")
        parts = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total += len(text)
            if total > 100_000:
                raise ProcessingError(
                    "The syllabus is too long. Attach only the relevant pages or paste an excerpt."
                )
            parts.append(text)
        result = "\n".join(parts).strip()
        if not result:
            raise ProcessingError(
                "This PDF has no extractable text. Use a text-based PDF or paste the syllabus. Scanned PDFs need OCR first."
            )
        return result
    except ProcessingError:
        raise
    except Exception:
        raise ProcessingError(
            "The syllabus PDF could not be read. Try another PDF or paste its text."
        ) from None
