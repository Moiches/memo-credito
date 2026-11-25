"""Extra exploration: concept catalogue, multi-line layout, RFC check digit, page-2 header."""
import collections
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from verify_all import group_lines, parse_statement  # noqa: E402

root = Path(sys.argv[1])

# 1) concept catalogue (normalized: strip digits/refs)
cat = collections.Counter()
for f in sorted(root.glob("SOL-*/estado_cuenta_*.pdf")):
    st = parse_statement(f)
    for m in st.movements:
        kind = "A" if m.abono is not None else "C"
        norm = re.sub(r"\d+", "#", m.concepto)
        cat[(kind, norm[:70])] += 1
print("== concept catalogue (kind, normalized concept): count")
for (kind, c), n in sorted(cat.items()):
    print(f"{kind} {n:4d}  {c}")

# 2) layout of the multi-line movement in SOL-0006 Aug
print("\n== SOL-0006 2026-08 lines around 2026-08-18")
with pdfplumber.open(root / "SOL-0006" / "estado_cuenta_2026-08.pdf") as pdf:
    words = pdf.pages[0].extract_words(extra_attrs=["fontname", "size"])
    lines = group_lines(words)
    for i, line in enumerate(lines):
        if any(w["text"] == "2026-08-18" for w in line):
            for l2 in lines[i - 1:i + 6]:
                print(f"top={l2[0]['top']:.2f} | " + " ".join(f"{w['text']}@{w['x0']:.0f}-{w['x1']:.0f}" for w in l2))
            break

# 3) page 2 header in SOL-0005 July
print("\n== SOL-0005 2026-07 page 2 first lines")
with pdfplumber.open(root / "SOL-0005" / "estado_cuenta_2026-07.pdf") as pdf:
    p2 = pdf.pages[1]
    for l in group_lines(p2.extract_words(extra_attrs=["fontname"]))[:6]:
        print(f"top={l[0]['top']:.2f} font={l[0]['fontname']} | " + " ".join(w["text"] for w in l))
    print("page1 last lines:")
    for l in group_lines(pdf.pages[0].extract_words(extra_attrs=["fontname"]))[-3:]:
        print(f"top={l[0]['top']:.2f} font={l[0]['fontname']} | " + " ".join(w["text"] for w in l))

# 4) RFC check digit (SAT algorithm, personas morales: 12 chars, padded with a leading space)
DICT = "0123456789ABCDEFGHIJKLMN&OPQRSTUVWXYZ Ñ"


def check_digit(rfc: str) -> str:
    base = rfc[:-1]
    if len(rfc) == 12:
        base = " " + base
    total = sum(DICT.index(ch) * (13 - i) for i, ch in enumerate(base))
    r = total % 11
    if r == 0:
        return "0"
    d = 11 - r
    return "A" if d == 10 else str(d)


print("\n== RFC check digits")
for rfc in ["ASP170418QK2", "TIM190923HB6", "TCG150817KP2", "TCG150817KP7", "PDL180305MN4",
            "SDN210611RT8", "AVA160229LC1", "CDS200114FG3", "CPA141120JS5"]:
    cd = check_digit(rfc)
    print(f"{rfc}: expected check digit={cd} actual={rfc[-1]} valid={cd == rfc[-1]}")
