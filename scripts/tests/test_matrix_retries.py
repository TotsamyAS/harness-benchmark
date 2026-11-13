from scripts.benchlib.core import Settings, MODELS
from scripts.benchlib.fixtures import generate
from scripts.benchlib.runner import Benchmark


def test_three_failed_attempts_do_not_stop_next_mode(tmp_path, monkeypatch):
    generate(tmp_path)
    folder = tmp_path/'failed'
    folder.mkdir()
    (folder/'events.jsonl').write_text('')
    calls=[]
    bench=Benchmark(Settings(tmp_path))
    def fake(case,model,mode,batch,repetition):
        calls.append(mode)
        return {'status':'error' if mode=='local' else 'completed','folder':'failed'}
    monkeypatch.setattr(bench,'run',fake)
    result=bench.run_matrix('retry-test',['BENCH-01'],MODELS[:1],['local','protected'])
    assert calls==['local','local','local','protected']
    assert len(result)==2
