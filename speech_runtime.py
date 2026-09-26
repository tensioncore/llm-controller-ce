"""CE's NeMo adapter, run only by the operator-configured speech Python interpreter."""

import argparse
import hmac
import io
import json
import os
import re
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_AUDIO_BYTES = 16000 * 2 * 120 + 44


def _log_inference_error(exc, token):
    message = str(exc).replace(token, "[redacted]")
    message = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", message)
    if re.search(r"(?i)(?:ce_speech_token|authorization|(?:set-)?cookie|password|secret|api[_-]?key|(?:hf|access|refresh)[_-]?token|session(?:_id)?)[\"']?\s*[:=]|\bbearer\s+\S+", message):
        message = "[sensitive exception details redacted]"
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)[:3500]
    print(f"Speech inference failed: {type(exc).__name__}: {message}", flush=True)


def main():
    print("Speech runtime bootstrap started.", flush=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    token = os.environ.pop("CE_SPEECH_TOKEN", "")
    if not token or not os.path.isfile(args.model):
        raise RuntimeError("A managed launch and local model file are required.")

    def parent_closed():
        sys.stdin.buffer.read()
        os._exit(0)

    print("Speech runtime importing dependencies...", flush=True)
    import numpy as np
    import torch
    from nemo.collections.asr.models import ASRModel
    print("Speech runtime dependencies imported.", flush=True)

    threading.Thread(target=parent_closed, daemon=True).start()

    if not torch.cuda.is_available():
        raise RuntimeError("The speech environment needs a working CUDA-enabled PyTorch installation.")
    model = ASRModel.restore_from(restore_path=args.model, map_location="cuda")
    model.eval()
    if callable(getattr(model, "set_inference_prompt", None)):
        model.set_inference_prompt("auto")
        strip_lang_tags = getattr(getattr(model, "decoding", None), "set_strip_lang_tags", None)
        if callable(strip_lang_tags):
            strip_lang_tags(True)
    if int(model.cfg.preprocessor.sample_rate) != 16000:
        raise RuntimeError("The selected model must accept 16 kHz audio.")
    device = torch.cuda.get_device_name(torch.cuda.current_device())
    inference_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *values):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            provided = self.headers.get("Authorization", "")
            if not hmac.compare_digest(provided.encode(), f"Bearer {token}".encode()):
                self.reply(403, {"error": "Unauthorized"})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == "/health":
                self.reply(200, {"status": "ready", "device": device})
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/transcribe":
                self.reply(404, {"error": "Not found"})
                return
            if not inference_lock.acquire(blocking=False):
                self.reply(409, {"error": "Busy"})
                return
            try:
                self.connection.settimeout(15)
                size = int(self.headers.get("Content-Length", "0"))
                if not 44 < size <= MAX_AUDIO_BYTES:
                    self.reply(413, {"error": "Audio size exceeds limit"})
                    return
                with wave.open(io.BytesIO(self.rfile.read(size)), "rb") as source:
                    if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
                        raise ValueError("Expected mono 16 kHz PCM16 WAV")
                    if not 0 < source.getnframes() <= 16000 * 120:
                        raise ValueError("Invalid audio duration")
                    raw = source.readframes(source.getnframes())
                    if len(raw) != source.getnframes() * 2:
                        raise ValueError("Incomplete audio")
                audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                with torch.inference_mode():
                    result = model.transcribe(audio=[audio], batch_size=1, verbose=False)
                if isinstance(result, tuple):
                    result = result[0]
                item = result[0]
                text = item if isinstance(item, str) else item.text
                if not isinstance(text, str) or len(text) > 20000:
                    raise ValueError("Invalid transcript")
                self.reply(200, {"text": text})
            except (ValueError, wave.Error, EOFError) as exc:
                _log_inference_error(exc, token)
                self.reply(400, {"error": "Invalid recording or transcription"})
            except Exception as exc:
                _log_inference_error(exc, token)
                self.reply(500, {"error": "Transcription failed"})
            finally:
                inference_lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"Speech runtime ready on loopback port {args.port}; device: {device}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
