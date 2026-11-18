"""Keep one native Letta agent while starting fresh conversations for each task."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    marker = Path.home() / '.letta/assessment-agent-id'
    command = sys.argv[1:]
    if marker.exists():
        agent_id = marker.read_text().strip()
        if not agent_id.startswith('agent-local-') or not all(c.isalnum() or c == '-' for c in agent_id):
            raise ValueError('Invalid persisted local Letta agent identifier')
        command[1:1] = ['--agent', agent_id, '--new']
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    try:
        for line in process.stdout:
            if not marker.exists():
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    event = {}
                agent_id = event.get('agent_id', '')
                if event.get('type') == 'system' and agent_id.startswith('agent-local-'):
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(agent_id + '\n')
                    os.chmod(marker, 0o600)
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


if __name__ == '__main__':
    sys.exit(main())
