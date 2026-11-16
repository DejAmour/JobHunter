from pathlib import Path

from jobhunter import cv

CV_TEXT = """Jed Dahlke
Exeter, Devon
00000000000 | jeddahlke@example.com

EDUCATION
University of Exeter
MSc Mathematical Finance - Distinction, Sept 2025 - Sept 2026

PROFESSIONAL EXPERIENCE
Investment Analyst
William Highbourne Wealth Management, Jul 2022 - Dec 2024
- Supported an 80m model portfolio service

SKILLS
Python, Excel, VBA, portfolio construction
"""


def test_reads_the_basics(tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text(CV_TEXT, encoding="utf-8")
    result = cv.audit(path)
    assert result.name == "Jed Dahlke"
    assert result.email == "jeddahlke@example.com"
    assert "education" in result.headings_found
    assert "experience" in result.headings_found
    assert "skills" in result.headings_found
    assert result.confidence >= 80


def test_warns_when_skills_section_missing(tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text(CV_TEXT.replace("SKILLS", "OTHER"), encoding="utf-8")
    result = cv.audit(path)
    assert any("Skills" in w for w in result.warnings)


def test_keyword_coverage(tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text(CV_TEXT, encoding="utf-8")
    result = cv.audit(path, ("python", "bloomberg", "portfolio construction"))
    assert "python" in result.present_terms
    assert "bloomberg" in result.absent_terms


def test_private_use_bullets_are_flagged(tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text(CV_TEXT.replace("- Supported", "\uf0b7 Supported"), encoding="utf-8")
    result = cv.audit(path)
    assert any("private-use glyph" in w for w in result.warnings)


def test_docx_text_is_extracted(tmp_path):
    import zipfile

    path = tmp_path / "cv.docx"
    body = (
        "<w:document><w:body>"
        "<w:p><w:r><w:t>Jed Dahlke</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>EDUCATION</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>jeddahlke@example.com</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    result = cv.audit(path)
    assert result.kind == "docx"
    assert result.email == "jeddahlke@example.com"


def test_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        cv.audit(Path(tmp_path / "nope.pdf"))
