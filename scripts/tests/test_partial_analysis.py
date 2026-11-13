from pathlib import Path
import hashlib
from scripts.benchlib.core import write_json,digest,inventory
from scripts.benchlib.partial_analysis import freeze,dataset


def fixture(root):
    case=root/'bemnchmark/BENCH-99';inp=case/'input';inp.mkdir(parents=True)
    (inp/'source.txt').write_text('Source')
    manifest={'id':'BENCH-99','instruction':'Analyze','input_hashes':inventory(inp)}
    write_json(case/'case.json',manifest)
    run=case/'output/model/local/run1';(run/'files').mkdir(parents=True)
    (run/'files/result.txt').write_text('Result')
    (run/'events.jsonl').write_text('')
    write_json(run/'result.json',{'batch_id':'partial','case_id':'BENCH-99','model':'test/model','mode':'local',
       'run_id':'run1','repetition':0,'status':'completed','duration_s':1,'final':'done',
       'case_hash':digest(manifest),'input_hashes':inventory(inp),'trace_digest':digest('')})
    return run


def test_snapshot_does_not_follow_live_changes(tmp_path):
    live=fixture(tmp_path);snap=freeze(tmp_path,'partial')
    frame,_,_=dataset(snap)
    assert len(frame)==1 and frame.quality.isna().all()
    (live/'files/result.txt').write_text('Later operation')
    frozen=snap/live.relative_to(tmp_path)/'files/result.txt'
    assert frozen.read_text()=='Result'
    assert not (snap/'.env').exists()


def test_inflight_run_is_not_snapshotted(tmp_path):
    live=fixture(tmp_path)
    (live/'events.jsonl').write_text('in progress')
    snap=freeze(tmp_path,'partial')
    from scripts.benchlib.core import read_json
    meta=read_json(snap/'snapshot.json')
    assert not meta['runs'] and meta['skipped']


def test_partial_notebook_is_valid_and_safe_by_default():
    import nbformat
    from IPython.core.interactiveshell import InteractiveShell
    root=Path(__file__).resolve().parents[2]
    nb=nbformat.read(root/'03_partial_full008_analysis.ipynb',as_version=4)
    nbformat.validate(nb)
    assert any('RUN_MISSING_JUDGE = False' in c.source for c in nb.cells)
    for c in nb.cells:
        if c.cell_type=='code':compile(InteractiveShell.instance().transform_cell(c.source),'<partial-notebook>','exec')
