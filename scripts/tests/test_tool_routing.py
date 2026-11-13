from scripts.benchlib.fixtures import generate
from scripts.benchlib.core import Settings,MODELS
from scripts.benchlib.runner import Benchmark


def test_word_translation_exposes_only_id_based_write_protocol(tmp_path):
    generate(tmp_path);seen=[]
    def fake(payload):
        seen.extend(t['function']['name'] for t in payload['tools'])
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'Unable to finish'}}]}
    result=Benchmark(Settings(tmp_path,live=True),fake).run('BENCH-02',MODELS[0],'local','routing-test')
    assert 'submit_work' in seen and 'translate_docx' not in seen and 'create_word' not in seen
    assert result['status']=='needs_review'
