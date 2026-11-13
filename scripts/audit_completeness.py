from pathlib import Path
from scripts.benchlib.core import read_json,write_json
from scripts.benchlib.completeness import inspect_outputs
from scripts.benchlib.runner import batch_runs

def audit(root,batch):
    rows=[]
    for folder in batch_runs(root,batch):
        r=read_json(folder/'result.json')
        case=read_json(root/'bemnchmark'/r['case_id']/'case.json')
        rows.append({'run_id':r['run_id'],'case':r['case_id'],'model':r['model'],'mode':r['mode'],
                     'status':r['status'],**inspect_outputs(case,folder)})
    write_json(root/'bemnchmark/_reports'/batch/'completeness_audit.json',rows)
    print('Checked:',len(rows),'flagged:',sum(not r['passed'] for r in rows))

if __name__=='__main__':
    import sys
    audit(Path(__file__).resolve().parents[1],sys.argv[1])
