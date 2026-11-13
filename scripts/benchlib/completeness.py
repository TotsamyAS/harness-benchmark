"""Local completion diagnostics, not a replacement for semantic evaluation."""
import re
from .core import inventory


def inspect_outputs(case, folder):
    files=folder/'files'
    available=inventory(files)
    missing=[ext for ext in case['expected_extensions'] if not any(p.endswith(ext) for p in available)]
    issues=[]
    if missing:
        issues.append({'kind':'missing_expected_file','extensions':missing})
    from .coverage import infer_profile
    profile=infer_profile(case.get('instruction',''),files)
    if profile.get('assemble_spreadsheet_translation'):
        from openpyxl import load_workbook
        for p in files.rglob('*.xlsx'):
            book=load_workbook(p,read_only=True,data_only=False)
            cells=[{'sheet':s.title,'cell':c.coordinate,'text':c.value}
                   for s in book for row in s for c in row
                   if isinstance(c.value,str) and re.search(r'[\u4e00-\u9fff]',c.value)]
            book.close()
            if cells:issues.append({'kind':'remaining_chinese_requires_review','file':p.name,'count':len(cells),'cells':cells})
    return {'passed':not issues,'issues':issues,
            'limitation':'Chinese names can be legitimate; remaining script is a review flag, not proof every flagged cell is wrong.'}
