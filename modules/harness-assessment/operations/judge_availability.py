"""Controller-only judge barrier: no verdict/fallback while an API is down."""
import http.client
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

DELAYS = (30, 60, 120, 300, 600, 900)


class BufferedResponse(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status, self.headers = status, headers


def open_judge_request(request):
    # A judge response must be complete before exposing any bytes to the agent.
    # Retrying a broken partial stream cannot then duplicate a delivered verdict.
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read(16 * 1024 * 1024 + 1)
        if not data or len(data) > 16 * 1024 * 1024:
            raise InvalidJudgeReply()
        if 'text/event-stream' in response.headers.get('Content-Type', ''):
            if b'data: [DONE]' not in data:
                raise InvalidJudgeReply()
        else:
            try:
                reply = json.loads(data)
                message = reply['choices'][0]['message']
                if not (message.get('content') or message.get('tool_calls')):
                    raise InvalidJudgeReply()
            except (ValueError, KeyError, IndexError, TypeError):
                raise InvalidJudgeReply() from None
        return BufferedResponse(data, response.status, response.headers)


class InvalidJudgeReply(ValueError):
    pass


class ReviewHTTPError(Exception):
    def __init__(self, status):
        self.status = status


def failure_kind(error):
    if isinstance(error, urllib.error.HTTPError):
        code = error.code
        error.close()
        return 'http_' + str(code)
    if isinstance(error, ReviewHTTPError):
        return 'http_' + str(error.status)
    # Never expose exception messages, URLs, payloads or credentials.
    return type(error).__name__


def notify_bridge(path, event):
    connection = http.client.HTTPConnection('localhost', timeout=25)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(25)
    try:
        connection.sock.connect(str(path))
        connection.request('POST', '/availability', body=json.dumps(event),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        response.read()
        if response.status not in (200, 202):
            raise ReviewHTTPError(response.status)
    finally:
        connection.close()


class JudgeAvailability:
    def __init__(self, run_dir, notify, clock=time.monotonic, sleep=time.sleep):
        self.run_dir, self.notify, self.clock, self.sleep = run_dir, notify, clock, sleep
        self.lock = threading.RLock()
        self.call_lock = threading.Lock()
        self.active = threading.Event()
        self.since = None
        self.waited = 0.0
        self.failures = 0

    def waiting_seconds(self):
        with self.lock:
            return self.waited + (self.clock() - self.since if self.since is not None else 0)

    def wait_ready(self):
        while self.active.is_set():
            self.sleep(1)

    def _record(self, event):
        with (self.run_dir / 'judge-availability.jsonl').open('a') as f:
            f.write(json.dumps({**event, 'time': time.time()}) + '\n')
        tmp = self.run_dir / 'judge-availability.tmp'
        tmp.write_text(json.dumps(event) + '\n')
        tmp.replace(self.run_dir / 'judge-availability.json')

    def call(self, model, operation):
        # Do not let concurrent reviews race the barrier or notification state.
        with self.call_lock:
            attempts, last_alert = 0, float('-inf')
            incident = uuid.uuid4().hex
            with self.lock:
                self.since = self.clock()
                self.active.set()
            while True:
                try:
                    result = operation()
                except (OSError, urllib.error.URLError, http.client.HTTPException,
                        ReviewHTTPError, InvalidJudgeReply) as error:
                    delay = DELAYS[min(attempts, len(DELAYS)-1)]
                    attempts += 1
                    self.failures += 1
                    event = {'run': self.run_dir.name, 'model': model, 'state': 'waiting', 'incident':incident,
                             'attempt': attempts, 'retry_seconds': delay,
                             'error_type': failure_kind(error)}
                    self._record(event)
                    if self.clock() - last_alert >= 1800:
                        try:
                            self.notify(event)
                            last_alert = self.clock()
                        except Exception:
                            self._record({**event, 'notification_failed': True})
                    self.sleep(delay)
                    continue
                if attempts:
                    event = {'run': self.run_dir.name, 'model': model, 'state': 'recovered', 'incident':incident,
                             'attempt': attempts, 'retry_seconds': 0, 'error_type': ''}
                    self._record(event)
                    try:
                        self.notify(event)
                    except Exception:
                        self._record({**event, 'notification_failed': True})
                with self.lock:
                    self.waited += self.clock() - self.since
                    self.since = None
                    self.active.clear()
                return result


def wait_process(process, timeout, availability):
    """API waiting never consumes the ticket deadline or permits next-ticket advance."""
    import subprocess
    start = time.monotonic()
    while True:
        if availability.active.is_set():
            time.sleep(0.2)
            continue
        rc = process.poll()
        if rc is not None:
            return rc
        if time.monotonic() - start - availability.waiting_seconds() >= timeout:
            raise subprocess.TimeoutExpired(process.args, timeout)
        time.sleep(0.2)
