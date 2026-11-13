from scripts.benchlib.completeness import inspect_outputs
from scripts.benchlib.documents import write_document


def test_missing_file_is_not_complete(tmp_path):
    (tmp_path/'files').mkdir()
    assert not inspect_outputs({'id':'BENCH-03','expected_extensions':['.xlsx']},tmp_path)['passed']


def test_untranslated_bottom_rows_are_detected(tmp_path):
    files=tmp_path/'files';files.mkdir()
    write_document(files/'translated.xlsx',{'sheets':{'Data':[['Переведено'] for _ in range(120)]+[['工序：包装检查']]}})
    result=inspect_outputs({'instruction':'Translate into Russian Excel','expected_extensions':['.xlsx']},tmp_path)
    assert not result['passed'] and result['issues'][0]['cells'][0]['cell']=='A121'
