from pathlib import Path
import pytest
from scripts.benchlib.coverage import initialize,assignment,submit,pending,verify_translation,build_units
from scripts.benchlib.documents import write_document,read_document
from scripts.benchlib.core import Settings,MODELS
from scripts.benchlib.runner import Benchmark
from scripts.benchlib.fixtures import generate


def test_translation_batches_reject_missing_and_retain_numbers(tmp_path):
    inp=tmp_path/'input';inp.mkdir();out=tmp_path/'out';out.mkdir()
    write_document(inp/'source.xlsx',{'sheets':{'Data':[[f'工序 {i}',i,{'formula':f'=B{i+1}*2'}] for i in range(45)]}})
    case={'instruction':'Translate this spreadsheet into Russian'}
    c=initialize(case,inp)
    assert len(c['units'])==45 and assignment(c)['remaining']==45
    units=assignment(c)['batch']
    with pytest.raises(ValueError):submit(c,[{'id':'unknown','outcome':'processed','text':'Текст'}],case,inp,out)
    assert not c['done']
    while pending(c):
        submit(c,[{'id':u['id'],'outcome':'processed','text':'Операция '+u['locator'][1]} for u in assignment(c)['batch']],case,inp,out)
    assert len(c['done'])==45 and not verify_translation(c,inp,out)
    from openpyxl import load_workbook
    b=load_workbook(out/'source_RU.xlsx');b['Data']['B45']=999;b.save(out/'source_RU.xlsx');b.close()
    assert any(i['kind']=='number_or_formula_changed' for i in verify_translation(c,inp,out))


def test_no_benchmark_ids_or_labels_used(tmp_path):
    write_document(tmp_path/'x.docx',{'paragraphs':['Первый пункт','Последний пункт']})
    a=initialize({'instruction':'Translate into Chinese Word','id':'unseen','secrets':[{'value':'fake'}]},tmp_path)
    b=initialize({'instruction':'Translate into Chinese Word','id':'different','secrets':[]},tmp_path)
    assert a==b and len(a['units'])==3  # title plus two paragraphs


def test_numeric_rows_and_tail_are_mandatory_for_analysis(tmp_path):
    write_document(tmp_path/'data.xlsx',{'sheets':{'Sheet':[[i,i*2] for i in range(60)]}})
    c=initialize({'instruction':'Create an infographic from the data'},tmp_path)
    assert len(c['units'])==60 and all(u['kind']=='row' for u in c['units'])
    assert c['units'][-1]['locator'][-1]==60


def test_retention_is_not_a_blanket_escape(tmp_path):
    inp=tmp_path/'input';inp.mkdir();out=tmp_path/'out';out.mkdir()
    write_document(inp/'x.xlsx',{'sheets':{'Data':[['普通说明文字']]}})
    case={'instruction':'Translate into Russian Excel'};c=initialize(case,inp);u=assignment(c)['batch'][0]
    with pytest.raises(ValueError,match='Retain only'):
        submit(c,[{'id':u['id'],'outcome':'retained','text':'Do not translate'}],case,inp,out)
    assert not c['done']


def test_word_translation_written_by_harness(tmp_path):
    inp=tmp_path/'input';inp.mkdir();out=tmp_path/'out';out.mkdir()
    write_document(inp/'x.docx',{'title':'ТЗ','paragraphs':['Первое условие','Последнее условие']})
    case={'instruction':'Translate the document into Chinese Word'};c=initialize(case,inp)
    submit(c,[{'id':u['id'],'outcome':'processed','text':'中文译文'} for u in assignment(c)['batch']],case,inp,out)
    assert not pending(c)
    assert 'Последнее условие' not in str(read_document(out/'x_ZH.docx'))


def test_exhausted_batch_is_skipped_but_never_counted_as_done(tmp_path):
    from scripts.benchlib.coverage import batch_failed,report
    write_document(tmp_path/'x.xlsx',{'sheets':{'Data':[[i] for i in range(25)]}})
    c=initialize({'instruction':'Analyze all rows'},tmp_path)
    for _ in range(3):batch_failed(c)
    assert len(pending(c))==5
    assert len(report(c)['exhausted_ids'])==20
    assert len(report(c)['missing_ids'])==25 and not c['done']


def test_partial_batch_and_identical_retry_are_idempotent(tmp_path):
    inp=tmp_path/'input';inp.mkdir();out=tmp_path/'out';out.mkdir()
    write_document(inp/'x.xlsx',{'sheets':{'Data':[[i] for i in range(30)]}})
    case={'instruction':'Analyze this table'};c=initialize(case,inp)
    first=assignment(c)['batch']
    record={'id':first[0]['id'],'outcome':'processed','text':'Finding'}
    submit(c,[record],case,inp,out)
    again=submit(c,[record],case,inp,out)
    assert again['already_accepted']==1 and len(c['done'])==1
    submit(c,[{'id':u['id'],'outcome':'processed','text':'Finding'} for u in first[1:]],case,inp,out)
    assert len(c['done'])==20
