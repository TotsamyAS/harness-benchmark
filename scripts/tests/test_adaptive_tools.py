from scripts.benchlib.core import Trace
from scripts.benchlib.tools import TOOLS, FileTools
from scripts.benchlib.documents import read_document


def test_native_schemas_and_chunked_excel(tmp_path):
    for t in TOOLS:
        assert not any(k.endswith('_json') for k in t['function']['parameters']['properties'])
    inp = tmp_path / 'input'
    inp.mkdir()
    tools = FileTools(inp, tmp_path/'files', tmp_path/'cache', Trace(tmp_path/'trace', {}))
    for start, rows in [(1, [['Name','Value']]), (2, [['Первая',10],['Вторая',20]])]:
        assert 'error' not in tools.execute('write_rows', {'path':'output/test.xlsx','sheet':'Data','start_row':start,'rows':rows})
    result=tools.execute('read_sheet', {'path':'output/test.xlsx','sheet':'Data','start_row':2,'count':1})
    assert result['rows'][0] == ('Первая',10) and result['next_row']==3
    assert 'error' not in tools.execute('edit_cells', {'source':'output/test.xlsx','target':'output/test.xlsx','changes':[{'sheet':'Data','cell':'A2','value':'Обновлено'}]})
    assert 'Обновлено' in str(read_document(tmp_path/'files/test.xlsx'))


def test_single_sheet_edit_without_sheet_name(tmp_path):
    from scripts.benchlib.documents import write_document
    inp=tmp_path/'input';inp.mkdir()
    write_document(inp/'a.xlsx', {'sheets': {'Данные': [['Original']]}})
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    result=tools.execute('edit_cells',{'source':'input/a.xlsx','target':'output/a.xlsx',
                                     'changes':[{'cell':'A1','value':'Переведено'}]})
    assert 'error' not in result
    assert 'Переведено' in str(read_document(tmp_path/'files/a.xlsx'))


def test_text_chunks(tmp_path):
    inp=tmp_path/'input';inp.mkdir()
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    for text,append in [('<html>',False),('текст</html>',True)]:
        assert 'error' not in tools.execute('append_text',{'path':'output/a.html','text':text,'append':append})
    assert (tmp_path/'files/a.html').read_text(encoding='utf-8')=='<html>текст</html>'


def test_nested_contracts_reject_missing_fields():
    import jsonschema
    schemas={t['function']['name']:t['function']['parameters'] for t in TOOLS}
    import pytest
    for name,args in [
        ('edit_cells',{'source':'input/a.xlsx','target':'output/a.xlsx','changes':[{'cell':'A1'}]}),
        ('revise_contract',{'source':'input/a.docx','target':'output/a.docx','changes':[{'old':'x'}]}),
        ('translate_docx',{'source':'input/a.docx','target':'output/a.docx','replacements':{'x':12}}),
        ('write_file',{'path':'output/a.pptx','data':{'slides':[{'bullets':['x']}]}}),
    ]:
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(args,schemas[name])


def test_discovery_and_multisheet_recovery(tmp_path):
    from scripts.benchlib.documents import write_document
    inp=tmp_path/'input';inp.mkdir()
    write_document(inp/'a.xlsx',{'sheets':{'First':[['x']],'Second':[['y']]}})
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    assert tools.execute('list_files',{})['excel_sheets']['input/a.xlsx']==['First','Second']
    error=tools.execute('edit_cells',{'source':'input/a.xlsx','target':'output/a.xlsx','changes':[{'cell':'A1','value':'z'}]})
    assert 'Available sheets' in error['message']
    assert not (tmp_path/'files/a.xlsx').exists()
    assert 'error' not in tools.execute('edit_cells',{'source':'input/a.xlsx','target':'output/a.xlsx','changes':[{'sheet':'Second','cell':'A1','value':'z'}]})


def test_word_pptx_native_objects_and_missing_format_payload(tmp_path):
    inp=tmp_path/'input';inp.mkdir()
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    for path,data in [('a.docx',{'title':'Заголовок','paragraphs':['Текст']}),('a.pptx',{'slides':[{'title':'Заголовок','bullets':['Текст']}]})]:
        assert 'error' not in tools.execute('write_file',{'path':'output/'+path,'data':data})
        assert 'Текст' in str(read_document(tmp_path/'files'/path))
    error=tools.execute('write_file',{'path':'output/a.xlsx','data':{'text':'not a table'}})
    assert 'data.sheets' in error['message']


def test_unambiguous_bare_filename_and_ambiguity(tmp_path):
    inp=tmp_path/'input';inp.mkdir();(inp/'meeting.txt').write_text('source')
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    assert 'error' not in tools.execute('read_file',{'path':'meeting.txt'})
    (tmp_path/'files/meeting.txt').write_text('result')
    assert 'Ambiguous' in tools.execute('read_file',{'path':'meeting.txt'})['message']


def test_bare_output_filename_is_safely_normalized(tmp_path):
    inp=tmp_path/'input';inp.mkdir()
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    assert 'error' not in tools.execute('write_file',{'path':'report.txt','data':{'text':'result'}})
    assert (tmp_path/'files/report.txt').read_text()=='result'
    assert 'error' in tools.execute('write_file',{'path':'../escape.txt','data':{'text':'bad'}})


def test_oversized_read_is_paginated_not_rejected(tmp_path):
    import jsonschema
    from scripts.benchlib.documents import write_document
    inp=tmp_path/'input';inp.mkdir()
    write_document(inp/'x.xlsx',{'sheets':{'Data':[[i] for i in range(120)]}})
    args={'path':'x.xlsx','sheet':'Data','start_row':1,'count':100}
    tool=next(t for t in TOOLS if t['function']['name']=='read_sheet')
    jsonschema.validate(args,tool['function']['parameters'])
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    result=tools.execute('read_sheet',args)
    assert len(result['rows'])==50 and result['next_row']==51
    assert result['requested_count']==100 and result['effective_count']==50


def test_specialized_creation_tools(tmp_path):
    inp=tmp_path/'input';inp.mkdir()
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    for name,args in [
        ('write_text',{'path':'report.txt','text':'Отчёт'}),
        ('create_word',{'path':'report.docx','title':'Отчёт','paragraphs':['Результат']}),
        ('create_presentation',{'path':'report.pptx','slides':[{'title':'Отчёт','bullets':['Результат']}]}),
    ]:
        assert 'error' not in tools.execute(name,args)
        assert (tmp_path/'files'/args['path']).exists()
    assert 'error' in tools.execute('write_text',{'path':'bad.pptx','text':'wrong format'})
