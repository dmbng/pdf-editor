"""
Script to generate sample PDFs for testing and demonstrating PDF Editor CLI capabilities.
"""

from pathlib import Path
import pymupdf as fitz


def generate_sample_report(output_path: str = "sample_report.pdf") -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    
    doc = fitz.open()

    # Page 1
    p1 = doc.new_page(width=595, height=842)  # A4
    p1.insert_text(fitz.Point(50, 80), "Annual Technical Report 2026", fontsize=24, fontname="helv", color=(0.1, 0.2, 0.6))
    p1.insert_text(fitz.Point(50, 110), "Status: DRAFT", fontsize=14, fontname="helv", color=(0.8, 0.2, 0.2))
    p1.insert_text(fitz.Point(50, 150), "Section 1: Introduction", fontsize=16, fontname="helv", color=(0, 0, 0))
    p1.insert_textbox(
        fitz.Rect(50, 170, 545, 300),
        "This document presents the quarterly progress of our software architecture project. "
        "The system has demonstrated exceptional stability and reliability across all modules. "
        "Notice: CONFIDENTIAL data contained within page 2.",
        fontsize=11, fontname="helv"
    )

    # Page 2
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text(fitz.Point(50, 80), "Section 2: Security & Confidentiality", fontsize=18, fontname="helv", color=(0, 0, 0))
    p2.insert_textbox(
        fitz.Rect(50, 110, 545, 250),
        "Key security tokens and access keys:\n"
        "API Key: CONFIDENTIAL-9988-X12\n"
        "Security Access Code: SECRET-PASSPHRASE-42\n"
        "All data transfers are encrypted end-to-end.",
        fontsize=11, fontname="helv"
    )

    # Page 3
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text(fitz.Point(50, 80), "Section 3: Performance Benchmark", fontsize=18, fontname="helv", color=(0, 0, 0))
    p3.insert_textbox(
        fitz.Rect(50, 110, 545, 250),
        "Performance benchmarks indicate a 40% speedup in processing time.\n"
        "Conclusion: Ready for production deployment.",
        fontsize=11, fontname="helv"
    )

    # Page 4
    p4 = doc.new_page(width=595, height=842)
    p4.insert_text(fitz.Point(50, 80), "Appendix: Additional Notes", fontsize=18, fontname="helv", color=(0, 0, 0))
    p4.insert_text(fitz.Point(50, 120), "End of Document.", fontsize=12, fontname="helv")

    # Add TOC / Bookmarks
    toc = [
        [1, "Cover & Introduction", 1],
        [1, "Security Details", 2],
        [1, "Performance Benchmark", 3],
        [1, "Appendix Notes", 4]
    ]
    doc.set_toc(toc)

    doc.save(str(out))
    doc.close()
    return out


def generate_sample_invoice(output_path: str = "sample_invoice.pdf") -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text(fitz.Point(50, 80), "INVOICE #INV-2026-001", fontsize=20, fontname="helv", color=(0.2, 0.4, 0.8))
    p1.insert_text(fitz.Point(50, 110), "Billed To: ACME Corporation", fontsize=12, fontname="helv")
    p1.insert_text(fitz.Point(50, 130), "Date: 2026-08-19", fontsize=12, fontname="helv")

    p1.insert_textbox(
        fitz.Rect(50, 170, 545, 300),
        "Item 1: Software Architecture Consulting - $1,500.00\n"
        "Item 2: PDF Tooling Module Development - $2,000.00\n\n"
        "Total Amount Due: $3,500.00",
        fontsize=12, fontname="helv"
    )

    doc.save(str(out))
    doc.close()
    return out


if __name__ == "__main__":
    r1 = generate_sample_report("samples/sample_report.pdf")
    r2 = generate_sample_invoice("samples/sample_invoice.pdf")
    print(f"Generated sample PDFs: '{r1}' and '{r2}'")
