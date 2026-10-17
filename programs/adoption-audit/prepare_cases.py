"""Small native-log fixture for descendant inclusion and unrelated-agent exclusion."""

import json
from pathlib import Path
import tempfile

root=Path(tempfile.mkdtemp(prefix='jumpai-adoption-audit-fixture-')).resolve()
logs = root / 'logs'
logs.mkdir()
empty = root / 'empty'
empty.mkdir()
for identifier,parent,path,total in [('parent','controller','/root/auto',10),('child','parent','/root/auto/diagnosis',6),('unrelated','other','/root/auto',100)]:
    usage={'input_tokens':total-2,'cached_input_tokens':2,'output_tokens':2,'reasoning_output_tokens':0,'total_tokens':total}
    rows=[
        {'type':'session_meta','payload':{'id':identifier,'source':{'subagent':{'thread_spawn':{'parent_thread_id':parent,'agent_path':path}}}}},
        {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'<!-- jumpai:begin --> startup instruction'}]}},
        {'type':'response_item','payload':{'type':'reasoning','summary':['PRIVATE REASONING MUST NOT EXPORT']}},
        {'type':'event_msg','timestamp':'2026-10-16T00:00:00Z','payload':{'type':'task_started'}},
        {'type':'response_item','timestamp':'2026-10-16T00:00:01Z','payload':{'type':'function_call','name':'exec','call_id':'call','arguments':'python3 jumpai.py run --target sample@1.0.0'}},
        {'type':'event_msg','timestamp':'2026-10-16T00:00:02Z','payload':{'type':'token_count','info':{'total_token_usage':usage,'last_token_usage':usage}}},
        {'type':'event_msg','timestamp':'2026-10-16T00:00:03Z','payload':{'type':'task_complete'}},
    ]
    (logs/f'{identifier}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
cases = []
for suffix,directory,ready,agents,tokens,exchanges in [('tree',logs,True,2,16,2),('empty',empty,False,0,0,0)]:
    output = root / suffix
    cases.append({'input':{'output_dir':str(output),'sessions_dir':str(directory),'parent_thread_id':'controller','root_agent_paths':['/root/auto']},'output':{'ready':ready,'agents':agents,'evidence':str(output/'adoption-evidence.json'),'totals_including_descendants':{'/root/auto':{'agents':agents,'total_tokens':tokens,'model_exchanges':exchanges}},'note':'Invocation candidates require execution-trace review; absence of instruction text in logs does not prove absence from model context.'}})
Path(__file__).with_name('cases.json').write_text(json.dumps(cases,indent=2)+'\n')
print(root)
