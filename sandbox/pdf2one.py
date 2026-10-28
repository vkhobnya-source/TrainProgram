import argparse
from pathlib import Path

from pypdf import PdfWriter


def merge_pdfs(input_dir: str, output_file: str) -> int:
    source_dir = Path(input_dir).resolve()
    output_path = Path(output_file).resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"Папка не найдена: {source_dir}")
        return 1

    pdf_files = sorted(source_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"В папке {source_dir} нет PDF-файлов.")
        return 1

    writer = PdfWriter()

    for pdf in pdf_files:
        print(f"Добавляю: {pdf.name}")
        writer.append(str(pdf))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    writer.close()
    print(f"Готово: {output_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Объединяет PDF-файлы из выбранной папки в один PDF.")
    parser.add_argument("input_dir", nargs="?", default="pdfs", help="Папка с PDF-файлами (по умолчанию: pdfs)")
    parser.add_argument("-o", "--output", default="merged.pdf", help="Имя/путь итогового файла (по умолчанию: merged.pdf)")
    args = parser.parse_args()

    raise SystemExit(merge_pdfs(args.input_dir, args.output))