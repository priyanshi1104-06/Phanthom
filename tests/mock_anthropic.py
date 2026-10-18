"""A stand-in for the Claude API on localhost, so the real SDK code path is tested without a key."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockAnthropic:
    """Answers POST /v1/messages with reply(body) -> (status, json). Records every request."""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                headers = {k.lower(): v for k, v in self.headers.items()}
                mock.requests.append({"path": self.path, "headers": headers, "body": body})
                status, payload = mock.reply(body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def message(body, answer=None, stop_reason="end_turn", text=None):
    """A Messages API response: an (empty) thinking block first, then the JSON answer as text."""
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": text if text is not None else json.dumps(answer)},
        ],
        "stop_reason": stop_reason, "stop_sequence": None, "stop_details": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def error(status, kind, text):
    return status, {"type": "error", "error": {"type": kind, "message": text}}
