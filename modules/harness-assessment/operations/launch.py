#!/usr/bin/env python3
"""Start a supervised batch; one three-guest environment per candidate process."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

p=argparse.ArgumentParser()
p.add_argument('--root',type=Path,required=True)
p.add_argument('--candidates',nargs='+',default=['pi-admin'],choices=['pi-admin-before','pi-admin','hermes','opencode','vibe','goose','dsh'])
p.add_argument('--tasks',default=','.join(str(i) for i in range(1,51)))
p.add_argument('--timeout',type=int,default=480)
p.add_argument('--max-credits',type=float,default=20)
p.add_argument('--validate',action='store_true')
p.add_argument('--background',action='store_true')
p.add_argument('--report',action='store_true')
a=p.parse_args()
if a.report:
    from report import report
    print(json.dumps(report(a.root),indent=2))
    raise SystemExit(0)
if len(a.candidates) != 1:
    p.error('use one candidate per invocation for independent guest state')
try:
    selected = [int(x) for x in a.tasks.split(',')]
    if not selected or any(x < 1 or x > 50 for x in selected): raise ValueError()
except ValueError:
    p.error('tasks must be comma-separated numbers from 1 to 50')
a.root=a.root.resolve();a.root.mkdir(parents=True,exist_ok=False);a.root.chmod(0o700)
(a.root/'vms').mkdir()
env=os.environ.copy()
env.update(HARNESS_OPERATIONS_ROOT=str(a.root),HARNESS_OPERATIONS_CANDIDATES=','.join(a.candidates),HARNESS_OPERATIONS_TASKS=a.tasks,HARNESS_OPERATIONS_TIMEOUT=str(a.timeout),HARNESS_OPERATIONS_MAX_CREDITS=str(a.max_credits),HARNESS_OPERATIONS_VALIDATE='1' if a.validate else '0',HARNESS_OPERATIONS_SUPERVISOR=str(Path(__file__).parent/'supervisor.py'))
with (a.root/'driver.log').open('w') as log:
    child=subprocess.Popen([os.environ['HARNESS_OPERATIONS_DRIVER']+'/bin/nixos-test-driver','-o',str(a.root/'vms')],cwd=a.root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
(a.root/'process.json').write_text(json.dumps({'pid':child.pid,'started':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'root':str(a.root)},indent=2))
print(json.dumps({'pid':child.pid,'root':str(a.root),'log':str(a.root/'driver.log')}),flush=True)
if not a.background:raise SystemExit(child.wait())
