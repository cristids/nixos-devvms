"""Redact operational command output inside the guest before it reaches any log.

This is an output guard, not an authorization boundary against guest root. All
credentials in this lab are generated for this disposable infrastructure only.
"""
import base64
import json
import os
import signal
import time
import pathlib
import re
import subprocess
import sys
import tempfile

def redact(output, secret_root):
    blocked = False
    for p in pathlib.Path(secret_root).rglob('*'):
        if p.is_file() and not p.is_symlink() and p.stat().st_size < 65536 and p.name != 'known_hosts' and not p.name.endswith(('.pub', '.crt', '.csr')):
            secret = p.read_text(errors='replace').strip()
            if len(secret) >= 16 and secret in output:
                output = output.replace(secret, '[SECRET OUTPUT BLOCKED]')
                blocked = True
    pattern = r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----'
    if re.search(pattern, output, re.S):
        output = re.sub(pattern, '[PRIVATE KEY OUTPUT BLOCKED]', output, flags=re.S)
        blocked = True
    if re.search(r'PRIVATE_BUSINESS_RECORD_[A-Za-z0-9]+', output):
        output = '[BUSINESS CONTENT OUTPUT BLOCKED]'
        blocked = True
    return output[:24000], blocked


def business_content_opened(trace, root='/srv/business'):
    # strace -yy resolves successful returned file descriptors, including
    # relative paths. Stat calls, directory listing and O_PATH are metadata.
    opened=re.compile(r'=\s+\d+<'+re.escape(root.rstrip('/'))+r'/[^>]+>\s*$')
    return any(opened.search(line) and 'O_DIRECTORY' not in line and 'O_PATH' not in line
               for line in trace.splitlines())


def run_guarded(command, secret_root='/root/.config/assessment', business_root='/srv/business', audit=True):
    # Trace only file opens, never read/write buffers or exec arguments. The
    # private temporary trace is deleted before any result leaves the guest.
    with tempfile.TemporaryDirectory(prefix='ops-open-audit-') as temp:
        root = pathlib.Path(temp)
        trace, done, captured = root/'trace', root/'done', root/'output'
        # seccomp-bpf cannot safely detach surviving descendants.
        tracer=['strace','-f','-qq','-yy','-e',
                'trace=open,openat,openat2','-o',str(trace)] if audit else []
        # strace -f waits for daemon descendants after the foreground shell
        # exits. Record foreground completion explicitly, then detach the tracer
        # without killing the service the command intentionally started.
        wrapper='bash -lc "$1"; rc=$?; printf "%s" "$rc" > "$2.tmp"; mv "$2.tmp" "$2"; exit "$rc"'
        with captured.open('wb') as out:
            proc=subprocess.Popen([*tracer,'bash','-c',wrapper,'guard',command,str(done)],
                                  stdout=out,stderr=out,start_new_session=True)
            deadline=time.monotonic()+145
            while proc.poll() is None and not done.exists() and time.monotonic()<deadline:
                time.sleep(0.02)
            if done.exists():
                rc=int(done.read_text())
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)  # strace detaches surviving descendants
                    try:proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill();proc.wait()
            elif proc.poll() is not None:
                rc=proc.returncode
            else:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait();rc=124
        output=captured.read_bytes().decode(errors='replace')
        if rc==124:output+='\nGuest command timed out'
        opened=business_content_opened(trace.read_text() if trace.exists() else '',business_root)
    output,blocked=redact(output,secret_root)
    return {'returncode':rc,'output':output,'sensitive_output_blocked':blocked,
            'business_content_opened':opened}


def main():
    command=base64.b64decode(sys.argv[1]).decode()
    print(json.dumps(run_guarded(command,audit='--audit-business' in sys.argv[2:])))


if __name__ == '__main__':
    main()
