"""Extract failing test bodies and source callees; no diagnosis or repair rules."""

import ast, hashlib, json, sys
from pathlib import Path

data = json.load(sys.stdin)
root = Path(data['root']).resolve()
path = (root/data['path']).resolve()
if not path.is_relative_to(root):
    raise ValueError('source path escapes workspace')
source = path.read_text()
functions = {node.name: node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
names = set()
tests = []
for failure in data['checks']['evidence']:
    module, _, test_name = failure['test'].split('.')[-3:]
    test_path = root/'tests'/f'{module}.py'
    test_source = test_path.read_text()
    for node in ast.walk(ast.parse(test_source)):
        if isinstance(node,ast.FunctionDef) and node.name == test_name:
            tests.append({'test':failure['test'],'source':ast.get_source_segment(test_source,node)})
            names.update((n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))))
selected = {name for name in names if name in functions}
# One level of source dependencies helps expose shared utilities without a full file read.
for name in list(selected):
    selected.update((n.func.id for n in ast.walk(functions[name]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and (n.func.id in functions)))
snippets = [{'symbol':name,'line':functions[name].lineno,'source':ast.get_source_segment(source,functions[name])} for name in sorted(selected,key=lambda n:functions[n].lineno)]
evidence={'path':data['path'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'failures':data['checks']['evidence'],'test_source':tests,'source':snippets}
artifact=root/'.triage-evidence.json'
artifact.write_text(json.dumps(evidence,indent=2)+'\n')
# The full record stays in the workspace; checkpoint context has a strict byte budget.
compact={'path':evidence['path'],'sha256':evidence['sha256'],'evidence_ref':str(artifact),
         'failures':[{'test':x['test'],'traceback':x['traceback'][-800:]} for x in evidence['failures'][:1]],
         'test_source':[{'test':x['test'],'source':x['source'][:900]} for x in tests[:1]],
         'source':[dict(x,source=x['source'][:450]) for x in snippets[:4]]}
while len(json.dumps(compact).encode())>3700 and compact['source']:
    compact['source'].pop()
print(json.dumps(compact))
