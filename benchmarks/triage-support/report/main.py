"""Produce a small deterministic before/after report."""

import json, sys

data = json.load(sys.stdin)
keys=('tests','passed','failures','errors')
print(json.dumps({'initial':{key:data['initial'][key] for key in keys},'final':{key:data['final'][key] for key in keys},'changed_files':data['patch']['changed_files'],'repair_applied':data['patch']['applied'],'status':'passed' if data['final']['ok'] else 'failed'}))
