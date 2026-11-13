from __future__ import annotations

import base64
import shutil
import time
from pathlib import Path

from .core import digest, environment, inventory, read_json, safe_path, write_json
from .documents import read_document, revise_contract, write_document


def schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}}}


STRING = {"type": "string"}
TOOLS = [
    schema("list_files", "List all available input/ and output/ files", {}, []),
    schema("read_file", "Read a document. Paths start with input/ or output/. Returns full content; use offset/limit to paginate characters.",
           {"path": STRING, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100000}}, ["path"]),
    schema("write_file", 'Create output file. data_json: JSON string. xlsx: {sheets:{Sheet:[[cells]]}}, formula cell: {formula:"=SUM(B2:B3)"}; docx: {title,paragraphs:[],tables:[]}; pptx: {slides:[{title,bullets:[],notes}]}; html/txt: {text:"..."}. Include source references in results.',
           {"path": STRING, "data_json": STRING}, ["path", "data_json"]),
    schema("edit_cells", 'Copy/update xlsx preserving sheets/formulas/styles. changes_json: [{sheet,cell,value}], formula value: {formula:"=..."}. Source input/ or output/, target output/.',
           {"source": STRING, "target": STRING, "changes_json": STRING}, ["source", "target", "changes_json"]),
    schema("revise_contract", 'Word tracked changes and comments. changes_json: [{old,new,reason,policy_ref}]. old must match exactly one whole or partial paragraph. source input/, target output/.',
           {"source": STRING, "target": STRING, "changes_json": STRING}, ["source", "target", "changes_json"]),
    schema("copy_file", "Copy original file into output/ under a classified archive path, preserving binary content.",
           {"source": STRING, "target": STRING}, ["source", "target"]),
    schema("translate_docx", 'Preserve Word layout, replace exact paragraph/cell texts. replacements_json: {"original text":"translated text"}. All matching texts replaced, numbers preserved by agent.',
           {"source": STRING, "target": STRING, "replacements_json": STRING}, ["source", "target", "replacements_json"]),
]
ADVICE_TOOL = schema("ask_advisor", "Ask external advisor. Never disclose secrets or confidential document meaning. topic: translation/contracts/aggregation/presentation/html/archive. question: safe question; purpose: brief observable rationale, no hidden chain of thought.",
                     {"topic": STRING, "question": STRING, "purpose": STRING}, ["topic", "question", "purpose"])


class FileTools:
    def __init__(self, input_dir: Path, output_dir: Path, cache: Path, trace, force_refresh=False):
        self.input = input_dir.resolve()
        self.output = output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.cache = cache
        self.trace = trace
        self.force_refresh = force_refresh

    def path(self, value, write=False):
        if value.startswith("input/") and not write:
            return safe_path(self.input, value[6:])
        if value.startswith("output/"):
            return safe_path(self.output, value[7:])
        raise ValueError("Use input/ for reading or output/ for writing")

    def execute(self, name: str, args: dict):
        import json
        # Full pre-state ensures writing tools replay only against the same workspace.
        key = digest({"source": Path(__file__).read_text(encoding="utf-8"),
                      "documents_source": Path(__file__).with_name("documents.py").read_text(encoding="utf-8"),
                      "environment": environment(), "name": name, "args": args,
                      "input": inventory(self.input), "output": inventory(self.output)})
        start = time.perf_counter()
        parent = self.trace.event("tool_start", tool=name, arguments=args, cache_key=key)
        cached = self.cache / f"{key}.json"
        if cached.exists() and not self.force_refresh:
            record = read_json(cached)
            for relative, encoded in record["files"].items():
                target = safe_path(self.output, relative)
                content = base64.b64decode(encoded)
                if digest(encoded) != record["checksums"][relative]:
                    raise RuntimeError("Corrupt tool artifact cache")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            self.trace.event("tool_end", parent_id=parent, tool=name, source="cache", result=record["result"],
                             artifacts=inventory(self.output), duration_s=time.perf_counter()-start,
                             original_duration_s=record["duration_s"])
            return record["result"]
        before = inventory(self.output)
        try:
            if name == "list_files":
                result = {"input": list(inventory(self.input)), "output": list(before)}
            elif name == "read_file":
                content = json.dumps(read_document(self.path(args["path"])), ensure_ascii=False, default=str)
                offset, limit = int(args.get("offset", 0)), min(int(args.get("limit", 50000)), 100000)
                if offset < 0 or limit <= 0:
                    raise ValueError("Invalid pagination")
                result = {"content": content[offset:offset+limit], "total_chars": len(content),
                          "next_offset": offset+limit if offset+limit < len(content) else None}
            elif name == "write_file":
                write_document(self.path(args["path"], write=True), json.loads(args["data_json"]))
                result = {"written": args["path"]}
            elif name == "revise_contract":
                revise_contract(self.path(args["source"]), self.path(args["target"], write=True), json.loads(args["changes_json"]))
                result = {"written": args["target"]}
            elif name == "copy_file":
                target = self.path(args["target"], write=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.path(args["source"]), target)
                result = {"written": args["target"]}
            elif name == "edit_cells":
                from openpyxl import load_workbook
                book = load_workbook(self.path(args["source"]))
                for item in json.loads(args["changes_json"]):
                    value = item["value"]
                    if isinstance(value, dict):
                        value = value["formula"]
                    elif isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                        value = "'"+value
                    book[item["sheet"]][item["cell"]] = value
                target = self.path(args["target"], write=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                book.save(target)
                book.close()
                result = {"written": args["target"]}
            elif name == "translate_docx":
                from docx import Document
                doc = Document(self.path(args["source"]))
                mapping = json.loads(args["replacements_json"])
                paragraphs = list(doc.paragraphs)
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            paragraphs.extend(cell.paragraphs)
                for p in paragraphs:
                    if p.text in mapping:
                        p.text = mapping[p.text]
                target = self.path(args["target"], write=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                doc.save(target)
                result = {"written": args["target"]}
            else:
                raise ValueError("Unknown file tool")
        except Exception as exc:
            result = {"error": type(exc).__name__, "message": str(exc)}
            self.trace.event("tool_error", parent_id=parent, tool=name, result=result,
                             duration_s=time.perf_counter()-start)
            return result
        after = inventory(self.output)
        files = {p: base64.b64encode(safe_path(self.output, p).read_bytes()).decode()
                 for p, h in after.items() if before.get(p) != h}
        elapsed = time.perf_counter()-start
        write_json(cached, {"result": result, "files": files, "checksums": {p: digest(v) for p, v in files.items()}, "duration_s": elapsed})
        self.trace.event("tool_end", parent_id=parent, tool=name, source="live", result=result,
                         artifacts=after, duration_s=elapsed)
        return result
