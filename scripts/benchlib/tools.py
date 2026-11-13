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
           {"path": STRING, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1}}, ["path"]),
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

# Native JSON values avoid asking a small model to serialize JSON inside a JSON string.
for tool in TOOLS:
    f = tool["function"]
    props = f["parameters"]["properties"]
    for old, new, kind in [("data_json", "data", "object"), ("changes_json", "changes", "array"),
                           ("replacements_json", "replacements", "object")]:
        if old not in props:
            continue
        del props[old]
        props[new] = {"type": kind}
        if kind == "array":
            props[new]["items"] = {"type": "object"}
        f["parameters"]["required"] = [new if r == old else r for r in f["parameters"]["required"]]
        f["description"] = f["description"].replace(old, new).replace("JSON string", "native JSON object")

TOOLS.extend([
    schema("read_sheet", "Read Excel rows as native values, preserving formulas. Use small pages; next_row indicates continuation.",
           {"path": STRING, "sheet": STRING, "start_row": {"type": "integer", "minimum": 1},
            "count": {"type": "integer", "minimum": 1}}, ["path", "sheet", "start_row", "count"]),
    schema("write_rows", "Write a small batch of Excel rows at explicit coordinates. Creates workbook/sheet if needed. For translation, copy_file first, then replace text cells with edit_cells; do not regenerate unchanged numeric rows. Use at most 25 rows per call. Repeating the same start_row replaces those rows, not appends duplicates.",
           {"path": STRING, "sheet": STRING, "start_row": {"type": "integer", "minimum": 1},
            "rows": {"type": "array", "maxItems": 25, "items": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}}}},
           ["path", "sheet", "start_row", "rows"]),
    schema("append_text", "Create or append a short text/HTML chunk. append=false starts a new file; append=true continues it. Use for long HTML rather than one huge call.",
           {"path": STRING, "text": STRING, "append": {"type": "boolean"}}, ["path", "text", "append"]),
])

CELL_VALUE = {"anyOf": [{"type": ["string", "number", "boolean", "null"]},
                         {"type": "object", "properties": {"formula": {"type": "string", "pattern": "^="}},
                          "required": ["formula"], "additionalProperties": False}]}
for tool in TOOLS:
    f = tool["function"]
    props = f["parameters"]["properties"]
    if f["name"] == "edit_cells":
        props["changes"] = {"type": "array", "minItems": 1, "items": {
            "type": "object", "properties": {"sheet": STRING, "cell": {"type": "string", "pattern": "^[A-Za-z]{1,3}[1-9][0-9]*$"}, "value": CELL_VALUE},
            "required": ["cell", "value"], "additionalProperties": False}}
    elif f["name"] == "revise_contract":
        props["changes"] = {"type": "array", "minItems": 1, "items": {
            "type": "object", "properties": {k: {"type": "string", "minLength": 1} for k in ["old", "new", "reason", "policy_ref"]},
            "required": ["old", "new", "reason", "policy_ref"], "additionalProperties": False}}
    elif f["name"] == "translate_docx":
        props["replacements"] = {"type": "object", "minProperties": 1, "additionalProperties": STRING}
    elif f["name"] == "write_file":
        props["data"] = {"type": "object", "properties": {
            "title": STRING, "text": STRING,
            "paragraphs": {"type": "array", "items": STRING},
            "tables": {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": CELL_VALUE}}},
            "sheets": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "array", "items": {"type": "array", "items": CELL_VALUE}}},
            "slides": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                "title": STRING, "bullets": {"type": "array", "items": STRING}, "notes": STRING}, "required": ["title"], "additionalProperties": False}}},
            "anyOf": [{"required": [k]} for k in ["text", "paragraphs", "tables", "sheets", "slides"]], "additionalProperties": False}
    if f["name"] == "read_sheet":
        f["description"] += " Get exact sheet names from list_files; never assume Sheet1. Requests above 50 rows are automatically paginated; follow next_row."

TOOLS.extend([
    schema('write_text','Write a text/Markdown/HTML file directly; use append_text for long files.',
           {'path':STRING,'text':STRING},['path','text']),
    schema('create_word','Create a Word document from paragraphs and optional tables. Use revise_contract for tracked revisions.',
           {'path':STRING,'title':STRING,'paragraphs':{'type':'array','minItems':1,'items':STRING}},['path','title','paragraphs']),
    schema('create_presentation','Create PPTX from a native array of slides; each slide needs title and bullets.',
           {'path':STRING,'slides':{'type':'array','minItems':1,'items':{'type':'object','properties':{
               'title':STRING,'bullets':{'type':'array','items':STRING},'notes':STRING},
               'required':['title','bullets'],'additionalProperties':False}}},['path','slides'])
])


class FileTools:
    def __init__(self, input_dir: Path, output_dir: Path, cache: Path, trace, force_refresh=False):
        self.input = input_dir.resolve()
        self.output = output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.cache = cache
        self.trace = trace
        self.force_refresh = force_refresh

    def path(self, value, write=False):
        value = value.replace("\\", "/")
        if value.startswith("./"):
            value = value[2:]
        if value.startswith("input/") and not write:
            return safe_path(self.input, value[6:])
        if value.startswith("output/"):
            return safe_path(self.output, value[7:])
        if write and not value.startswith("input/"):
            return safe_path(self.output, value)
        if not write:
            candidates = [safe_path(root, value) for root in [self.input, self.output]]
            found = [p for p in candidates if p.is_file()]
            if len(found) == 1:
                return found[0]
            if len(found) > 1:
                raise ValueError("Ambiguous filename: specify input/ or output/")
        available=list(inventory(self.input))[:10]
        raise ValueError(f"File not found or invalid scope. Read input/<file>, write output/<file>. Available input files: {available}")

    def execute(self, name: str, args: dict):
        import json
        args = dict(args)
        # Legacy recordings remain replayable; current schemas expose native objects only.
        for old, new in [("data_json", "data"), ("changes_json", "changes"), ("replacements_json", "replacements")]:
            if new in args:
                args[old] = json.dumps(args.pop(new), ensure_ascii=False)
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
            if name == "read_sheet":
                from openpyxl import load_workbook
                book = load_workbook(self.path(args["path"]), read_only=True, data_only=False)
                if args["sheet"] not in book.sheetnames:
                    names = book.sheetnames
                    book.close()
                    raise ValueError(f"Unknown sheet. Available sheets: {names}")
                sheet = book[args["sheet"]]
                first, count = args["start_row"], min(args["count"], 50)
                result = {"rows": list(sheet.iter_rows(min_row=first, max_row=min(first+count-1, sheet.max_row), values_only=True)),
                          "start_row": first, "total_rows": sheet.max_row, "total_columns": sheet.max_column,
                          "requested_count":args["count"],"effective_count":count,
                          "next_row": first+count if first+count <= sheet.max_row else None}
                book.close()
            elif name == "write_rows":
                from openpyxl import load_workbook, Workbook
                target = self.path(args["path"], write=True)
                if target.suffix.lower() != ".xlsx":
                    raise ValueError("write_rows requires .xlsx")
                fresh = not target.exists()
                book = load_workbook(target) if not fresh else Workbook()
                if fresh:
                    book.active.title = args["sheet"]
                sheet = book[args["sheet"]] if args["sheet"] in book.sheetnames else book.create_sheet(args["sheet"])
                for i, row in enumerate(args["rows"], args["start_row"]):
                    for j, value in enumerate(row, 1):
                        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                            value = "'"+value
                        sheet.cell(i, j).value = value
                target.parent.mkdir(parents=True, exist_ok=True)
                book.save(target)
                book.close()
                result = {"written": args["path"], "next_row": args["start_row"]+len(args["rows"])}
            elif name == "append_text":
                target = self.path(args["path"], write=True)
                if target.suffix.lower() not in {".txt", ".md", ".html", ".csv"}:
                    raise ValueError("Text output extension required")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a" if args["append"] else "w", encoding="utf-8") as stream:
                    stream.write(args["text"])
                result = {"written": args["path"]}
            elif name == "list_files":
                result = {"input": list(inventory(self.input)), "output": list(before)}
                from openpyxl import load_workbook
                result["excel_sheets"] = {}
                for prefix, folder in [("input", self.input), ("output", self.output)]:
                    for path in folder.rglob("*.xlsx"):
                        book = load_workbook(path, read_only=True)
                        result["excel_sheets"][prefix+"/"+path.relative_to(folder).as_posix()] = book.sheetnames
                        book.close()
            elif name == "read_file":
                content = json.dumps(read_document(self.path(args["path"])), ensure_ascii=False, default=str)
                offset, limit = int(args.get("offset", 0)), min(int(args.get("limit", 50000)), 100000)
                if offset < 0 or limit <= 0:
                    raise ValueError("Invalid pagination")
                result = {"content": content[offset:offset+limit], "total_chars": len(content),
                          "next_offset": offset+limit if offset+limit < len(content) else None}
            elif name in {"write_text","create_word","create_presentation"}:
                target=self.path(args['path'],write=True)
                allowed={'write_text':{'.txt','.md','.html','.csv','.json'},'create_word':{'.docx'},'create_presentation':{'.pptx'}}
                if target.suffix.lower() not in allowed[name]:
                    raise ValueError(f"{name} requires one of {sorted(allowed[name])}; got {target.suffix}")
                write_document(target,{k:v for k,v in args.items() if k!='path'})
                result={'written':args['path']}
            elif name == "write_file":
                target = self.path(args["path"], write=True)
                data = json.loads(args["data_json"])
                needed = {".xlsx": "sheets", ".pptx": "slides", ".txt": "text", ".html": "text", ".md": "text", ".csv": "text", ".json": "text"}.get(target.suffix.lower())
                if needed and needed not in data:
                    raise ValueError(f"{target.suffix} requires data.{needed}; use native structured values")
                write_document(target, data)
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
                    sheet_name = item.get("sheet")
                    if sheet_name is None and len(book.sheetnames) == 1:
                        sheet_name = book.sheetnames[0]
                    if sheet_name not in book.sheetnames:
                        names = book.sheetnames
                        book.close()
                        raise ValueError(f"Each change needs a valid sheet. Available sheets: {names}")
                    value = item["value"]
                    if isinstance(value, dict):
                        value = value["formula"]
                    elif isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                        value = "'"+value
                    book[sheet_name][item["cell"]] = value
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
