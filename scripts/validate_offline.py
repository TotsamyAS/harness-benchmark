"""Execute safe notebook cells, verify the locked analysis gate and render a sample PDF."""
from pathlib import Path
import os
import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError


def validate(root: Path):
    scratch = root / ".tmp"
    scratch.mkdir(exist_ok=True)
    os.environ["JUPYTER_PATH"] = str(root / ".venv/share/jupyter")
    first = nbformat.read(root / "01_run_benchmark.ipynb", as_version=4)
    for cell in first.cells:
        if cell.cell_type == "code" and "%pip install" in cell.source:
            cell.source = "pass  # Dependencies already installed; skip network installation in offline validation"
    NotebookClient(first, timeout=120, kernel_name="harness-benchmark", resources={"metadata": {"path": str(root)}}).execute()
    nbformat.write(first, scratch / "01_run_benchmark.executed.ipynb")
    second = nbformat.read(root / "02_analysis.ipynb", as_version=4)
    try:
        NotebookClient(second, timeout=120, kernel_name="harness-benchmark", resources={"metadata": {"path": str(root)}}).execute()
    except CellExecutionError as exc:
        if "Analytics locked" not in str(exc):
            raise
    else:
        raise AssertionError("Analysis should be locked before human approval")
    import pymupdf as fitz
    pdf = root / "bemnchmark/BENCH-03/input/act.pdf"
    with fitz.open(pdf) as doc:
        doc[0].get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).save(scratch / "sample-pdf.png")
    print("Offline notebook executed; analysis gate verified; sample PDF rendered. No .env or API used.")


if __name__ == "__main__":
    validate(Path(__file__).resolve().parents[1])
