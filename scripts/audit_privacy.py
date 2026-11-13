"""Offline audit against reference annotations; never sends data to a model."""
from pathlib import Path
import json
from scripts.benchlib.core import read_json, write_json
from scripts.benchlib.documents import read_document
from scripts.benchlib.privacy import prepare_advice, normalized, TOKEN


def audit(root):
    records = []
    for manifest in sorted((root / "bemnchmark").glob("BENCH-*/case.json")):
        case = read_json(manifest)
        for path in sorted((manifest.parent / "input").iterdir()):
            text = json.dumps(read_document(path), ensure_ascii=False, default=str)
            decision = prepare_advice("protected", {"question": text, "topic": "translation"}, [],
                policy="pseudonymize_affine", vault_dir=root / ".tmp/privacy-audit" / case["id"] / path.name,
                semantic_sensitive=case.get("semantic_sensitive", False))
            sent = decision.get("question", "") if decision["allowed"] else ""
            plain = TOKEN.sub("", sent)
            present = [s for s in case["secrets"] if normalized(s["value"]) in normalized(text)]
            leaked = [s for s in present if normalized(s["value"]) in normalized(plain)]
            records.append({"case": case["id"], "file": path.name, "annotated_secrets_present": len(present),
                            "residual_secrets": leaked, "policy": decision.get("policy"),
                            "passed_exact_match_check": not leaked})
    write_json(root / "docs/privacy-audit.json", records)
    rows = ["# Локальная проверка защиты", "", "Проверены все входные файлы; API не используется. Разметка используется только для оценки, не для обнаружения.",
            "Проверка ищет остатки известных секретов в исходном написании. Она не доказывает отсутствие смысловых утечек, парафразов и неизвестных персональных данных.",
            "", "| Задание | Файл | Секретов в разметке найдено | Осталось | Политика |", "|---|---|---:|---:|---|"]
    for r in records:
        rows.append(f"| {r['case']} | {r['file']} | {r['annotated_secrets_present']} | {len(r['residual_secrets'])} | {r['policy']} |")
    (root / "docs/privacy-audit.md").write_text("\n".join(rows)+"\n", encoding="utf-8")
    failures = [r for r in records if r["residual_secrets"]]
    print(json.dumps({"files_checked": len(records), "failed_files": failures}, ensure_ascii=False, indent=2))
    return records


if __name__ == "__main__":
    audit(Path(__file__).resolve().parents[1])
