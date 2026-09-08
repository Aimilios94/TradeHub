"""prompt-library integrity: every relative markdown link resolves; §23 structure present; originals untouched."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "prompt-library"
LINK_RE = re.compile(r"\]\(([^)#]+)(?:#[^)]*)?\)")


def _md_files():
    return [p for p in LIB.rglob("*.md")]


def test_every_relative_link_resolves():
    broken = []
    for md in _md_files():
        for target in LINK_RE.findall(md.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).exists():
                broken.append(f"{md.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, "\n".join(broken)


def test_section_23_structure():
    expected = {
        "strategies": 8, "frameworks": 2, "hermes-core": 7, "hermes-extensions": 9,
        "extension-modules": 6, "tests": 3,
    }
    for folder, n in expected.items():
        files = list((LIB / folder).glob("*.md"))
        assert len(files) == n, f"{folder}: expected {n} files, found {len(files)}"
    for code in range(1, 9):
        assert list((LIB / "strategies").glob(f"S{code}_*.md")), f"S{code} file missing"


def test_originals_index_lists_every_file():
    originals = LIB / "_originals"
    index = (originals / "00_README.md").read_text(encoding="utf-8")
    for p in originals.rglob("*"):
        if p.is_file() and p.name != "00_README.md":
            rel = p.relative_to(originals).as_posix()
            assert rel in index, f"{rel} not listed in _originals/00_README.md"


def test_readme_links_every_adapted_file():
    readme = (LIB / "README.md").read_text(encoding="utf-8")
    for folder in ("strategies", "frameworks", "hermes-core", "hermes-extensions", "extension-modules", "tests"):
        for p in (LIB / folder).glob("*.md"):
            assert f"{folder}/{p.name}" in readme, f"{folder}/{p.name} not linked from prompt-library/README.md"
