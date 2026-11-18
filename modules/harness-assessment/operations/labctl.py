#!/usr/bin/env python3
"""Guest administration transport. Commands execute only on named NixOS guests."""
import argparse
import json
import urllib.request

p = argparse.ArgumentParser()
s = p.add_subparsers(dest='operation', required=True)
e = s.add_parser('exec')
e.add_argument('node', choices=['admin', 'app', 'data'])
e.add_argument('command', help='One quoted shell command; use heredocs for file edits')
r = s.add_parser('review')
r.add_argument('purpose', help='Purpose, necessity, scope and proposed command')
a = vars(p.parse_args())
req = urllib.request.Request('http://127.0.0.1:5099/lab/' + a.pop('operation'),
                             data=json.dumps(a).encode(), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=180) as response:
    result = json.load(response)
print(json.dumps(result))
raise SystemExit(result.get('returncode', 0))
