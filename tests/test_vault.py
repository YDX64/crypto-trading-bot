"""Obsidian kasasi (`docs/vault/`) butunluk testleri.

Kasa, yeni bir yapay zekanin/muhendisin sisteme dogru bir zihinsel modelle
girmesi icindir. Bir dokumantasyon seti ancak **kirilabildigi** olcude
guvenilirdir; bu modul su seyleri kilitler:

1. Her `[[wiki-link]]` gercek bir nota cozuluyor (olu bag yok).
2. Her notta YAML frontmatter var (`tags`, `guncelleme`, `kaynak`).
3. Giris kapisi (`00-BASLA-BURADAN.md`) tum ust klasorlere bag veriyor.
4. Notlardaki `dosya:satir` referanslari gercek — dosya var ve satir
   numarasi dosyanin icinde kaliyor.

Bu testler AGSIZDIR ve yalniz depo dosyalarini okur.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT = REPO_ROOT / "docs" / "vault"

# [[hedef]] · [[hedef|takma ad]] · [[hedef\|takma ad]] (Markdown tablosunda)
WIKILINK_RE = re.compile(r"\[\[([^\]\[|]+?)(?:\\?\|[^\]\[]*)?\]\]")

# Yalniz depo icindeki bilinen ust dizinlere isaret eden yollari yakala;
# "localhost:9091" ya da "state/x.json" gibi seyleri KASITLI olarak dislar.
FILEREF_RE = re.compile(
    r"(?<![\w/.\-])"
    r"((?:src|tests|scripts|docs)/[A-Za-z0-9_./\-]+"
    r"\.(?:py|md|sh|json|ya?ml|html|txt|ini|cfg))"
    r"(?::(\d+)(?:-(\d+))?)?"
)

REQUIRED_FRONTMATTER_KEYS = ("tags", "guncelleme", "kaynak")
ENTRY_NOTE = "00-BASLA-BURADAN"


def _notes() -> List[Path]:
    assert VAULT.is_dir(), "kasa dizini yok: docs/vault"
    return sorted(VAULT.rglob("*.md"))


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _link_index() -> Dict[str, Path]:
    """Hem 'not-adi' hem 'klasor/not-adi' bicimini cozen indeks."""
    index: Dict[str, Path] = {}
    for note in _notes():
        rel = note.relative_to(VAULT).with_suffix("").as_posix()
        index[rel] = note
        # Kisa ad (Obsidian'in "shortest path when possible" davranisi).
        index.setdefault(note.stem, note)
    return index


FENCE_RE = re.compile(r"```.*?```", flags=re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_code(text: str) -> str:
    """Kod bloklarini/inline kodu duser.

    README'de bag SOZDIZIMI ornek olarak gosterilir (`[[not-adi]]`); bunlar
    gercek bag degildir ve cozulmeleri BEKLENMEZ.
    """
    text = FENCE_RE.sub("", text)
    return INLINE_CODE_RE.sub("", text)


def _links_in(text: str) -> List[str]:
    targets: List[str] = []
    for raw in WIKILINK_RE.findall(_strip_code(text)):
        target = raw.strip().rstrip("\\").strip()
        target = target.split("#", 1)[0].strip()
        if target:
            targets.append(target)
    return targets


def _frontmatter(text: str) -> Tuple[bool, str]:
    if not text.startswith("---\n"):
        return False, "dosya '---' ile baslamiyor"
    end = text.find("\n---", 4)
    if end == -1:
        return False, "frontmatter kapanis '---' bulunamadi"
    return True, text[4:end]


def test_vault_dizini_ve_notlar_var():
    notes = _notes()
    assert len(notes) >= 40, "kasada beklenenden az not var: %d" % len(notes)
    for folder in ("10-mimari", "20-kararlar", "30-deneyler", "40-isletme",
                   "50-veri", "90-ai-icin"):
        assert (VAULT / folder).is_dir(), "eksik klasor: %s" % folder


def test_not_adlari_benzersiz():
    """Kisa ad (`[[not-adi]]`) belirsiz kalmamali.

    Obsidian kisa adi 'en kisa yol' ile cozer; ayni ada sahip iki not,
    bagin sessizce YANLIS nota gitmesine yol acar.
    """
    gorulen: Dict[str, List[str]] = {}
    for note in _notes():
        gorulen.setdefault(note.stem, []).append(_rel(note))
    cakisan = {k: v for k, v in gorulen.items() if len(v) > 1}
    assert not cakisan, "Ayni ada sahip notlar: %s" % cakisan


def test_her_notta_frontmatter_var():
    """(b) tags / guncelleme / kaynak alanlari zorunlu."""
    hatalar: List[str] = []
    for note in _notes():
        text = note.read_text(encoding="utf-8")
        ok, body = _frontmatter(text)
        if not ok:
            hatalar.append("%s: %s" % (_rel(note), body))
            continue
        for key in REQUIRED_FRONTMATTER_KEYS:
            if not re.search(r"^%s\s*:" % key, body, flags=re.MULTILINE):
                hatalar.append("%s: frontmatter'da '%s' yok" % (_rel(note), key))
    assert not hatalar, "Frontmatter hatalari:\n" + "\n".join(hatalar)


def test_her_wikilink_gercek_bir_nota_cozuluyor():
    """(a) olu bag yok."""
    index = _link_index()
    hatalar: List[str] = []
    toplam = 0
    for note in _notes():
        text = note.read_text(encoding="utf-8")
        for target in _links_in(text):
            toplam += 1
            if target not in index:
                hatalar.append("%s: [[%s]] cozulemedi" % (_rel(note), target))
    assert not hatalar, "Olu wiki-link:\n" + "\n".join(hatalar)
    assert toplam >= 100, "kasada beklenenden az bag var: %d" % toplam


def test_giris_kapisi_tum_ust_klasorlere_bag_veriyor():
    """(c) 00-BASLA-BURADAN her ust klasore en az bir bag icermeli."""
    entry = VAULT / ("%s.md" % ENTRY_NOTE)
    assert entry.is_file(), "giris kapisi notu yok"
    index = _link_index()
    text = entry.read_text(encoding="utf-8")

    kapsanan: Set[str] = set()
    for target in _links_in(text):
        hedef = index.get(target)
        if hedef is None:
            continue
        rel = hedef.relative_to(VAULT)
        if len(rel.parts) > 1:
            kapsanan.add(rel.parts[0])

    beklenen = {
        p.name for p in VAULT.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    }
    eksik = sorted(beklenen - kapsanan)
    assert not eksik, "giris kapisi su klasorlere bag vermiyor: %s" % eksik


def test_dosya_satir_referanslari_gercek():
    """(d) referans verilen dosya var ve satir numarasi dosyanin icinde."""
    hatalar: List[str] = []
    kontrol_edilen = 0
    satir_sayisi: Dict[str, int] = {}

    for note in _notes():
        text = note.read_text(encoding="utf-8")
        for rel_path, line_a, line_b in FILEREF_RE.findall(text):
            kontrol_edilen += 1
            target = REPO_ROOT / rel_path
            if not target.is_file():
                hatalar.append("%s: '%s' dosyasi YOK" % (_rel(note), rel_path))
                continue
            if not line_a:
                continue
            if rel_path not in satir_sayisi:
                with target.open("r", encoding="utf-8", errors="replace") as fh:
                    satir_sayisi[rel_path] = sum(1 for _ in fh)
            toplam = satir_sayisi[rel_path]
            for satir in (line_a, line_b):
                if not satir:
                    continue
                if int(satir) < 1 or int(satir) > toplam:
                    hatalar.append(
                        "%s: '%s:%s' dosya disinda (dosya %d satir)"
                        % (_rel(note), rel_path, satir, toplam)
                    )

    assert not hatalar, (
        "Gecersiz dosya:satir referanslari:\n" + "\n".join(hatalar)
    )
    assert kontrol_edilen >= 50, (
        "beklenenden az dosya referansi bulundu: %d" % kontrol_edilen
    )


def test_yetim_not_yok():
    """Giris kapisi ve README disindaki her not en az bir yerden baglanmali."""
    index = _link_index()
    baglanan: Set[Path] = set()
    for note in _notes():
        text = note.read_text(encoding="utf-8")
        for target in _links_in(text):
            hedef = index.get(target)
            if hedef is not None and hedef != note:
                baglanan.add(hedef)

    muaf = {VAULT / "README.md", VAULT / ("%s.md" % ENTRY_NOTE)}
    yetim = sorted(
        _rel(n) for n in _notes() if n not in baglanan and n not in muaf
    )
    assert not yetim, "Hicbir nottan baglanmayan notlar:\n" + "\n".join(yetim)
