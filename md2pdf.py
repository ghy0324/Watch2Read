#!/usr/bin/env python3
# /// script
# dependencies = ["markdown", "weasyprint"]
# ///
"""Markdown to PDF converter — expands <details> for print, supports CJK."""

import argparse
import ctypes.util
import os
import re
import subprocess
import sys
from pathlib import Path


def _ensure_homebrew_libs():
    """Auto-detect Homebrew lib path so WeasyPrint can find pango/cairo."""
    if ctypes.util.find_library("pango-1.0"):
        return
    try:
        prefix = subprocess.check_output(
            ["brew", "--prefix"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return
    lib_dir = os.path.join(prefix, "lib")
    if os.path.isfile(os.path.join(lib_dir, "libpango-1.0.dylib")):
        fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{lib_dir}:{fallback}" if fallback else lib_dir
        )


_ensure_homebrew_libs()

import markdown  # noqa: E402
from weasyprint import HTML  # noqa: E402

CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm;

    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #999;
    }
}

body {
    font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
                 "Microsoft YaHei", "Source Han Sans SC", sans-serif;
    font-size: 10pt;
    line-height: 1.7;
    color: #2c2c2c;
}

h1 {
    font-size: 18pt;
    border-bottom: 2px solid #333;
    padding-bottom: 8px;
    margin-top: 0;
    margin-bottom: 16px;
}

h2 {
    font-size: 14pt;
    color: #1a1a1a;
    border-bottom: 1px solid #ddd;
    padding-bottom: 4px;
    margin-top: 28px;
    margin-bottom: 12px;
}

h3 {
    font-size: 11pt;
    color: #333;
    margin-top: 20px;
    margin-bottom: 8px;
}

blockquote {
    border-left: 3px solid #4a90d9;
    padding: 8px 14px;
    margin: 12px 0;
    color: #555;
    background: #f8f9fa;
    font-size: 9pt;
}

blockquote strong {
    color: #333;
}

.details-block {
    margin: 6px 0 6px 12px;
    padding: 4px 0;
}

.summary-line {
    font-weight: 600;
    color: #2c2c2c;
    margin-bottom: 4px;
    font-size: 10pt;
}

.sub-heading {
    font-weight: 700;
    color: #1a73e8;
    margin-top: 10px;
    margin-bottom: 4px;
    font-size: 10pt;
}

a {
    color: #1a73e8;
    text-decoration: none;
}

code {
    background: #f0f0f0;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 9pt;
    font-family: "SF Mono", "Menlo", "Monaco", monospace;
}

hr {
    border: none;
    border-top: 1px solid #e0e0e0;
    margin: 20px 0;
}

ul {
    padding-left: 18px;
    margin: 4px 0;
}

li {
    margin-bottom: 3px;
}

strong a {
    color: #1a73e8;
}

p {
    margin: 6px 0;
}
"""


def expand_details_tags(html: str) -> str:
    """Replace interactive <details>/<summary> with styled divs for PDF."""
    html = re.sub(r"<details[^>]*>", '<div class="details-block">', html)
    html = html.replace("</details>", "</div>")
    html = re.sub(r"<summary[^>]*>", '<div class="summary-line">', html)
    html = html.replace("</summary>", "</div>")
    return html


def md_to_pdf(md_path: str, pdf_path: str | None = None) -> str:
    md_file = Path(md_path)
    if not md_file.exists():
        raise FileNotFoundError(f"File not found: {md_path}")

    if pdf_path is None:
        pdf_path = str(md_file.with_suffix(".pdf"))

    md_content = md_file.read_text(encoding="utf-8")

    html_body = markdown.markdown(
        md_content,
        extensions=["tables", "fenced_code"],
    )

    html_body = expand_details_tags(html_body)

    full_html = f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    HTML(string=full_html).write_pdf(pdf_path)
    return pdf_path


def main():
    parser = argparse.ArgumentParser(description="Convert Markdown to PDF")
    parser.add_argument("input", help="Input Markdown file path")
    parser.add_argument(
        "-o", "--output", help="Output PDF file path (default: same name with .pdf)"
    )
    args = parser.parse_args()

    try:
        output = md_to_pdf(args.input, args.output)
        print(f"✓ PDF generated: {output}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
