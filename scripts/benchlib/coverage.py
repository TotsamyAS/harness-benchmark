"""Harness-owned source inventory and durable processing receipts."""
from pathlib import Path
import shutil
from .core import digest, inventory, safe_path
from .documents import read_document

def tool(name,description,properties,required):
    return {'type':'function','function':{'name':name,'description':description,'parameters':{
        'type':'object','properties':properties,'required':required,'additionalProperties':False}}}

COVERAGE_TOOLS=[
    tool('next_work','Get the next mandatory source batch and coverage progress.',{},[]),
    tool('coverage_results','Read processed findings by index for final synthesis; paginate until next_offset is null.',
         {'offset':{'type':'integer','minimum':0}},['offset']),
    tool('submit_work','Submit processing receipts for current batch. text is the translation or a grounded finding/reason. For spreadsheet translation the harness writes translations into a copy of the source workbook.',
         {'records':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','properties':{
             'id':{'type':'string'},'outcome':{'type':'string','enum':['processed','retained','not_applicable']},
             'text':{'type':'string','minLength':1}},'required':['id','outcome','text'],'additionalProperties':False}}},['records'])]


def build_units(input_dir):
    units=[]
    def add(file,locator,text,kind):
        if str(text).strip():
            units.append({'id':digest([file,locator,str(text)])[:24], 'file':file, 'path':'input/'+file,
                          'locator':locator,'text':str(text),'kind':kind})
    for p in sorted(input_dir.rglob('*')):
        if not p.is_file():continue
        file=p.relative_to(input_dir).as_posix()
        if p.suffix=='.xlsx':
            from openpyxl import load_workbook
            b=load_workbook(p,read_only=True,data_only=False)
            for s in b:
                for row in s:
                    for c in row:
                        if isinstance(c.value,str) and c.data_type!='f':add(file,[s.title,c.coordinate],c.value,'cell')
            b.close()
        else:
            data=read_document(p)
            if p.suffix=='.docx':
                for i,t in enumerate(data['paragraphs_and_cells']):add(file,['paragraph',i],t,'paragraph')
            elif p.suffix=='.pdf':
                for page in data:
                    text=page['text']
                    for i in range(0,len(text),2500):add(file,['page',page['page'],i],text[i:i+2500],'page_fragment')
            elif p.suffix=='.pptx':
                import json
                for slide in data:add(file,['slide',slide['slide']],json.dumps(slide,ensure_ascii=False),'slide')
            else:
                text=str(data)
                for i in range(0,len(text),2000):add(file,['chars',i,min(i+2000,len(text))],text[i:i+2000],'text_fragment')
        # Numerical rows are units too, for non-translation aggregation tasks.
        if p.suffix=='.xlsx':
            from openpyxl import load_workbook
            b=load_workbook(p,read_only=True,data_only=False)
            for s in b:
                for i,row in enumerate(s.iter_rows(values_only=True),1):
                    if any(v is not None for v in row):add(file,[s.title,'row',i],repr(row),'row')
            b.close()
    return units


def infer_profile(instruction,input_dir):
    text=instruction.casefold()
    translation=any(w in text for w in ['translate','переведи','перевод'])
    spreadsheet=any(input_dir.rglob('*.xlsx'))
    russian=any(w in text for w in ['into russian','на русский','на русском'])
    chinese=any(w in text for w in ['into chinese','на китайский'])
    operation='analysis'
    for label,words in [('archive',['archive','архив']),('comparison',['compare','reconcile','свер','сравн']),
                        ('aggregation',['consolidate','normalize','свед','сопостав']),
                        ('revision',['revise','policy','правки','политик']),
                        ('report',['presentation','infographic','презентац','инфограф']),
                        ('action_plan',['action plan','поручен'])]:
        if any(w in text for w in words):operation=label;break
    return {'kind':'translation' if translation else 'source_analysis', 'operation':'translation' if translation else operation,
            'target_language':'ru' if russian and not chinese else 'zh' if chinese and not russian else None,
            'assemble_spreadsheet_translation':translation and spreadsheet and russian and not chinese,
            'assemble_word_translation':translation and any(input_dir.rglob('*.docx')) and (russian != chinese),
            'basis':'User instruction and actual file types only; no benchmark IDs or reference annotations'}


def initialize(case,input_dir):
    units=build_units(input_dir)
    profile=infer_profile(case['instruction'],input_dir)
    if profile['assemble_spreadsheet_translation']:units=[u for u in units if u['kind']=='cell']
    elif profile['kind']!='translation':units=[u for u in units if u['kind']!='cell']
    return {'units':units,'done':{},'failures':{},'version':1,'profile':profile}


def pending(coverage):
    return [u for u in coverage['units'] if u['id'] not in coverage['done'] and coverage.get('failures',{}).get(u['id'],0)<3]


def batch_failed(coverage):
    for u in pending(coverage)[:20]:
        coverage['failures'][u['id']]=coverage['failures'].get(u['id'],0)+1


def assignment(coverage,limit=20):
    todo=pending(coverage)
    issued=set(coverage.get('issued_ids',[]))
    issued.update(u['id'] for u in todo[:limit])
    coverage['issued_ids']=sorted(issued)
    return {'total':len(coverage['units']),'processed':len(coverage['done']),
            'remaining':len(todo),'batch':todo[:limit],
            'profile':coverage['profile'],
            'instruction':'Process every unit in this batch, then call submit_work with one record per ID. Read/view does not count as processing. For spreadsheet/Word translation supply translated text in text; the harness writes the document. Do NOT call translate_docx, edit_cells or regenerate the document yourself. Use retained only for proper names/IDs with a reason. For analysis give a source-grounded finding or an explicit not_applicable reason; cite generated output files when applicable. When no units remain, synthesize final deliverables using coverage_results and file tools.'}


def submit(coverage,records,case,input_dir,output_dir):
    batch={u['id']:u for u in coverage['units'] if u['id'] in set(coverage.get('issued_ids',[]))}
    if not records:raise ValueError('Empty batch is not processing')
    if len({r['id'] for r in records})!=len(records):raise ValueError('Duplicate work unit IDs')
    fresh=[]
    for r in records:
        previous=coverage['done'].get(r['id'])
        if previous is not None:
            if previous!=r:raise ValueError('Previously accepted ID has different content; do not silently overwrite it')
            continue
        fresh.append(r)
    if not fresh:
        return {'accepted':0,'already_accepted':len(records),'remaining':len(pending(coverage)),'next':assignment(coverage)}
    records=fresh
    for r in records:
        if r['id'] not in batch:raise ValueError('Unknown or never-issued ID; request next_work for valid IDs')
        if r['outcome'] not in {'processed','retained','not_applicable'}:raise ValueError('Invalid outcome')
        if not r['text'].strip():raise ValueError('A nonempty translation/finding/reason is required')
        if coverage['profile']['assemble_spreadsheet_translation'] and r['outcome']=='not_applicable':raise ValueError('Every text cell requires translation or justified retention')
        if coverage['profile']['kind']=='translation' and r['outcome']=='retained':
            from .privacy import ENTITY
            import re
            original=batch[r['id']]['text']
            if not ENTITY.fullmatch(original) and not re.fullmatch(r'[A-Z0-9][A-Z0-9_. /-]*',original):
                raise ValueError('Retain only recognized proper names or identifiers; translate ordinary text')
        if coverage['profile']['assemble_spreadsheet_translation'] and r['outcome']=='processed':
            import re
            if re.search(r'[\u4e00-\u9fff]',r['text']):raise ValueError('Translation still contains Chinese; use retained with a reason only for proper names/IDs')
    # Validate all entries before touching files or marking any unit done.
    if coverage['profile']['assemble_spreadsheet_translation']:
        from openpyxl import load_workbook
        grouped={}
        for r in records:grouped.setdefault(batch[r['id']]['file'],[]).append(r)
        for name,items in grouped.items():
            source=safe_path(input_dir,name)
            target=safe_path(output_dir,str(Path(name).with_name(Path(name).stem+'_RU.xlsx')))
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            book=load_workbook(target)
            for r in items:
                unit=batch[r['id']];sheet,cell=unit['locator']
                if r['outcome']=='processed':
                    text=r['text'];book[sheet][cell]="'"+text if text.startswith(('=','+','-','@')) else text
            book.save(target);book.close()
    if coverage['profile']['assemble_word_translation']:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph
        grouped={}
        for r in records:
            unit=batch[r['id']]
            if unit['kind']=='paragraph':grouped.setdefault(unit['file'],[]).append(r)
        for name,items in grouped.items():
            source=safe_path(input_dir,name);language=coverage['profile']['target_language'].upper()
            target=safe_path(output_dir,str(Path(name).with_name(Path(name).stem+'_'+language+'.docx')))
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            doc=Document(target);paragraphs=list(doc.element.body.iter(qn('w:p')))
            for r in items:
                if r['outcome']=='processed':Paragraph(paragraphs[batch[r['id']]['locator'][1]],doc).text=r['text']
            doc.save(target)
    for r in records:coverage['done'][r['id']]=r
    return {'accepted':len(records),'remaining':len(pending(coverage)), 'next':assignment(coverage)}


def report(coverage):
    return {'total':len(coverage['units']),'processed':len(coverage['done']),
            'missing_ids':[u['id'] for u in coverage['units'] if u['id'] not in coverage['done']],
            'exhausted_ids':[u['id'] for u in coverage['units'] if coverage.get('failures',{}).get(u['id'],0)>=3],
            'warning':'Processing receipts prove structural coverage, not semantic correctness. Retained/not_applicable decisions require judge/human review.'}


def verify_translation(coverage,input_dir,output_dir):
    issues=[]
    if not coverage['profile']['assemble_spreadsheet_translation']:return issues
    from openpyxl import load_workbook
    for source in sorted(input_dir.rglob('*.xlsx')):
        relative=source.relative_to(input_dir)
        target=output_dir/relative.with_name(relative.stem+'_RU.xlsx')
        if not target.exists():issues.append({'kind':'missing_translation_copy','file':str(relative)});continue
        original=load_workbook(source,read_only=True,data_only=False)
        translated=load_workbook(target,read_only=True,data_only=False)
        if original.sheetnames!=translated.sheetnames:issues.append({'kind':'sheet_structure_changed','file':str(relative)})
        for sheet in original:
            if sheet.title not in translated:continue
            for row in sheet:
                for c in row:
                    if (c.value is not None and (not isinstance(c.value,str) or c.data_type=='f')
                        and translated[sheet.title][c.coordinate].value!=c.value):
                        issues.append({'kind':'number_or_formula_changed','sheet':sheet.title,'cell':c.coordinate})
        for u in coverage['units']:
            if u['file']!=relative.as_posix() or u['id'] not in coverage['done']:continue
            r=coverage['done'][u['id']];sheet,cell=u['locator']
            if sheet not in translated:continue
            expected=r['text'] if r['outcome']=='processed' else u['text']
            if translated[sheet][cell].value!=expected:issues.append({'kind':'submitted_translation_not_in_output','sheet':sheet,'cell':cell})
        original.close();translated.close()
    return issues
