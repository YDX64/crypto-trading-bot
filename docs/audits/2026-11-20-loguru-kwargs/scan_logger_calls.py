"""Envanter: v2/src altındaki loguru çağrılarını AST ile tara (D45).

Kullanım (repo kökünden):
  python3 -I docs/audits/2026-11-20-loguru-kwargs/scan_logger_calls.py src \\
      docs/audits/2026-11-20-loguru-kwargs/envanter.md

Çıktı dosyası (başlık dahil) BU betiğin ürettiği metindir; yeniden üretmek onu aynen yeniler.

Sınıflandırma kuralları (rapordaki sayıların tanımı):
  kwargs'lı        : çağrıda `exc_info=` / `extra=` / başka kwarg var (loguru bunları `str.format`'a verir)
  serbest metin    : f-string/concat içine istisna, neden, webhook/API metni gömülü (süslü parantez taşıyabilir);
                     sayı/sembol/etiket gömenler "düz" sayılır. Değişken ADINA göre belirlenir: sezgiseldir
  except içinde    : çağrı bir `except` bloğunun içinde
  ardından         : log çağrısından SONRA, aynı `except` bloğunda çalışacak ifadeler. Çağrının bulunduğu bloktaki
                     sonraki kardeş ifadeler ve (üst bloklara çıkarak) işleyicinin sonuna kadar her kapsayıcı bloğun
                     sonraki ifadeleri sayılır; başka dallardaki (`else` vb.) ifadeler sayılmaz.
                       - güvenlik eylemi : koruma emri, acil kapatma, iptal, entry-halt mandalı, tmp dosya temizliği,
                                           rezervasyon/`set_status` gibi çağrılar ya da atamalar
                       - yalnız çıkış    : sonraki ifadelerin HEPSİ `raise` / `return` / `continue` / `break`
                       - çıkış + diğer   : çıkış var, ama güvenlik eylemi olmayan başka ifade de var
                       - hiçbiri         : log çağrısı işleyicinin son ifadesidir (istisna çağırana kaçar)
  döngü içinde     : except bloğu aynı fonksiyondaki bir while/for döngüsünün içinde (log patlarsa döngü/görev ölür)
"""
import ast
import collections
import hashlib
import re
import sys
from pathlib import Path

LEVELS = {"trace", "debug", "info", "success", "warning", "error", "critical", "exception", "log"}
FREE = re.compile(
    r"^(e|e\d|ex|exc|err|error|cancel_error|last_error|reason|message|msg|detail|halt_reason|_entry_halt_reason)$"
)
ACTION = re.compile(
    r"(place_stop|emergency|close_position|_close_market|cancel|_entry_halted\s*=|_entry_halt_reason\s*="
    r"|unlink|os\.replace|set_status|remove|release|reserve)"
)
EXITS = (ast.Raise, ast.Return, ast.Continue, ast.Break)
PRIORITY = [
    "strategies/scalper/executor.py",
    "strategies/scalper/exits.py",
    "strategies/scalper/engine.py",
    "trading/position_manager.py",
    "services/orchestrator.py",
]


def is_free_text(expr: str) -> bool:
    base = re.split(r"[:!]", expr)[0].strip()
    root = re.sub(r"\[.*", "", base)
    if FREE.match(root.split(".")[-1]):
        return True
    if re.search(r"\[['\"](detail|reason|message|msg|error)['\"]\]", base):
        return True
    return bool(re.match(r"^(str|repr)\((e|exc|err|error)\)$", base))


def msg_kind(arg) -> str:
    if arg is None:
        return "none"
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return "const"
    if isinstance(arg, ast.JoinedStr):
        return "fstring" if any(isinstance(v, ast.FormattedValue) for v in arg.values) else "const"
    if isinstance(arg, ast.BinOp):
        return "concat/percent"
    return "other"


def fvalues(arg):
    if arg is None or isinstance(arg, ast.Constant):
        return []
    return [ast.unparse(n.value) for n in ast.walk(arg) if isinstance(n, ast.FormattedValue)]


def following_statements(call, parents, handler):
    """Log çağrısından sonra, `handler` içinde çalışacak ifadeler (aynı dalda; üst bloklara çıkarak)."""
    cur = call
    while cur in parents and not isinstance(cur, ast.stmt):
        cur = parents[cur]  # çağrıyı içeren en yakın ifade
    out = []
    while cur is not handler and cur in parents:
        parent = parents[cur]
        for _, value in ast.iter_fields(parent):
            if isinstance(value, list) and any(item is cur for item in value):
                idx = next(i for i, item in enumerate(value) if item is cur)
                out.extend(s for s in value[idx + 1:] if isinstance(s, ast.stmt))
                break
        cur = parent
    return out


def classify_following(stmts):
    """(güvenlik eylemi izleri, çıkış türleri, yalnız çıkış mı) — iç içe fonksiyon tanımlarına girmez."""
    only_exit = bool(stmts) and all(isinstance(s, EXITS) for s in stmts)
    actions, exits = [], []
    stack = list(stmts)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, EXITS):
            exits.append(type(node).__name__.lower())
        if isinstance(node, ast.stmt) and not isinstance(node, ast.Pass):
            hit = ACTION.search(ast.unparse(node).split("\n")[0])
            if hit:
                actions.append(hit.group(1))
        stack.extend(ast.iter_child_nodes(node))
    return sorted(set(actions)), sorted(set(exits)), only_exit


def scan_file(path: Path, rel: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    rows = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS):
            continue
        recv = ast.unparse(node.func.value)
        if "logger" not in recv.lower() and recv not in ("log", "self.log"):
            continue
        args = node.args[1:] if node.func.attr == "log" else node.args
        msg = args[0] if args else None
        kws = [k.arg or "**" for k in node.keywords]
        handler, loop, fn = None, False, "<module>"
        n = node
        while n in parents:
            n = parents[n]
            if handler is None and isinstance(n, ast.ExceptHandler):
                handler = n
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = n.name
                break
        if handler is not None:
            m = handler
            while m in parents:
                m = parents[m]
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                if isinstance(m, (ast.While, ast.For, ast.AsyncFor)):
                    loop = True
                    break
        following = following_statements(node, parents, handler) if handler is not None else []
        actions, exits, only_exit = classify_following(following) if following else ([], [], False)
        exprs = fvalues(msg)
        rows.append(
            {
                "file": rel, "line": node.lineno, "level": node.func.attr, "fn": fn, "kwargs": kws,
                "npos": max(0, len(args) - 1), "kind": msg_kind(msg), "in_except": handler is not None,
                "in_loop": loop, "n_after": len(following), "actions": actions, "exits": exits, "only_exit": only_exit,
                "exprs": exprs, "free": any(is_free_text(e) for e in exprs),
            }
        )
    return rows


def tree_label(root: Path) -> str:
    """Taranan `*.py` dosyalarının içerik özeti: git'e bağlı DEĞİL, kirli ağaçta da yeniden üretilebilir."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return f"`{root.name}/` altındaki `*.py` dosyalarının içerik özeti `sha256:{digest.hexdigest()[:12]}`"


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for p in sorted(root.rglob("*.py")):
        rows.extend(scan_file(p, str(p.relative_to(root))))
    kw = [r for r in rows if r["kwargs"]]
    ex = [r for r in kw if r["in_except"]]
    fx = [r for r in ex if r["free"]]
    lines = []
    w = lines.append
    w("# loguru kwargs envanteri (D45)\n")
    w(f"Taranan ağaç: {tree_label(root)}. Bu dosya `scan_logger_calls.py`'nin çıktısıdır; yeniden üretmek onu aynen yeniler:\n"
      "`python3 -I docs/audits/2026-11-20-loguru-kwargs/scan_logger_calls.py src docs/audits/2026-11-20-loguru-kwargs/envanter.md`\n"
      "Karar ve gerekçe: `docs/DECISIONS.md` D45. Sınıflandırma kuralları betiğin başındaki açıklamadadır "
      "(serbest metin değişken adına göre belirlenir: sezgiseldir).\n")
    w(f"- toplam log çağrısı: **{len(rows)}**; kwargs'lı: **{len(kw)}** "
      f"({sum('extra' in r['kwargs'] for r in kw)} `extra=`, {sum('exc_info' in r['kwargs'] for r in kw)} `exc_info=`); "
      f"başka kwarg: {sum(1 for r in kw for k in r['kwargs'] if k not in ('extra', 'exc_info'))}")
    w(f"- konumsal argümanlı çağrı (mesajdan sonra): {sum(1 for r in rows if r['npos'])} "
      f"(mesaj türleri: {dict(collections.Counter(r['kind'] for r in rows if r['npos']))})")
    w(f"- kwargs'lı çağrıda mesaj türü: {dict(collections.Counter(r['kind'] for r in kw))}")
    w(f"- serbest metin taşıyan (istisna/neden/webhook): **{sum(r['free'] for r in kw)}**")
    w(f"- `except` içinde: **{len(ex)}**; bunlardan serbest metinli: **{len(fx)}**")
    w(f"  - ardından güvenlik eylemi olan: **{sum(1 for r in fx if r['actions'])}**")
    w(f"  - ardından yalnız çıkış (`raise`/`return`/`continue`/`break`) olan: "
      f"**{sum(1 for r in fx if not r['actions'] and r['only_exit'])}**")
    w(f"  - ardından çıkış + güvenlik eylemi olmayan başka ifade olan: "
      f"{sum(1 for r in fx if not r['actions'] and r['exits'] and not r['only_exit'])}")
    w(f"  - ardından başka ifade olan (eylem ya da çıkış dahil): {sum(1 for r in fx if r['n_after'])}")
    w(f"  - döngü içinde (log patlarsa görev ölür): **{sum(r['in_loop'] for r in fx)}**")
    w(f"  - log çağrısı işleyicinin son ifadesi (istisna çağırana kaçar): {sum(1 for r in fx if r['n_after'] == 0)}\n")
    w("## Öncelikli dosyalar\n\n| dosya | kwargs'lı | serbest metin | except+serbest |\n|---|---|---|---|")
    for f in PRIORITY:
        q = [r for r in kw if r["file"] == f]
        w(f"| {f} | {len(q)} | {sum(r['free'] for r in q)} | {sum(1 for r in q if r['in_except'] and r['free'])} |")
    w("\n## Diğer dosyalar\n\n| dosya | kwargs'lı | serbest metin | except+serbest |\n|---|---|---|---|")
    other = collections.defaultdict(lambda: [0, 0, 0])
    for r in kw:
        if r["file"] in PRIORITY:
            continue
        o = other[r["file"]]
        o[0] += 1
        o[1] += r["free"]
        o[2] += r["in_except"] and r["free"]
    for f, v in sorted(other.items(), key=lambda x: (-x[1][0], x[0])):
        w(f"| {f} | {v[0]} | {v[1]} | {v[2]} |")
    w("\n## except içindeki serbest metinli çağrılar (satır satır)\n")
    w("| dosya:satır | fonksiyon | seviye | kwarg | gömülü | döngü | ardından |\n|---|---|---|---|---|---|---|")
    for r in sorted(fx, key=lambda r: (r["file"], r["line"])):
        if r["actions"]:
            after = ", ".join(r["actions"] + r["exits"])
        elif r["only_exit"]:
            after = "yalnız " + ", ".join(r["exits"])
        elif r["exits"]:
            after = "diğer ifade + " + ", ".join(r["exits"])
        else:
            after = "log son ifade" if r["n_after"] == 0 else "diğer ifade"
        w(f"| {r['file']}:{r['line']} | {r['fn']} | {r['level']} | {','.join(r['kwargs'])} | "
          f"{', '.join(r['exprs'][:3])} | {'evet' if r['in_loop'] else ''} | {after} |")
    w("\n## except DIŞINDA serbest metinli kwargs'lı çağrılar\n")
    for r in sorted((r for r in kw if r["free"] and not r["in_except"]), key=lambda r: (r["file"], r["line"])):
        w(f"- {r['file']}:{r['line']} {r['level']} {r['kwargs']} gömülü={r['exprs'][:3]}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:16]))


if __name__ == "__main__":
    main()
