"""Frontend: HTML shablonlardagi JS satrlari to'g'ri yopilganmi.

O'zbekcha apostrof xatosi: x-text="'… ko'rinadi'" — brauzerda SyntaxError beradi va xabar/tugma ishlamay qoladi.
To'g'risi: 'ko\\'rinadi' yoki "ko'rinadi".
"""
import html as h
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
ATTR = re.compile(r'\s((?:x-[\w:.-]+|:[\w.-]+|@[\w.-]+))="([^"]*)"')


def unterminated(code: str) -> int | None:
    """Yopilmagan '…', "…" yoki `…` satr boshlangan joy (yo'q bo'lsa — None)."""
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c in "'\"`":
            j = i + 1
            while j < n and code[j] != c:
                if code[j] == "\\":
                    j += 1
                elif code[j] == "\n" and c != "`":
                    return i
                j += 1
            if j >= n:
                return i
            i = j + 1
        elif code.startswith("//", i):
            while i < n and code[i] != "\n":
                i += 1
        elif c == "/" and re.search(r"[(,=:!&|?{};]\s*$", code[max(0, i - 40):i] or "("):
            j = i + 1  # regex literal
            while j < n and code[j] != "/":
                j += 2 if code[j] == "\\" else 1
            i = j + 1
        else:
            i += 1
    return None


def problems(src: str) -> list[str]:
    out = []
    for m in ATTR.finditer(src):
        code = h.unescape(m.group(2))
        if unterminated(code) is not None:
            out.append(f"{src[:m.start()].count(chr(10)) + 1}-qator {m.group(1)}: {code[:80]}")
    for m in re.finditer(r"<script>(.*?)</script>", src, re.S):
        base = src[:m.start(1)].count("\n")
        for k, line in enumerate(m.group(1).split("\n"), start=1):
            if not line.strip().startswith("//") and unterminated(line) is not None:
                out.append(f"{base + k}-qator: {line.strip()[:80]}")
    return out


@pytest.mark.parametrize("name", ["index.html", "login.html"])
def test_no_broken_js_strings(name):
    assert problems((TEMPLATES / name).read_text(encoding="utf-8")) == []


def test_checker_catches_the_apostrophe_bug():
    assert problems("""<span x-text="'tegishli ko'rinadi'"></span>""")
    assert not problems("""<span x-text="'tegishli ko\\'rinadi'"></span>""")
    assert problems("<script>\nthis.toast('Qoida qo'shildi');\n</script>")
    assert not problems("<script>\nthis.toast(\"Qoida qo'shildi\");\n</script>")
