"""Apply one model-selected exact replacement with scope and content guards."""

import hashlib, json, sys
from pathlib import Path

data = json.load(sys.stdin)
root = Path(data['root']).resolve()
patch = data['patch']
relative = Path(patch['path'])
if relative.is_absolute() or '..' in relative.parts or patch['path'] != data['allowed_path']:
    raise ValueError('replacement must target the allowed relative file')
path = (root / relative).resolve()
if not path.is_relative_to(root):
    raise ValueError('replacement escapes workspace')
original = path.read_bytes()
if hashlib.sha256(original).hexdigest() != data['expected_sha256']:
    raise ValueError('source changed after evidence was collected')
text = original.decode()
if not patch['old'] or text.count(patch['old']) != 1 or patch['old'] == patch['new']:
    raise ValueError('replacement must match exactly once and change the file')
path.write_text(text.replace(patch['old'],patch['new'],1))
print(json.dumps({'applied':True,'changed_files':[patch['path']]}))
