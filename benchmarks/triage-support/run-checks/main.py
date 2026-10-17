"""Run selected unittest IDs in a child interpreter; preserve compact failures."""

import json, os, subprocess, sys
from pathlib import Path

data = json.load(sys.stdin)
root = Path(data['root']).resolve()
child = r'''
import io,json,sys,unittest
suite=unittest.defaultTestLoader.loadTestsFromNames(json.loads(sys.argv[1]))
result=unittest.TextTestRunner(stream=io.StringIO(),verbosity=0).run(suite)
failures=[{'test':test.id(),'traceback':trace[-4000:]} for test,trace in result.failures+result.errors]
print(json.dumps({'tests':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),'ok':result.wasSuccessful(),'evidence':failures}))
'''
env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root), str(root/'tests')]), PYTHONDONTWRITEBYTECODE='1')
result = subprocess.run([sys.executable, '-c', child, json.dumps(data['tests'])], cwd=root, env=env, capture_output=True, text=True, timeout=30)
if result.returncode:
    raise RuntimeError(result.stderr[-2000:])
print(json.dumps(json.loads(result.stdout)))
