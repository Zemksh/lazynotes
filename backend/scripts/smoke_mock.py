"""Isolated HTTP -> Redis -> Celery -> FFmpeg -> mock notes smoke test.

Run with: python scripts/smoke_mock.py --redis-server /path/to/redis-server
Does not start Ollama or download models. All servers bind to loopback.
"""

import argparse
import io
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path

import httpx
import redis


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (httpx.HTTPError, redis.RedisError):
            pass
        time.sleep(0.2)
    raise TimeoutError("Service did not reach the expected state")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-server", default="redis-server")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    processes = []
    with tempfile.TemporaryDirectory(prefix="lazynotes-smoke-") as temp:
        redis_port, api_port = free_port(), free_port()
        env = {
            **os.environ,
            "MOCK_MODE": "true",
            "STORAGE_PATH": f"{temp}/data",
            "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
        }
        with (Path(temp) / "services.log").open("w+") as log:

            def start(command):
                process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
                processes.append(process)
                return process

            try:
                start(
                    [
                        args.redis_server,
                        "--bind",
                        "127.0.0.1",
                        "--port",
                        str(redis_port),
                        "--save",
                        "",
                        "--appendonly",
                        "no",
                        "--dir",
                        temp,
                    ]
                )
                queue = redis.Redis(host="127.0.0.1", port=redis_port)
                wait_for(queue.ping)
                start(
                    [
                        sys.executable,
                        "-m",
                        "celery",
                        "-A",
                        "app.tasks.celery",
                        "worker",
                        "--concurrency=1",
                        "--loglevel=WARNING",
                        "--without-gossip",
                        "--without-mingle",
                    ]
                )
                api_command = [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                    "--no-access-log",
                ]
                api_process = start(api_command)
                base = f"http://127.0.0.1:{api_port}"
                with httpx.Client(base_url=base, timeout=10) as client:
                    wait_for(lambda: client.get("/health").is_success)
                    audio = io.BytesIO()
                    with wave.open(audio, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                        wav.writeframes(b"\x00\x00" * 16000)
                    auth = {"Authorization": f"Bearer {secrets.token_hex(32)}"}
                    data = {"request_id": str(uuid.uuid4()), "topics": "Smoke test", "title": "Test class"}
                    files = {"audio": ("sample.wav", audio.getvalue(), "audio/wav")}
                    response = client.post("/jobs", headers=auth, data=data, files=files)
                    assert response.status_code == 202, response.status_code
                    jid = response.json()["id"]
                    assert response.json()["state"] == "queued"
                    path = f"/jobs/{jid}"

                    def finished():
                        status = client.get(path, headers=auth).json()
                        if status["state"] == "failed":
                            raise AssertionError(status["error"])
                        return status["state"] == "ready"

                    wait_for(finished, timeout=45)
                    # Restart HTTP service: job state must survive independently of Redis results.
                    api_process.terminate()
                    api_process.wait(timeout=10)
                    start(api_command)
                    wait_for(lambda: client.get("/health").is_success)
                    output = client.get(path + "/result", headers=auth)
                    assert output.status_code == 200
                    assert output.json()["mock"] is True
                    assert output.json()["duration_seconds"] == 1
                    assert client.post("/jobs", headers=auth, data=data, files=files).json()["id"] == jid
                    assert client.get(path).status_code == 401
                    assert client.delete(path, headers=auth).status_code == 204
                    assert client.get(path + "/result", headers=auth).status_code == 410
                    assert not (Path(temp) / "data" / "jobs" / jid).exists()
                    print(
                        "PASS: HTTP upload → Redis → Celery → FFmpeg → explicit mock result; API restart, idempotency, authorization, deletion."
                    )
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()


if __name__ == "__main__":
    main()
