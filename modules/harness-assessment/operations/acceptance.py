"""Shell oracle policy: an earlier failed assertion must not be masked."""
def strict_command(command):
    return 'set -euo pipefail\n' + command
