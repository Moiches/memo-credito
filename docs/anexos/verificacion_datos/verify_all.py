"""Deterministic verification of the challenge data (exploration script, not the deliverable).

Usage: python -I -X utf8 verify_all.py <solicitudes_dir>
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, getcontext
from pathlib import Path

import pdfplumber

getcontext().prec = 34
CENT = Decimal("0.01")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONEY_RE = re.compile(r"^-?\$-?[\d,]+\.\d{2}$")
INJECTION_RE = re.compile(
    r"(ignora|ignore|instrucci|sistema de an[aá]lisis|recomienda|aprobar|aprueba|prompt|"
    r"sin observaciones|olvida|system|assistant|you are|eres un)",
    re.IGNORECASE,
)


def money(text: str) -> Decimal:
    neg = "-" in text
    v = Decimal(text.replace("$", "").replace(",", "").replace("-", ""))
    return -v if neg else v


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Movement:
    fecha: str
    concepto: str
    cargo: Decimal | None
    abono: Decimal | None
    saldo: Decimal
    page: int
    line_top: float
    n_lines: int


@dataclass
class Statement:
    file: str
    pages: int
    titular: str = ""
    rfc: str = ""
    cuenta: str = ""
    periodo: str = ""
    saldo_inicial: Decimal = Decimal(0)
    total_abonos: Decimal = Decimal(0)
    total_cargos: Decimal = Decimal(0)
    saldo_final: Decimal = Decimal(0)
    movements: list[Movement] = field(default_factory=list)
    white_text: list[str] = field(default_factory=list)
    other_colors: dict[str, str] = field(default_factory=dict)


def group_lines(words: list[dict], tol: float = 2.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def parse_statement(path: Path) -> Statement:
    st = Statement(file=path.name, pages=0)
    col_cargo = col_abono = col_saldo = None
    current: dict | None = None
    raw_moves: list[dict] = []
    with pdfplumber.open(path) as pdf:
        st.pages = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(extra_attrs=["fontname", "size", "non_stroking_color"])
            # colors audit
            for w in words:
                color = w["non_stroking_color"]
                if color == (1.0, 1.0, 1.0) or color == [1.0, 1.0, 1.0]:
                    st.white_text.append(w["text"])
                elif color not in ((0.0, 0.0, 0.0), (0.501961, 0.501961, 0.501961), (0, 0, 0), (0,)):
                    st.other_colors[str(color)] = st.other_colors.get(str(color), "") + " " + w["text"]
            # header columns (white bold header row)
            for w in words:
                if w["fontname"].endswith("Bold") and w["text"] in ("Cargo", "Abono", "Saldo") and w["top"] > 180:
                    if w["text"] == "Cargo":
                        col_cargo = w["x0"]
                    elif w["text"] == "Abono":
                        col_abono = w["x0"]
                    elif w["text"] == "Saldo":
                        col_saldo = w["x0"]
            lines = group_lines(words)
            for i, line in enumerate(lines):
                texts = [w["text"] for w in line]
                joined = " ".join(texts)
                if "Oblique" in line[0]["fontname"]:  # footer
                    continue
                if pno == 1 and joined.startswith("Titular "):
                    st.titular = " ".join(texts[1:])
                elif pno == 1 and joined.startswith("RFC "):
                    st.rfc = texts[1]
                elif pno == 1 and joined.startswith("Cuenta "):
                    st.cuenta = texts[1]
                elif pno == 1 and joined.startswith("Periodo "):
                    st.periodo = " ".join(texts[1:])
                elif pno == 1 and len(texts) == 4 and all(MONEY_RE.match(t) for t in texts) and line[0]["top"] < 200:
                    st.saldo_inicial, st.total_abonos, st.total_cargos, st.saldo_final = (money(t) for t in texts)
                elif DATE_RE.match(texts[0]) and line[0]["x0"] < 100:
                    if current:
                        raw_moves.append(current)
                    current = {"fecha": texts[0], "concepto": [], "amounts": [], "page": pno,
                               "top": line[0]["top"], "n_lines": 1}
                    for w in line[1:]:
                        if MONEY_RE.match(w["text"]) and w["x0"] > 300:
                            current["amounts"].append((w["x1"], money(w["text"])))
                        else:
                            current["concepto"].append(w["text"])
                elif current is not None and line[0]["top"] > 200 or (current is not None and pno > 1):
                    # continuation line (multi-line concept or amounts on another line)
                    if any(w["fontname"].endswith("Bold") for w in line):
                        continue  # repeated header on later page
                    current["n_lines"] += 1
                    for w in line:
                        if MONEY_RE.match(w["text"]) and w["x0"] > 300:
                            current["amounts"].append((w["x1"], money(w["text"])))
                        else:
                            current["concepto"].append(w["text"])
        if current:
            raw_moves.append(current)
    assert col_cargo and col_abono and col_saldo, f"columns not found in {path}"
    for m in raw_moves:
        cargo = abono = saldo = None
        for x1, amt in m["amounts"]:
            if x1 <= col_abono:
                cargo = amt
            elif x1 <= col_saldo:
                abono = amt
            else:
                saldo = amt
        st.movements.append(Movement(m["fecha"], " ".join(m["concepto"]), cargo, abono, saldo,
                                     m["page"], m["top"], m["n_lines"]))
    return st


def parse_kv_doc(path: Path) -> tuple[str, int, dict]:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        return text, len(pdf.pages), {}


def parse_constancia(path: Path) -> dict:
    text, pages, _ = parse_kv_doc(path)
    out = {"pages": pages}
    pats = {
        "fecha_emision": r"Fecha de emisión:\s*(\d{4}-\d{2}-\d{2})",
        "rfc": r"^RFC\s+(\S+)",
        "razon_social": r"Denominación o razón social\s+(.+)$",
        "regimen_capital": r"Régimen de capital\s+(.+)$",
        "regimen_fiscal": r"Régimen fiscal\s+(.+)$",
        "inicio_operaciones": r"Fecha de inicio de operaciones\s+(\d{4}-\d{2}-\d{2})",
        "estatus": r"Estatus en el padrón\s+(\S+)",
        "domicilio": r"Domicilio fiscal\s+(.+)$",
        "actividad": r"Actividad económica\s+(.+)$",
    }
    for k, p in pats.items():
        m = re.search(p, text, re.MULTILINE)
        out[k] = m.group(1).strip() if m else None
    out["_text"] = text
    return out


def parse_eeff(path: Path) -> dict:
    text, pages, _ = parse_kv_doc(path)
    out = {"pages": pages}
    lines = text.splitlines()
    out["razon_social"] = lines[0].strip()
    m = re.search(r"RFC:\s*(\S+)", text)
    out["rfc"] = m.group(1) if m else None
    m = re.search(r"al (\d+ de \w+ de \d{4})", text)
    out["fecha_cierre"] = m.group(1) if m else None
    items = {}
    for line in lines:
        m = re.match(r"^(.+?)\s+(-?\$-?[\d,]+\.\d{2})$", line.strip())
        if m:
            items[m.group(1)] = money(m.group(2))
    out["items"] = items
    out["dictamen"] = next((l for l in lines if "dictamin" in l.lower()), None)
    out["_text"] = text
    return out


def pago_mensual(monto: Decimal, n: int, i: Decimal = Decimal("0.02")) -> Decimal:
    return monto * i / (1 - (1 + i) ** (-n))


def main(root: Path) -> None:
    summary = []
    for sol_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        folio = sol_dir.name
        sol = json.loads((sol_dir / "solicitud.json").read_text(encoding="utf-8"))
        print("=" * 100)
        print(f"{folio}  fecha_solicitud={sol['fecha_solicitud']}  top-level keys={list(sol)}")
        files = sorted(f.name for f in sol_dir.iterdir())
        listed = set(sol["documentos"])
        print(f"  files={files}")
        print(f"  json.documentos missing on disk={sorted(listed - set(files))} on disk not listed={sorted(set(files) - listed - {'solicitud.json'})}")
        stmts = [parse_statement(f) for f in sorted(sol_dir.glob("estado_cuenta_*.pdf"))]
        flows: dict[str, Decimal] = {}
        prev_final = None
        all_abonos: list[tuple[str, Decimal, str]] = []
        for st in stmts:
            sum_ab = sum((m.abono for m in st.movements if m.abono is not None), Decimal(0))
            sum_ca = sum((m.cargo for m in st.movements if m.cargo is not None), Decimal(0))
            run = st.saldo_inicial
            row_ok = True
            both_or_none = 0
            for m in st.movements:
                if (m.cargo is None) == (m.abono is None):
                    both_or_none += 1
                run = run + (m.abono or 0) - (m.cargo or 0)
                if run != m.saldo:
                    row_ok = False
                if m.abono is not None:
                    all_abonos.append((m.fecha, m.abono, m.concepto))
            month = st.file.replace("estado_cuenta_", "").replace(".pdf", "")
            net = st.total_abonos - st.total_cargos
            flows[month] = net
            chain = "n/a" if prev_final is None else ("OK" if prev_final == st.saldo_inicial else f"BREAK prev_final={prev_final}")
            prev_final = st.saldo_final
            multiline = [m for m in st.movements if m.n_lines > 1]
            print(f"  {st.file}: pages={st.pages} movs={len(st.movements)} titular={st.titular!r} rfc={st.rfc} cuenta={st.cuenta} periodo={st.periodo!r}")
            print(f"     SI={st.saldo_inicial} TA={st.total_abonos} TC={st.total_cargos} SF={st.saldo_final} "
                  f"sumAb_ok={sum_ab == st.total_abonos} sumCa_ok={sum_ca == st.total_cargos} "
                  f"SI+A-C=SF:{st.saldo_inicial + st.total_abonos - st.total_cargos == st.saldo_final} rows_ok={row_ok} "
                  f"chain={chain} bad_rows(both/none)={both_or_none} net={net}")
            pages_with_moves = sorted({m.page for m in st.movements})
            print(f"     pages_with_movements={pages_with_moves} white_text={' '.join(st.white_text)!r} other_colors={st.other_colors}")
            for m in multiline:
                print(f"     MULTILINE ({m.n_lines} lines) {m.fecha} cargo={m.cargo} abono={m.abono} saldo={m.saldo}: {m.concepto!r}")
            for m in st.movements:
                if INJECTION_RE.search(m.concepto):
                    print(f"     INJECTION? {m.fecha} {m.abono or m.cargo}: {m.concepto!r}")
            if st.pages > 1:
                last = st.movements[-1]
                print(f"     last movement page={last.page} {last.fecha} {last.concepto!r} saldo={last.saldo}")
        # constancia / eeff
        cons = parse_constancia(sol_dir / "constancia_situacion_fiscal.pdf")
        eeff = parse_eeff(sol_dir / "estados_financieros_2025.pdf")
        print(f"  CONSTANCIA: { {k: v for k, v in cons.items() if k != '_text'} }")
        print(f"  EEFF: rfc={eeff['rfc']} razon={eeff['razon_social']!r} cierre={eeff['fecha_cierre']} pages={eeff['pages']} dictamen={eeff['dictamen']!r}")
        items = eeff["items"]
        print(f"     items={ {k: str(v) for k, v in items.items()} }")
        # balance identities
        ta, tp, cc = items.get("Total activo"), items.get("Total pasivo"), items.get("Capital contable")
        tpc = items.get("Total pasivo + capital")
        ac, af = items.get("Activo circulante"), items.get("Activo fijo neto")
        pcp, plp = items.get("Pasivo a corto plazo"), items.get("Pasivo a largo plazo")
        print(f"     A=P+C:{ta == tp + cc} TPC==TA:{tpc == ta} AC+AF=TA:{ac + af == ta} PCP+PLP=TP:{pcp + plp == tp}")
        v, cv, ub = items.get("Ventas netas"), items.get("Costo de ventas"), items.get("Utilidad bruta")
        go, uo, gf = items.get("Gastos de operación"), items.get("Utilidad de operación"), items.get("Gastos financieros")
        uai, isr, un = items.get("Utilidad antes de impuestos"), items.get("ISR"), items.get("Utilidad neta")
        print(f"     ER: V-CV=UB:{v - cv == ub} UB-GO=UO:{ub - go == uo} UO-GF=UAI:{uo - gf == uai} UAI-ISR=UN:{uai - isr == un}")
        # RFC / razon social consistency
        rfcs = {"json": sol["solicitante"]["rfc"], "constancia": cons["rfc"], "eeff": eeff["rfc"],
                **{s.file: s.rfc for s in stmts}}
        razones = {"json": sol["solicitante"]["razon_social"], "constancia": cons["razon_social"],
                   "eeff": eeff["razon_social"], **{s.file: s.titular for s in stmts}}
        print(f"  RFCs distinct={set(rfcs.values())} {'' if len(set(rfcs.values())) == 1 else rfcs}")
        print(f"  Razones distinct={set(razones.values())}")
        # expected months
        fs = date.fromisoformat(sol["fecha_solicitud"])
        exp = []
        y, mth = fs.year, fs.month
        for _ in range(3):
            mth -= 1
            if mth == 0:
                y, mth = y - 1, 12
            exp.append(f"{y:04d}-{mth:02d}")
        exp = sorted(exp)
        missing = [m for m in exp if m not in flows]
        # indicators
        monto = Decimal(str(sol["credito"]["monto_solicitado"]))
        n = int(sol["credito"]["plazo_meses"])
        pago_raw = pago_mensual(monto, n)
        pago = q2(pago_raw)
        if missing:
            flujo = None
            cap = None
        else:
            flujo_raw = sum((flows[m] for m in exp), Decimal(0)) / 3
            flujo = q2(flujo_raw)
            cap_raw_unrounded_inputs = flujo_raw / pago_raw
            cap = q2(flujo / pago)
            cap_alt = q2(cap_raw_unrounded_inputs)
        apal = q2(tp / cc)
        print(f"  expected months={exp} missing={missing}")
        print(f"  flows={ {k: str(v) for k, v in flows.items()} }")
        print(f"  FLUJO_PROM={flujo} PAGO={pago} (raw={pago_raw:.10f}) CAP={cap}"
              f"{'' if cap is None else f' (cap from unrounded={cap_alt})'} APAL={apal}")
        # derived facts
        inicio = date.fromisoformat(cons["inicio_operaciones"])
        years = (fs - inicio).days / 365.25
        full_years = fs.year - inicio.year - ((fs.month, fs.day) < (inicio.month, inicio.day))
        pct_ventas = q2(monto / v * 100)
        tot_ab = sum((s.total_abonos for s in stmts), Decimal(0))
        cash = sum((a for _, a, c in all_abonos if "EFECTIVO" in c.upper()), Decimal(0))
        pct_cash = q2(cash / tot_ab * 100) if tot_ab else None
        amounts = [float(a) for _, a, _ in all_abonos]
        med = statistics.median(amounts)
        mean = statistics.mean(amounts)
        sd = statistics.pstdev(amounts)
        q1, q2_, q3 = statistics.quantiles(amounts, n=4)
        iqr = q3 - q1
        atip_iqr = [(f, str(a)) for f, a, c in all_abonos if float(a) > q3 + 1.5 * iqr]
        atip_med = [(f, str(a)) for f, a, c in all_abonos if float(a) > 2 * med]
        atip_3sd = [(f, str(a)) for f, a, c in all_abonos if float(a) > mean + 3 * sd]
        max_ab = max(all_abonos, key=lambda t: t[1])
        hi = max(flows, key=lambda k: flows[k]) if flows else None
        lo = min(flows, key=lambda k: flows[k]) if flows else None
        print(f"  DERIVED: antiguedad_anios_completos={full_years} ({years:.2f}) ventas={v} util_neta={un} "
              f"monto/ventas={pct_ventas}% cash_abonos={cash} ({pct_cash}%) hi={hi} lo={lo}")
        print(f"     abonos n={len(amounts)} median={med:.2f} mean={mean:.2f} sd={sd:.2f} max={max_ab[1]} ({max_ab[0]} {max_ab[2][:60]!r})")
        print(f"     atipicos IQR={atip_iqr} >2xmediana={atip_med} >mean+3sd={atip_3sd}")
        summary.append((folio, flows, flujo, pago, cap, apal))
        # PII scan of json
        unexpected = set(sol) - {"folio", "fecha_solicitud", "solicitante", "credito", "contacto", "documentos"}
        print(f"  unexpected json keys={sorted(unexpected)}  contacto keys={list(sol['contacto'])}")
        non_ascii_email = not sol["contacto"]["correo"].isascii()
        print(f"  contacto.correo non-ascii={non_ascii_email} ({sol['contacto']['correo']!r})")
        print(f"  sha256 constancia={sha256(sol_dir / 'constancia_situacion_fiscal.pdf')[:16]}…")
    print("=" * 100)
    print("SUMMARY")
    for folio, flows, flujo, pago, cap, apal in summary:
        f = " / ".join(str(flows.get(m, "(falta)")) for m in ("2026-07", "2026-08", "2026-09"))
        print(f"{folio} | {f} | {flujo} | {pago} | {cap} | {apal}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
