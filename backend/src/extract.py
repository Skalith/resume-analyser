import pdfplumber


def extract_pdf_text(pdf_path: str) -> str:
    """Extract all readable text from a PDF file.

    Iterates every page with pdfplumber, concatenates the extracted
    text, and returns the stripped result.
    """
    if not pdf_path.endswith(".pdf"):
        raise ValueError(f"Not a PDF: {pdf_path}")

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages_text.append(text)

    return "\n".join(pages_text).strip()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python extract.py <path-to-pdf>")
        sys.exit(1)

    text = extract_pdf_text(sys.argv[1])
    print(text)
    print(f"\n--- extracted {len(text)} characters ---")