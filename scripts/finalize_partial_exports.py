from pathlib import Path
import nbformat
import pandas as pd
from scripts.benchlib.core import read_json
from scripts.benchlib.partial_analysis import publish_exports


def finalize(root,snapshot):
    out=snapshot/'exports';frame=pd.read_csv(out/'runs.csv');meta=read_json(snapshot/'snapshot.json')
    lines=['# Предварительные результаты '+meta['batch'],'',
           'Срез: '+meta['cutoff_utc']+'.',
           f'Сохранено сочетаний: {len(frame)}. Валидных оценок судьи: {int(frame.judged.sum())}.','',
           'Оценки отсутствуют, если судья не запускался или результат устарел. Они не заменяются нулями.',
           'Время включает кэш и ожидания. Оценка расходов не равна списаниям и не включает судью.','',
           '| Модель | Результатов |','|---|---:|']
    lines.extend(f'| {m} | {n} |' for m,n in frame.groupby('model').size().items())
    lines+=['','Сравнивать модели следует на пересечении заданий и режимов: paired_*.csv; для качества — jointly_scored.']
    (out/'README-results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    dest=publish_exports(snapshot,root)
    p=root/'03_partial_full008_analysis.ipynb';nb=nbformat.read(p,as_version=4)
    if len(nb.cells)>1 and nb.cells[1].cell_type=='markdown':del nb.cells[1]
    preview='## Уже экспортированный срез\n\n'+f"{len(frame)} результатов; оценённых судьёй: {int(frame.judged.sum())}. "
    preview+='Графики ниже относятся к срезу '+meta['cutoff_utc']+'. Код notebook позволяет создать новый независимый срез.\n\n'
    for name in ['all_duration_s.png','all_live_inference_s.png','tool_matrix.png','timeline_google__gemma-4-26b-a4b-it.png','status_matrix.png']:
        preview+='!['+name+']('+(dest/name).relative_to(root).as_posix()+')\n\n'
    nb.cells.insert(1,nbformat.v4.new_markdown_cell(preview));nbformat.validate(nb);nbformat.write(nb,p)
    print(dest)


if __name__=='__main__':
    import sys
    finalize(Path(__file__).resolve().parents[1],Path(sys.argv[1]).resolve())
