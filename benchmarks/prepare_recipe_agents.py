"""Prepare a limited rerun without changing the original frozen benchmark inputs."""

import hashlib, json, subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[1]
old=root/'benchmarks/agent-results/2026-10-16'
out=root/'benchmarks/agent-results/2026-10-16-recipes'
out.mkdir(exist_ok=True)
inputs=json.loads((old/'inputs.json').read_text())
frozen=Path(inputs['root'])
for path,digest in inputs['sha256'].items():
    assert hashlib.sha256((frozen / path).read_bytes()).hexdigest() == digest, path
for name in ('inputs.json','expected.json'):
    (out / name).write_bytes((old / name).read_bytes())
result=subprocess.run(['python3',str(root/'skills/jumpai/scripts/jumpai.py'),'--home',str(root/'.jumpai/agent-benchmark'),'run','--target','repo.health@1.1.0','--cwd',str(frozen),'--input',json.dumps(inputs['workflow_input']),'--save-recipe','repository-health'],capture_output=True,text=True,check=True)
card=json.loads(result.stdout)['recipe']
(out/'recipe-card.json').write_text(json.dumps(card,indent=2)+'\n')
for condition,source in [('reread_chain','jumpai_chained'),('recipe_chain','jumpai_chained'),('ordinary_batch','ordinary_chained')]:
    for i in (1,2):
        name = f'{condition}_{i}'
        prompt = (old / f'{source}_1.prompt.txt').read_text().replace(f'scratch/{source}_1', f'scratch/{name}')
        if condition=='reread_chain':
            prompt=prompt.replace('Read the entry skill at '+str(root/'skills/jumpai/SKILL.md'), 'Read the entry skill at '+str(frozen/'skills/jumpai/SKILL.md'))
        if condition=='recipe_chain':
            prompt=prompt.split('Use the prepared JumpAI catalog')[0]+('Use this prepared JumpAI recipe for the computations. It chains all three checks and combines their outputs, with arguments already saved for these exact input paths. Execute this command once; it returns an envelope whose output is the requested report:\n'+card['command']+'\nThis self-contained recipe card is sufficient: do not read a skill or contract or perform a lookup. Do not run component programs separately or write replacement computation code.\n')
        (out / f'{name}.prompt.txt').write_text(prompt)
print(json.dumps(card))
