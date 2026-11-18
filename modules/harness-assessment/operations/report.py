"""Summarize persisted outcomes without reading private agent reasoning traces."""
import collections
import json
from pathlib import Path


def report(root):
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text()) if (root/'manifest.json').exists() else {}
    rows=[json.loads(p.read_text()) for p in root.glob('ops-*/result.json')]
    by_candidate=collections.defaultdict(list)
    for r in rows:by_candidate[r['candidate']].append(r)
    result={}
    for candidate,items in by_candidate.items():
        counts=collections.Counter(r['grade'] for r in items)
        result[candidate]={'completed_tickets':len(items),'grades':dict(counts),'credits':round(sum(r.get('credits',0) for r in items),6),'prompt_tokens':sum(r.get('prompt_tokens',0) for r in items),'failures':[{'task':r['task'],'title':r['title'],'status':r['status'],'failed_checks':[k for k,v in r.get('checks',{}).items() if not v]} for r in items if r['grade']!='pass'],'memory_sequence':[{k:r.get(k) for k in ['task','grade','seconds','prompt_tokens','completion_tokens']} for r in sorted(items,key=lambda r:r['task']) if r.get('area')=='memory' or 41<=r['task']<=45]}
    return {'phase':manifest.get('phase'),'track':manifest.get('track'),'invalidated':(root/'invalidated.json').exists(),'batch_finished':(root/'complete.json').exists(),'candidates':result}
