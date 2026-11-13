"""Count recorded observable errors without executing any model."""
from collections import Counter,defaultdict
from pathlib import Path
import csv
from scripts.benchlib.core import read_json,write_json
from scripts.benchlib.runner import events

RULES=[
 ('paths',['Use input/','File not found or invalid scope']),
 ('coverage_ids',['Unknown, already processed or out-of-batch','never-issued ID']),
 ('tool_loop',['Repeated identical tool']),('pending_marker',['Previous send']),
 ('uncertain_delivery',['may be billed']),('empty_response',['Empty assistant']),
 ('context_guard',['context bound']),('sheets',['Worksheet','sheet']),
 ('json_encoding',['Expecting','Extra data','Unterminated string']),
 ('batch_size',['greater than the maximum']),('connection',['APIConnectionError']),
 ('timeout',['APITimeoutError']),('retention',['Retain only','Retained sentences']),
 ('schema',['Invalid tool arguments']),('output_payload',['requires data.']),
 ('missing_file',['No such file or directory']),('manual_interrupt',['keyboard_interrupt'])]


def audit(root):
    counts=Counter();affected=defaultdict(set);by_batch=defaultdict(Counter);statuses=Counter();total=0
    for p in sorted((root/'bemnchmark').glob('BENCH-*/output/*/*/*/result.json')):
        r=read_json(p);statuses[r['status']]+=1;total+=1
        for e in events(p.parent):
            if e['event'] not in {'tool_error','tool_argument_error','run_error','run_paused','llm_error'}:continue
            msg=str(e.get('result',{}).get('message') or e.get('message') or e.get('reason') or e.get('error_type') or '')
            category=next((label for label,patterns in RULES if any(s in msg for s in patterns)),'other')
            counts[category]+=1;affected[category].add(r['run_id']);by_batch[r['batch_id']][category]+=1
    rows=[{'category':k,'events':n,'affected_run_ids':len(affected[k])} for k,n in counts.most_common()]
    folder=root/'docs'
    write_json(folder/'error-frequency.json',{'run_records':total,'statuses':dict(statuses),'categories':rows,
               'by_batch':{k:dict(v) for k,v in by_batch.items()},
               'caveat':'Mixed historical code versions. Counts include resumed sessions; terminal errors and network errors can refer to the same incident. Not an independent failure probability.'})
    with (folder/'error-frequency.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['category','events','affected_run_ids']);writer.writeheader();writer.writerows(rows)
    from scripts.benchlib.runner import batch_runs
    paths=list((root/'bemnchmark').glob('BENCH-*/output/*/*/*/result.json'))
    if paths:
        latest=max(paths,key=lambda p:p.stat().st_mtime)
        batch=read_json(latest)['batch_id'];fresh=Counter();fresh_runs=defaultdict(set)
        for run in batch_runs(root,batch):
            history=events(run)
            session=history[-1].get('trace_session_id') if history else None
            for e in history:
                if e.get('trace_session_id')!=session or e['event'] not in {'tool_error','tool_argument_error','run_error','run_paused','llm_error'}:continue
                msg=str(e.get('result',{}).get('message') or e.get('message') or e.get('reason') or e.get('error_type') or '')
                category=next((label for label,patterns in RULES if any(s in msg for s in patterns)),'other')
                fresh[category]+=1;fresh_runs[category].add(run.name)
        write_json(folder/'error-frequency-latest.json',{'batch':batch,'scope':'Latest attempt per slot, latest trace session only',
                   'categories':[{'category':k,'events':v,'affected_runs':len(fresh_runs[k])} for k,v in fresh.most_common()],
                   'caveat':'Latest recorded execution, not proof of current edited code behavior. No new model calls.'})
    print('Audited saved runs:',total)

if __name__=='__main__':audit(Path(__file__).resolve().parents[1])
