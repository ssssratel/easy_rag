"""使用 Docling 解析本地 PDF，并导出 Markdown、JSON 与解析摘要。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_PDF = Path(__file__).resolve().parent / "test_file" / "123.pdf"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "test_file" / "docling_output"


def create_converter(enable_ocr: bool = False):
    """创建仅处理 PDF 的 Docling 转换器。"""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise RuntimeError(
            "当前 Python 环境未安装 Docling，请先执行: pip install docling"
        ) from exc

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = enable_ocr
    pipeline_options.do_table_structure = True

    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
            )
        },
    )


def parse_pdf(
    pdf_path: Path,
    output_dir: Path,
    enable_ocr: bool = False,
) -> dict:
    """解析 PDF、保存结果文件，并返回不含正文的摘要。"""
    pdf_path = pdf_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()

    if not pdf_path.is_file():
        raise FileNotFoundError("PDF 文件不存在: {}".format(pdf_path))
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("仅支持 PDF 文件: {}".format(pdf_path))

    output_dir.mkdir(parents=True, exist_ok=True)
    converter = create_converter(enable_ocr=enable_ocr)

    started_at = time.perf_counter()
    result = converter.convert(str(pdf_path))
    elapsed_seconds = round(time.perf_counter() - started_at, 3)

    document = result.document
    markdown = document.export_to_markdown()
    document_json = document.export_to_dict()

    output_stem = "{}.ocr".format(pdf_path.stem) if enable_ocr else pdf_path.stem
    markdown_path = output_dir / "{}.md".format(output_stem)
    json_path = output_dir / "{}.json".format(output_stem)
    summary_path = output_dir / "{}.summary.json".format(output_stem)

    markdown_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(document_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "source": str(pdf_path),
        "source_size_bytes": pdf_path.stat().st_size,
        "ocr_enabled": enable_ocr,
        "elapsed_seconds": elapsed_seconds,
        "pages": len(getattr(document, "pages", {})),
        "tables": len(getattr(document, "tables", [])),
        "pictures": len(getattr(document, "pictures", [])),
        "markdown_characters": len(markdown),
        "markdown_output": str(markdown_path),
        "json_output": str(json_path),
        "summary_output": str(summary_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    """定义命令行参数。"""
    parser = argparse.ArgumentParser(description="使用 Docling 测试 PDF 解析")
    parser.add_argument(
        "pdf_path",
        nargs="?",
        type=Path,
        default=DEFAULT_PDF,
        help="待解析 PDF；默认 test_file/123.pdf",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="结果目录；默认 test_file/docling_output",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="启用 OCR；数字版 PDF 建议先不启用",
    )
    return parser


def main() -> int:
    """执行解析测试并打印摘要。"""
    args = build_parser().parse_args()
    try:
        summary = parse_pdf(args.pdf_path, args.output_dir, enable_ocr=args.ocr)
    except Exception as exc:
        print("Docling 解析失败: {}".format(exc), file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
