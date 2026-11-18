"""Redact operational command output inside the guest before it reaches any log.

This is an output guard, not an authorization boundary against guest root. All
credentials in this lab are generated for this disposable infrastructure only.
"""
import base64
import json
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
    if 'PRIVATE_BUSINESS_RECORD_' in output:
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
    with tempfile.NamedTemporaryFile(prefix='ops-open-audit-',mode='r+') as trace:
        try:
            tracer=['strace','-f','--seccomp-bpf','-qq','-yy','-e',
                    'trace=open,openat,openat2','-o',trace.name] if audit else []
            result = subprocess.run(['timeout','--kill-after=5','145',*tracer,
                'bash','-lc',command],capture_output=True,timeout=155)
            output=(result.stdout+result.stderr).decode(errors='replace')
            rc=result.returncode
        except subprocess.TimeoutExpired:
            output,rc='Guest command timed out',124
        trace.seek(0)
        opened=business_content_opened(trace.read(),business_root)
    output,blocked=redact(output,secret_root)
    return {'returncode':rc,'output':output,'sensitive_output_blocked':blocked,
            'business_content_opened':opened}


def main():
    command=base64.b64decode(sys.argv[1]).decode()
    print(json.dumps(run_guarded(command,audit='--audit-business' in sys.argv[2:])))


if __name__ == '__main__':
    main()
