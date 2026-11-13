from pathlib import Path
import nbformat as nbf

def generate(root):
    cells=[
      nbf.v4.new_markdown_cell('''# Неполный full-008: зафиксированный срез

Визуализация сохранённых результатов без повторного выполнения заданий. Новые операции основного прогона не включаются после создания snapshot. Отсутствующие оценки не заменяются нулями. Неподтверждённые оценки судьи разрешаются только флагом конфигурации.
Сравнения для двух и трёх моделей строятся по пересечению заданий **и режимов**. Графики jointly_scored дополнительно требуют оценок всех сравниваемых моделей. Общее сравнение всех доступных данных — описательное, а не честный рейтинг на одинаковой выборке.'''),
      nbf.v4.new_code_cell('%pip install -r requirements-lock.txt\n%pip install --no-deps -e .'),
      nbf.v4.new_code_cell('''from pathlib import Path
import sys
ROOT = Path.cwd().resolve()
assert Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(), "Выберите .venv"
from IPython.display import display, FileLink
from scripts.benchlib.partial_analysis import freeze, evaluate_missing, render, dataset, publish_exports
BATCH_ID = "full-008"
SNAPSHOT_PATH = None  # либо путь уже созданного среза для повторной визуализации
ALLOW_UNCONFIRMED_JUDGE = True  # предварительные оценки, без ручного подтверждения
RUN_MISSING_JUDGE = False  # платные вызовы, запускает только пользователь
'''),
      nbf.v4.new_code_cell('''snapshot = Path(SNAPSHOT_PATH).resolve() if SNAPSHOT_PATH else freeze(ROOT, BATCH_ID)
print("Зафиксированный срез:", snapshot)
from scripts.benchlib.core import read_json
display(read_json(snapshot / "snapshot.json"))
'''),
      nbf.v4.new_markdown_cell('''## Необязательная оценка недостающих результатов

RUN_MISSING_JUDGE=True вызывает только судью для отсутствующих/невалидных оценок в копии, не агента. Основной прогон и его оценки не изменяются. Ключ загружает runtime из корневого .env, значение не выводится. Судья и его расходы хранятся отдельно в срезе; совпадающие запросы используют общий API-кэш. Неопределённые сетевые отправки не повторяются молча.
Если ALLOW_UNCONFIRMED_JUDGE=False, требуется сохранённая подтверждённая проверка; иначе аналитика остановится. Для предварительного отчёта оставьте True.'''),
      nbf.v4.new_code_cell('''if RUN_MISSING_JUDGE:
    display(evaluate_missing(snapshot, ROOT))
else:
    print("Новых обращений к моделям нет; используются только сохранённые оценки.")
'''),
      nbf.v4.new_markdown_cell('''## Графики, сравнения и task mining

Цвет — модель, штриховка — режим. Формируются качество, покрытие, время, шаги, токены, ошибки, затраты, безопасный зачёт; сравнения общих сочетаний для каждой пары/тройки; карты качества и статусов; матрицы инструментов и временные карты событий. Время включает кэш и ожидание, поэтому не является чистой скоростью модели. Неизвестные расходы и оценки показаны отдельно в таблицах.
Судья не подтверждает визуальное качество; данные без человека предварительные. Частота безопасного зачёта считается только среди оценённых результатов. Для финального README используйте экспортированные таблицы и явно укажите объём общего покрытия.'''),
      nbf.v4.new_code_cell('''frame, summary, EXPORT_DIR = render(snapshot, ALLOW_UNCONFIRMED_JUDGE)
from scripts.render_process_maps import render as render_process_maps
render_process_maps(snapshot)
display(summary)
display(frame.groupby(["model", "mode"])[["judged", "reviewed"]].sum())
print("Артефакты:", EXPORT_DIR)
README_ASSETS = publish_exports(snapshot, ROOT)
print("Для будущего README:", README_ASSETS)
for p in sorted(EXPORT_DIR.glob("*")):
    if p.suffix in {".csv", ".xlsx", ".json"}:
        display(FileLink(p.relative_to(ROOT).as_posix()))
'''),
      nbf.v4.new_markdown_cell('''## Повторная работа

Для редактирования графиков задайте SNAPSHOT_PATH напечатанным путём. Он сохранит тот же состав данных независимо от продолжающегося full-008. Для нового среза обнулите SNAPSHOT_PATH и повторно выполните создание snapshot.
PNG/SVG, CSV, Excel и export_manifest.json экспортируются в snapshot/exports. Полные события доступны в events.csv; переходы — transitions.csv. Пропущенные из-за параллельного изменения файлы перечислены в snapshot.json. Срез не включает .env.''')]
    nb=nbf.v4.new_notebook(cells=cells)
    nb.metadata.kernelspec={'name':'harness-benchmark','display_name':'Harness benchmark (.venv)','language':'python'}
    nbf.validate(nb);nbf.write(nb,root/'03_partial_full008_analysis.ipynb')

if __name__=='__main__':generate(Path(__file__).resolve().parents[1])
