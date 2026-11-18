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


def main():
    command = base64.b64decode(sys.argv[1]).decode()
    try:
        result = subprocess.run(['timeout', '--kill-after=5', '145', 'bash', '-lc', command], capture_output=True, timeout=155)
        output = (result.stdout + result.stderr).decode(errors='replace')
        rc = result.returncode
    except subprocess.TimeoutExpired:
        output, rc = 'Guest command timed out', 124
    output, blocked = redact(output, '/root/.config/assessment')
    print(json.dumps({'returncode': rc, 'output': output, 'sensitive_output_blocked': blocked}))


if __name__ == '__main__':
    main()
