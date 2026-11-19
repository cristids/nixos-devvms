"""Copy an inactive preparation home without following candidate-created links."""
import hashlib
import json
import os
from pathlib import Path
import stat


def snapshot(source, destination, candidate):
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / 'manifest.json').read_text())
    if manifest.get('phase') != 'native-profile-practice' or manifest.get('candidates') != [candidate]:
        raise ValueError('source must be preparation for the same candidate')
    process = json.loads((source / 'process.json').read_text())
    try:
        os.kill(process['pid'], 0)
    except ProcessLookupError:
        pass
    else:
        raise ValueError('source process still exists; snapshot only inactive runs')
    files, excluded = [], []
    # OMP 19's daemon runtime contains an IPC credential, socket and queued
    # process state. Recreate it in the new run; learned state lives in agent/.
    runtime_dirs = {'home-omp/.omp/run/daemon'} if candidate == 'omp' else set()
    for name in ('home-' + candidate, 'notes-' + candidate):
        root = source / name
        if root.is_symlink() or not root.is_dir():
            raise ValueError('missing or linked preparation directory')
        target = destination / name
        target.mkdir(mode=0o700)
        for current, dirs, names in os.walk(root, followlinks=False):
            for entry in sorted(dirs + names):
                src = Path(current) / entry
                rel = src.relative_to(source)
                dst = destination / rel
                mode = src.lstat().st_mode
                if str(rel) in runtime_dirs and stat.S_ISDIR(mode):
                    dirs.remove(entry)
                    excluded.append({'path': str(rel), 'reason': 'daemon runtime: IPC credential, socket and process state; recreated per run'})
                    continue
                if stat.S_ISDIR(mode):
                    dst.mkdir(mode=0o700)
                elif stat.S_ISREG(mode):
                    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, 'rb') as f:
                        data = f.read()
                    dst.write_bytes(data)
                    dst.chmod(0o700 if mode & 0o111 else 0o600)
                    files.append({'path': str(rel), 'sha256': hashlib.sha256(data).hexdigest()})
                else:
                    raise ValueError('preparation contains link or special file: ' + str(rel))
    record = {'source': str(source.resolve()), 'candidate': candidate,
              'source_manifest_sha256': hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest(),
              'files': files, 'excluded': excluded}
    (destination / 'prepared-snapshot.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
