"""Narrow local Claude review bridge. Key stays on the source host.

Only nonstreaming Sonnet purpose reviews; no arbitrary upstream URLs, headers,
models or tools. Private Unix socket may be forwarded through authenticated SSH.
Logs contain usage and estimated USD only, never request/response text.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import re
import socketserver
import threading
import time
import urllib.request
import urllib.error

MODEL = 'claude-sonnet-5-5'
RUN = re.compile(r'^ops-[A-Za-z0-9._-]{1,120}$')
MAX_OUTPUT = 2048
# Official Claude pricing, checked 2026-11-18; compatibility route has no cache.
INPUT_USD_MTOK, OUTPUT_USD_MTOK = 2, 10


def validate_alert(obj):
    if set(obj) != {'run','model','state','attempt','retry_seconds','error_type','incident'}:
        raise ValueError('invalid alert fields')
    if not re.fullmatch(r'[0-9a-f]{32}', obj['incident']):
        raise ValueError('invalid incident id')
    if not RUN.fullmatch(obj['run']) or not re.fullmatch(r'[A-Za-z0-9._:+-]{1,100}', obj['model']):
        raise ValueError('invalid alert identity')
    if obj['state'] not in ('waiting','recovered','test'):
        raise ValueError('invalid alert state')
    if type(obj['attempt']) is not int or not 0 <= obj['attempt'] <= 1000000:
        raise ValueError('invalid attempts')
    if type(obj['retry_seconds']) is not int or not 0 <= obj['retry_seconds'] <= 900:
        raise ValueError('invalid delay')
    if not re.fullmatch(r'[A-Za-z0-9_]{0,80}', obj['error_type']):
        raise ValueError('invalid error category')
    return obj


def post_alert(event):
    # Only this credential-source host receives the existing Sysadmin bot env.
    token, channel, url = (os.environ.get(k) for k in
                          ('MATTERMOST_BOT_TOKEN','MATTERMOST_CHANNEL_ID','MATTERMOST_URL'))
    if not all((token, channel, url)):
        raise RuntimeError('Mattermost configuration missing')
    state = {'waiting':'ÎN AȘTEPTARE: judecător indisponibil; evaluarea nu avansează',
             'recovered':'REVENIT: judecătorul a răspuns; evaluarea poate continua',
             'test':'TEST notificare: verificare controlată, fără incident real'}[event['state']]
    prefix = '[TEST CONTROLAT] ' if event['run'].startswith('ops-availability-selftest') else ''
    msg = (f"{prefix}Harness assessment — {state}\nRun: {event['run']}\nModel: {event['model']}\n"
           f"Încercări nereușite: {event['attempt']}; următoarea în {event['retry_seconds']}s; "
           f"categorie: {event['error_type'] or '-'}")
    req = urllib.request.Request(url.rstrip('/')+'/api/v4/posts',
        data=json.dumps({'channel_id':channel,'message':msg}).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        if response.status != 201:
            raise RuntimeError('Mattermost post failed')


class AlertOutbox:
    """Durable metadata-only notifications, including when Mattermost is down."""
    def __init__(self, directory, deliver=post_alert):
        self.deliver, self.lock = deliver, threading.Lock()
        self.ledger = directory/'availability-alerts.jsonl'
        self.queue = directory/'availability-alerts-pending.jsonl'
        self.sent, self.pending = set(), {}
        if self.ledger.exists():
            for line in self.ledger.read_text().splitlines():
                event=json.loads(line);event.pop('delivered_at',None)
                self.sent.add(json.dumps(event,sort_keys=True))
        if self.queue.exists():
            for line in self.queue.read_text().splitlines():
                event=json.loads(line);identity=json.dumps(event,sort_keys=True)
                if identity not in self.sent:self.pending[identity]=event

    def _attempt(self, identity, event):
        try:
            self.deliver(event)
        except Exception:
            return False
        with self.ledger.open('a') as f:
            f.write(json.dumps({**event,'delivered_at':time.time()})+'\n')
        self.sent.add(identity);self.pending.pop(identity,None)
        return True

    def send(self, event):
        identity=json.dumps(event,sort_keys=True)
        with self.lock:
            if identity in self.sent:return True
            if identity not in self.pending:
                with self.queue.open('a') as f:f.write(json.dumps(event)+'\n')
                self.pending[identity]=event
            # Keep waiting/recovery messages in order if an older one is queued.
            if next(iter(self.pending)) != identity:return False
            return self._attempt(identity,event)

    def retry(self):
        with self.lock:
            if self.pending:
                identity=next(iter(self.pending))
                self._attempt(identity,self.pending[identity])

    def run(self):
        while True:
            time.sleep(30)
            try:self.retry()
            except OSError:pass  # keep queue on disk, retry later


def validate(run, obj):
    if not RUN.fullmatch(run):
        raise ValueError('invalid assessment run')
    if obj.get('model') != MODEL or obj.get('stream', False) is not False:
        raise ValueError('only nonstreaming Sonnet reviews supported')
    if set(obj) - {'model', 'stream', 'max_tokens', 'messages'}:
        raise ValueError('unsupported request fields')
    messages = obj.get('messages')
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError('review requires system and user messages')
    if [m.get('role') for m in messages] != ['system', 'user']:
        raise ValueError('invalid review roles')
    if any(not isinstance(m.get('content'), str) for m in messages):
        raise ValueError('text reviews only')
    encoded = json.dumps(obj).encode()
    if len(encoded) > 24000 or b'PRIVATE_BUSINESS_RECORD_' in encoded:
        raise ValueError('review payload rejected')
    return {**obj, 'max_tokens': MAX_OUTPUT, 'stream': False}


def estimated_usd(usage):
    return (int(usage.get('prompt_tokens', 0)) * INPUT_USD_MTOK +
            int(usage.get('completion_tokens', 0)) * OUTPUT_USD_MTOK) / 1_000_000


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == '/availability':
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 2048:raise ValueError('invalid alert size')
                event = validate_alert(json.loads(self.rfile.read(size)))
            except (ValueError, TypeError, KeyError):
                return self.reply(400, {'error':'invalid alert'})
            delivered = self.server.outbox.send(event)
            return self.reply(200 if delivered else 202, {'delivered':delivered,'queued':not delivered})
        if self.path != '/v1/chat/completions':
            return self.reply(403, {'error': 'route not allowed'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 24000:
                raise ValueError('invalid length')
            run = self.headers.get('X-Assessment-Run', '')
            obj = validate(run, json.loads(self.rfile.read(size)))
        except (ValueError, TypeError, AttributeError):
            return self.reply(400, {'error': 'invalid review request'})
        # Serialize quota accounting and inference; reviews are infrequent.
        with self.server.lock:
            records = []
            if self.server.ledger.exists():
                records = [json.loads(l) for l in self.server.ledger.read_text().splitlines() if l]
            spent = sum(r.get('estimated_usd', 0) for r in records)
            calls = sum(r['run'] == run and r.get('status') == 'completed' for r in records)
            if spent + 0.10 > self.server.max_usd or calls >= 20:
                return self.reply(402, {'error': 'review budget reached'})
            record = {'run': run, 'model': MODEL, 'time': time.time(),
                      'pricing': 'estimated USD; input2/output10 per MTok; no caching'}
            # Persist every attempt. Failed availability retries do not consume
            # the completed-review allowance; reported spend stays bounded.
            with self.server.ledger.open('a') as f:
                f.write(json.dumps({**record, 'status': 'admitted'}) + '\n')
            req = urllib.request.Request('https://api.anthropic.com/v1/chat/completions',
                data=json.dumps(obj).encode(), headers={
                    'Authorization': 'Bearer ' + self.server.key,
                    'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=180) as response:
                    answer = json.load(response)
                usage = answer.get('usage')
                if not isinstance(usage, dict) or 'prompt_tokens' not in usage or 'completion_tokens' not in usage:
                    raise ValueError('missing billing usage')
                record.update(status='completed', usage=usage, estimated_usd=estimated_usd(usage))
                answer['_assessment_billing'] = {'estimated_usd': record['estimated_usd'], 'usage': usage}
            except Exception as e:
                record.update(status='failed', error_type=type(e).__name__)
                answer = {'error': 'Claude review failed'}
            with self.server.ledger.open('a') as f:
                f.write(json.dumps(record) + '\n')
            self.reply(200 if record['status'] == 'completed' else 502, answer)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--socket', type=Path, required=True)
    p.add_argument('--key-file', type=Path, required=True)
    p.add_argument('--ledger', type=Path, required=True)
    p.add_argument('--max-usd', type=float, default=10)
    args = p.parse_args()
    os.umask(0o077)
    if args.key_file.stat().st_mode & 0o077:
        raise SystemExit('credential file must not be group/world accessible')
    key = None
    for line in args.key_file.read_text().splitlines():
        m = re.match(r'\s*(?:export\s+)?ANTHROPIC_API_KEY=(.*)$', line)
        if m:
            key = m.group(1).strip().strip('\"\'')
    if not key:
        raise SystemExit('configured credential assignment missing')
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink an existing socket: avoid taking over another bridge.
    server = Server(str(args.socket), Handler)
    server.key, server.ledger, server.max_usd = key, args.ledger, args.max_usd
    server.lock = threading.Lock()
    server.outbox = AlertOutbox(args.ledger.parent)
    threading.Thread(target=server.outbox.run,daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        args.socket.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
