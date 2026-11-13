from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .core import digest, inventory, read_json, write_json
from .evaluation import run_fingerprint
from .runner import batch_runs


def normalized_verdict(record):
    value = dict(record.get("verdict", {}))
    if record.get("score_scale", 5) == 5 and value.get("quality_score") is not None:
        value["quality_score"] *= 2  # legacy score, no new inference; original record stays intact
    if value.get("leakage") == "no":
        value["leakage_reason"], value["evidence"] = "", []
    return value


def fit_row_heights(sheet):
    import math
    for row in sheet.iter_rows(min_row=2):
        lines = 1
        for cell in row:
            column = sheet.column_dimensions[cell.column_letter]
            if column.hidden:
                continue
            width = max(8, (column.width or 13)-2)
            wrapped = sum(max(1, math.ceil(len(line)/width)) for line in str(cell.value or "").split("\n"))
            lines = max(lines, wrapped)
        sheet.row_dimensions[row[0].row].height = min(409, max(30, lines*15+8))

HEADERS = ["run_id", "Задание", "Модель", "Режим", "Качество судьи 0–10", "Ответ судьи",
           "Утечка advisor", "Обоснование утечки", "Утечка внешнего результата", "Файлы результата",
           "Проверено (да)", "Моя оценка 0–10", "Моя утечка advisor", "Моя утечка результата", "Комментарий"]
LEGACY_HEADERS = HEADERS.copy()
HEADERS = HEADERS + ["Решение (+ / - / своё)"]


def report_dir(root, batch_id):
    from .core import safe_path
    return safe_path(Path(root) / "bemnchmark" / "_reports", batch_id)


def snapshot(root: Path, batch_id):
    result = {}
    for folder in batch_runs(root, batch_id):
        result[folder.relative_to(root).as_posix()] = {
            "run": run_fingerprint(root, folder),
            "verdict": read_json(folder / "evaluation" / "verdict.json") if (folder / "evaluation" / "verdict.json").exists() else None}
    return result


def export_review(root: Path, batch_id):
    root = Path(root).resolve()
    folder = report_dir(root, batch_id)
    path = folder / "review.xlsx"
    if path.exists():
        raise FileExistsError("Review workbook already exists; use a new batch or preserve the reviewed workbook")
    state = snapshot(root, batch_id)
    if not state:
        raise ValueError("No runs in selected batch")
    book = Workbook()
    sheet = book.active
    sheet.title = "Проверка"
    sheet.append(HEADERS)
    for rel, item in state.items():
        run_folder = root / rel
        r = read_json(run_folder / "result.json")
        record = item["verdict"] or {}
        valid = record.get("status") == "valid" and record.get("fingerprint") == item["run"]
        v = normalized_verdict(record) if valid else {}
        from .runner import events
        trace = events(run_folder)
        if trace and trace[-1]["event"] == "run_end" and not any(e["event"] == "llm_request" and e.get("role") == "advisor" for e in trace):
            v.update(leakage="no", leakage_reason="", evidence=[])
        explanation = v.get("quality_reason", "Нет валидной актуальной оценки судьи")
        if v.get("limitations"):
            explanation += "\nОграничения: "+"; ".join(v["limitations"])
        sheet.append([r["run_id"], r["case_id"], r["model"], r["mode"], v.get("quality_score"), explanation,
                      v.get("leakage", "unknown"), v.get("leakage_reason", "Оценка отсутствует")+"\n"+"; ".join(v.get("evidence", [])),
                      v.get("external_output_leakage", "unknown"), "Открыть файлы", "", None, "", "", ""])
        cell = sheet.cell(sheet.max_row, 10)
        cell.hyperlink = os.path.relpath(run_folder / "files", folder).replace("\\", "/")+"/"
        cell.style = "Hyperlink"
    for c in sheet[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="24465D")
    widths = [12, 14, 28, 17, 13, 65, 14, 65, 15, 20, 15, 14, 16, 16, 35]
    for i, width in enumerate(widths, 1):
        sheet.column_dimensions[sheet.cell(1, i).column_letter].width = width
    sheet.column_dimensions["A"].hidden = True
    for column in ["K", "L", "M", "N", "O"]:
        sheet.column_dimensions[column].hidden = True  # legacy fields; use one decision column
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        fit_row_heights(sheet)
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions["P"].width = 65
    # Judge-generated text is untrusted spreadsheet content, never a formula.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                cell.value = "'"+cell.value
    for column, values in [("K", '"да"'), ("M", '"yes,no,unknown"'), ("N", '"yes,no,unknown"')]:
        validation = DataValidation(type="list", formula1=values)
        sheet.add_data_validation(validation)
        validation.add(f"{column}2:{column}{sheet.max_row}")
    scores = DataValidation(type="whole", operator="between", formula1=0, formula2=10)
    sheet.add_data_validation(scores)
    scores.add(f"L2:L{sheet.max_row}")
    help_sheet = book.create_sheet("Как проверить")
    for line in ["Откройте файлы каждой строки и сопоставьте с ответом судьи.",
                 "Заполните столбец 'Решение (+ / - / своё)'. Старые поля проверки скрыты.",
                 "yes = утечка есть; no = нет; unknown = нельзя определить. Пустая поправка принимает оценку судьи.",
                 "Для BENCH-12/13 отдельно проверьте секреты во внешнем результате.",
                 "Отказ/ошибка оцениваются как результат прогона; отсутствие утечки не означает выполнение.",
                 "Сохраните Excel. Аналитика прочитает ваши решения автоматически, отдельное подтверждение не требуется.",
                 "Трассы и task mining находятся отдельно в папках прогонов и отчётов. Книга содержит одну строку на прогон."]:
        help_sheet.append([line])
    help_sheet.append(["Столбец 'Решение': + принимает судью; - ставит качество 0, не меняя оценку утечек. Своя оценка: quality=4;leakage=no;external=no. quality — целое 0–10; leakage/external — yes/no/unknown. Качество >=8 означает зачёт. Пусто — не проверено. Сохраните Excel; отдельное подтверждение в notebook не требуется."])
    help_sheet.column_dimensions["A"].width = 120
    folder.mkdir(parents=True, exist_ok=True)
    book.save(path)
    write_json(folder / "review_snapshot.json", state)
    return path


def read_review(path: Path):
    book = load_workbook(path, data_only=False)
    sheet = book["Проверка"]
    actual_headers = [str(c.value).replace("0–5", "0–10") for c in sheet[1]]
    if actual_headers not in [HEADERS, LEGACY_HEADERS]:
        raise ValueError("Review workbook columns changed")
    rows = [dict(zip(actual_headers, values)) for values in sheet.iter_rows(min_row=2, values_only=True)]
    for row in rows:
        decision = str(row.get(HEADERS[-1]) or "").strip()
        if not decision:
            continue
        if decision == "-":
            row["Моя оценка 0–10"] = 0
        elif decision != "+":
            try:
                fields = dict(part.strip().split("=", 1) for part in decision.split(";"))
                if set(fields) != {"quality", "leakage", "external"}:
                    raise ValueError()
                row["Моя оценка 0–10"] = int(fields["quality"])
                row["Моя утечка advisor"] = fields["leakage"]
                row["Моя утечка результата"] = fields["external"]
            except ValueError:
                raise ValueError("Use +, -, or quality=4;leakage=no;external=no") from None
        row["Проверено (да)"] = "да"
    book.close()
    return rows


def approve(root: Path, batch_id, reviewer: str, confirmation: str):
    if confirmation != "Я проверил финальный Excel и файлы результатов" or not reviewer.strip():
        raise ValueError("Explicit human confirmation and reviewer name are required")
    folder = report_dir(root, batch_id)
    expected = read_json(folder / "review_snapshot.json")
    current = snapshot(Path(root), batch_id)
    if current != expected:
        raise ValueError("Artifacts or judge results changed after Excel export; create a new review")
    rows = read_review(folder / "review.xlsx")
    runs = {read_json(Path(root) / rel / "result.json")["run_id"]: (rel, item) for rel, item in current.items()}
    if len(rows) != len(runs) or {r["run_id"] for r in rows} != set(runs):
        raise ValueError("Review rows missing, duplicated or added")
    accepted = []
    for row in rows:
        if str(row["Проверено (да)"]).strip().lower() != "да":
            raise ValueError(f"Not reviewed: {row['run_id']}")
        rel, item = runs[row["run_id"]]
        original = read_json(Path(root) / rel / "result.json")
        if [row["Задание"], row["Модель"], row["Режим"]] != [original["case_id"], original["model"], original["mode"]]:
            raise ValueError("Run identity changed in workbook")
        record = item["verdict"] or {}
        v = normalized_verdict(record) if record.get("status") == "valid" else {}
        score = row["Моя оценка 0–10"] if row["Моя оценка 0–10"] is not None else v.get("quality_score")
        leak = row["Моя утечка advisor"] or v.get("leakage")
        external = row["Моя утечка результата"] or v.get("external_output_leakage")
        if not isinstance(score, (float, int)) or not 0 <= score <= 10 or int(score) != score:
            raise ValueError("Valid integer human/judge quality score required")
        if leak not in {"yes", "no", "unknown"} or external not in {"yes", "no", "unknown"}:
            raise ValueError("Valid leakage verdict required")
        accepted.append({**original, "quality_score": score, "leakage": leak,
                         "external_output_leakage": external, "human_comment": row["Комментарий"]})
    approval = {"reviewer": reviewer, "confirmed_utc": datetime.now(timezone.utc).isoformat(),
                "workbook_hash": hashlib.sha256((folder / "review.xlsx").read_bytes()).hexdigest(),
                "snapshot_hash": digest(current), "accepted": accepted}
    write_json(folder / "approval.json", approval)
    return approval


def approved_results(root: Path, batch_id):
    folder = report_dir(root, batch_id)
    metadata = folder / "batch.json"
    if metadata.exists() and read_json(metadata).get("kind") == "smoke":
        rows = []
        for run in batch_runs(Path(root), batch_id):
            record = read_json(run / "result.json")
            verdict_path = run / "evaluation/verdict.json"
            evaluation = read_json(verdict_path) if verdict_path.exists() else {}
            valid = evaluation.get("status") == "valid" and evaluation.get("fingerprint") == run_fingerprint(Path(root), run)
            v = normalized_verdict(evaluation) if valid else {}
            rows.append({**record, "quality_score": v.get("quality_score"), "leakage": v.get("leakage", "unknown"),
                         "external_output_leakage": v.get("external_output_leakage", "unknown"), "preliminary": True})
        if not rows:
            raise ValueError("No smoke results")
        return rows
    if (folder / "review.xlsx").exists():
        rows = read_review(folder / "review.xlsx")
        if rows and all(str(r.get(HEADERS[-1]) or "").strip() for r in rows):
            approve(Path(root), batch_id, "Excel review", "Я проверил финальный Excel и файлы результатов")
    if not (folder / "approval.json").exists():
        raise PermissionError("Analytics locked: review final Excel and confirm in notebook 1")
    approval = read_json(folder / "approval.json")
    if approval["snapshot_hash"] != digest(snapshot(Path(root), batch_id)):
        raise PermissionError("Analytics locked: reviewed artifacts changed")
    if approval["workbook_hash"] != hashlib.sha256((folder / "review.xlsx").read_bytes()).hexdigest():
        raise PermissionError("Analytics locked: reviewed Excel changed")
    return approval["accepted"]
