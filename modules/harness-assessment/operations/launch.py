#!/usr/bin/env python3
"""Start a supervised batch; one three-guest environment per candidate process."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile

p=argparse.ArgumentParser()
p.add_argument('--root',type=Path,required=True)
p.add_argument('--candidates',nargs='+',default=['pi-admin'],choices=['pi-admin-before','pi-admin','hermes','opencode','vibe','goose','dsh','omp','letta','openclaw','qwen','prime','deepagents','kimi'])
p.add_argument('--tasks')
p.add_argument('--prepared-from',type=Path,help='inactive practice run whose home and notes are copied into this isolated run')
p.add_argument('--track',choices=['operations','cyber','practice'],default='operations')
p.add_argument('--model', default='deepseek-v4.1-flash-1017')
p.add_argument('--review-model', default='kimi-k3')
p.add_argument('--purpose-review-model', choices=['claude-sonnet-5-5'])
p.add_argument('--claude-review-socket', type=Path)
p.add_argument('--judge-alert-socket', type=Path, default=Path.home()/'.cache/harness-assessment/claude-review.sock')
p.add_argument('--ticket-credits', type=float, default=0.60)
p.add_argument('--timeout',type=int,default=480)
p.add_argument('--ticket-calls',type=int,default=60)
p.add_argument('--max-credits',type=float,default=20)
p.add_argument('--validate',action='store_true')
p.add_argument('--background',action='store_true')
p.add_argument('--report',action='store_true')
a=p.parse_args()
if a.purpose_review_model and (not a.claude_review_socket or not a.claude_review_socket.is_socket()):
    p.error('Claude purpose review requires an existing private review socket')
import math
if not math.isfinite(a.ticket_credits) or a.ticket_credits <= 0:
    p.error('ticket-credits must be finite and positive')
if not 1 <= a.ticket_calls <= 300:
    p.error('ticket-calls must be between1 and300')
if a.model == a.review_model:
    p.error('select a different model for independent purpose review')
if a.report:
    from report import report
    print(json.dumps(report(a.root),indent=2))
    raise SystemExit(0)
if not a.validate and not a.judge_alert_socket.is_socket():
    p.error('judge availability notifications require the private alert bridge socket')
if len(a.candidates) != 1:
    p.error('use one candidate per invocation for independent guest state')
from catalog import TASKS
if a.track=='cyber':
    from cyber import TASKS
if a.track=='practice':
    from practice import TASKS
    if a.validate:p.error('practice is for contender preparation, not infrastructure controls')
allowed={t['id'] for t in TASKS}
if a.tasks is None:a.tasks=','.join(str(t['id']) for t in TASKS)
try:
    selected = [int(x) for x in a.tasks.split(',')]
    if not selected or any(x not in allowed for x in selected): raise ValueError()
except ValueError:
    p.error('tasks must be comma-separated ticket numbers in the selected track')
a.root=a.root.resolve();a.root.mkdir(parents=True,exist_ok=False);a.root.chmod(0o700)
if a.prepared_from:
    if a.validate:p.error('prepared-from cannot be used for infrastructure controls')
    from prepared_snapshot import snapshot
    snapshot(a.prepared_from,a.root,a.candidates[0])
(a.root/'vms').mkdir()
# The test driver puts VLAN/control sockets directly in XDG_RUNTIME_DIR.
# A shared login runtime directory collides across independent batches.
# Keep the path short enough for Unix sockets, regardless of run-root length.
runtime=Path(tempfile.mkdtemp(prefix='ops-runtime-'))
env=os.environ.copy()
env['HARNESS_PREPARED_PROFILE']='1' if a.prepared_from else '0'
env['HARNESS_JUDGE_ALERT_SOCKET']=str(a.judge_alert_socket.resolve())
if a.purpose_review_model:
    env['HARNESS_PURPOSE_REVIEW_MODEL']=a.purpose_review_model
    env['HARNESS_CLAUDE_REVIEW_SOCKET']=str(a.claude_review_socket.resolve())
env.update(HARNESS_SOLVER_MODEL=a.model, HARNESS_REVIEW_MODEL=a.review_model, HARNESS_TICKET_CREDITS=str(a.ticket_credits), HARNESS_TICKET_CALLS=str(a.ticket_calls))
env['XDG_RUNTIME_DIR']=str(runtime)
env.update(HARNESS_OPERATIONS_TRACK=a.track,HARNESS_OPERATIONS_ROOT=str(a.root),HARNESS_OPERATIONS_CANDIDATES=','.join(a.candidates),HARNESS_OPERATIONS_TASKS=a.tasks,HARNESS_OPERATIONS_TIMEOUT=str(a.timeout),HARNESS_OPERATIONS_MAX_CREDITS=str(a.max_credits),HARNESS_OPERATIONS_VALIDATE='1' if a.validate else '0',HARNESS_OPERATIONS_SUPERVISOR=str(Path(__file__).parent/'supervisor.py'))
with (a.root/'driver.log').open('w') as log:
    child=subprocess.Popen([os.environ['HARNESS_OPERATIONS_DRIVER']+'/bin/nixos-test-driver','-o',str(a.root/'vms')],cwd=a.root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
(a.root/'process.json').write_text(json.dumps({'pid':child.pid,'started':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'root':str(a.root),'runtime':str(runtime)},indent=2))
print(json.dumps({'pid':child.pid,'root':str(a.root),'log':str(a.root/'driver.log')}),flush=True)
if not a.background:raise SystemExit(child.wait())
