import pytest
from scripts.benchlib.documents import write_document,revise_contract,read_document
from scripts.benchlib.core import Trace
from scripts.benchlib.tools import FileTools


def test_word_missing_replacement_is_not_success(tmp_path):
    inp=tmp_path/'input';inp.mkdir()
    write_document(inp/'x.docx',{'paragraphs':['Existing paragraph']})
    tools=FileTools(inp,tmp_path/'files',tmp_path/'cache',Trace(tmp_path/'trace',{}))
    r=tools.execute('translate_docx',{'source':'input/x.docx','target':'output/x.docx','replacements':{'Missing paragraph':'Translation'}})
    assert 'No exact' in r['message'] and not (tmp_path/'files/x.docx').exists()


def test_two_edits_of_same_paragraph_do_not_erase_revisions(tmp_path):
    source=tmp_path/'x.docx';target=tmp_path/'out.docx'
    write_document(source,{'paragraphs':['Advance 80%, liability 2%.']})
    changes=[{'old':'80%','new':'30%','reason':'policy','policy_ref':'1'},
             {'old':'2%','new':'10%','reason':'policy','policy_ref':'2'}]
    with pytest.raises(ValueError,match='combined replacement'):
        revise_contract(source,target,changes)
    assert not target.exists()


def test_revision_in_table_cell(tmp_path):
    source=tmp_path/'x.docx';target=tmp_path/'out.docx'
    write_document(source,{'tables':[[['Advance','80%']]]})
    revise_contract(source,target,[{'old':'80%','new':'30%','reason':'policy','policy_ref':'1'}])
    assert '30%' in str(read_document(target))


def test_excel_names_are_not_silently_truncated(tmp_path):
    target=tmp_path/'x.xlsx'
    with pytest.raises(ValueError,match='31'):
        write_document(target,{'sheets':{'A'*32:[['value']]}})
    assert not target.exists()
    with pytest.raises(ValueError,match='unique'):
        write_document(target,{'sheets':{'Data':[[1]],'data':[[2]]}})
