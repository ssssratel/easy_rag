#!/usr/bin/env python3
"""
文档分块脚本
将 .txt / .html 源文档切分为 chunk，导出为 rag_builder.py 所需的 JSON 格式。
也可被 rag_builder.py 直接导入使用。
"""
import os
import re
import json
import argparse
from pathlib import Path
from html.parser import HTMLParser


# ========== 文本提取 ==========
def read_txt(filepath):
    """读取 txt 文件，返回文本内容"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


class _HTMLTextExtractor(HTMLParser):
    """HTML 转纯文本：丢弃 script/style，标签映射换行"""

    BLOCK_TAGS = {'p', 'br', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                  'tr', 'hr', 'section', 'article', 'header', 'footer', 'nav'}
    SKIP_TAGS = {'script', 'style', 'noscript'}

    def __init__(self):
        """初始化文本片段和跳过标签的状态。"""
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, _attrs):
        """把块标签转为换行，并跳过脚本等内容。"""
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append('\n')

    def handle_endtag(self, tag):
        """结束跳过区域并保留块标签边界。"""
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append('\n')

    def handle_data(self, data):
        """收集可见文本。"""
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self):
        """返回合并后的纯文本。"""
        return ''.join(self._parts)


def extract_html(filepath):
    """提取 HTML 文件的纯文本内容"""
    with open(filepath, 'r', encoding='utf-8') as f:
        parser = _HTMLTextExtractor()
        parser.feed(f.read())
        return parser.get_text()


def read_document(filepath):
    """
    根据扩展名读取文档文本。
    支持格式：.txt .html .htm .md
    """
    ext = Path(filepath).suffix.lower()
    if ext in ('.html', '.htm'):
        return extract_html(filepath)
    else:
        return read_txt(filepath)


def split_by_paragraph(text, chunk_size=500, overlap=50):
    """
    按段落 + 长度分块。
    优先在段落边界切，段落过长时按句子切。
    """
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if not current_chunk:
            current_chunk = para
        elif len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk += "\n\n" + para
        else:
            if current_chunk:
                chunks.append(current_chunk)

            if len(para) > chunk_size:
                sentences = re.split(r'(?<=[。！？.!?])\s*', para)
                sub_chunk = ""
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    if not sub_chunk:
                        sub_chunk = sent
                    elif len(sub_chunk) + len(sent) + 1 <= chunk_size:
                        sub_chunk += sent
                    else:
                        chunks.append(sub_chunk)
                        if overlap > 0 and len(sub_chunk) > overlap:
                            sub_chunk = sub_chunk[-overlap:] + sent
                        else:
                            sub_chunk = sent
                current_chunk = sub_chunk if sub_chunk else ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def chunk_directory(input_dir, chunk_size=500, overlap=50):
    """
    遍历目录下 .txt/.html/.md 文件，分块并返回 chunk 列表。
    供 rag_builder.py 直接调用。

    Returns:
        list[dict]: 每个元素包含 content, filename, page
    """
    SUPPORTED = {"*.txt", "*.html", "*.htm", "*.md"}
    doc_files = []
    for pattern in SUPPORTED:
        doc_files.extend(Path(input_dir).rglob(pattern))
    # 去重（同一文件可能被多个 pattern 匹配）
    doc_files = list(set(doc_files))

    if not doc_files:
        print(f"在 {input_dir} 中未找到支持的文档文件 (.txt/.html/.md)")
        return []

    print(f"找到 {len(doc_files)} 个文档文件")

    all_chunks = []
    for filepath in doc_files:
        print(f"  {filepath.name} ...", end=" ")
        text = read_document(filepath)
        if not text.strip():
            print("空文件，跳过")
            continue

        chunks = split_by_paragraph(text, chunk_size=chunk_size, overlap=overlap)
        for i, chunk in enumerate(chunks):
            all_chunks.append({
                "content": chunk,
                "filename": filepath.name,
                "page": i + 1
            })
        print(f"{len(chunks)} 个 chunk")

    print(f"共生成 {len(all_chunks)} 个 chunk")
    return all_chunks


def chunk_documents(input_dir, output_file="chunks.json", chunk_size=500, overlap=50):
    """遍历目录下所有 .txt 文件，分块后导出 JSON（独立使用）"""
    all_chunks = chunk_directory(input_dir, chunk_size=chunk_size, overlap=overlap)
    if all_chunks:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_chunks, f, ensure_ascii=False, indent=2)
        print(f"已保存到 {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将 txt 文档切分为 chunk 并导出 JSON")
    parser.add_argument("input_dir", type=str, help="源文档目录路径")
    parser.add_argument("--output", type=str, default="chunks.json", help="输出 JSON 路径")
    parser.add_argument("--chunk_size", type=int, default=500, help="chunk 最大字符数")
    parser.add_argument("--overlap", type=int, default=50, help="重叠字符数")
    args = parser.parse_args()

    chunk_documents(
        input_dir=args.input_dir,
        output_file=args.output,
        chunk_size=args.chunk_size,
        overlap=args.overlap
    )
