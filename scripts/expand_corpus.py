"""Explicit migration; preserve original inputs and manifests, never touch outputs."""
from pathlib import Path
import shutil
from scripts.benchlib.core import inventory, read_json
from scripts.benchlib.fixtures import generate


def migrate(root):
    cases = sorted((root / "bemnchmark").glob("BENCH-*/case.json"))
    pending = []
    for p in cases:
        c = read_json(p)
        if c["corpus_version"] == "synthetic-expanded-v2":
            continue
        if inventory(p.parent / "input") != c["input_hashes"]:
            raise RuntimeError(f"Modified inputs: {c['id']}; refusing overwrite")
        backup = root / "bemnchmark/_corpus_versions/synthetic-starter-v1" / c["id"]
        if backup.exists():
            raise RuntimeError(f"Backup already exists: {backup}")
        pending.append((p, backup))
    for p, backup in pending:
        backup.mkdir(parents=True)
        shutil.move(str(p.parent / "input"), str(backup / "input"))
        shutil.move(str(p), str(backup / "case.json"))
    generated = generate(root)
    rows = ["# Размеры расширенного корпуса", "", "Размер — число символов содержимого до записи в формат, не размер ZIP/PDF на диске.", "", "| Задание | Файл | Было | Стало | Рост |", "|---|---|---:|---:|---:|"]
    for case in generated:
        for name, size in case["content_sizes"].items():
            ratio = size["after_chars"]/size["before_chars"]
            rows.append(f"| {case['id']} | {name} | {size['before_chars']} | {size['after_chars']} | {ratio:.1f}× |")
    (root / "docs/corpus-sizes.md").write_text("\n".join(rows)+"\n", encoding="utf-8")
    print(f"Expanded {len(generated)} cases; originals preserved; outputs unchanged")


if __name__ == "__main__":
    migrate(Path(__file__).resolve().parents[1])
