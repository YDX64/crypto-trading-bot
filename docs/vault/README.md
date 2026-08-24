---
tags: [kasa, giris]
guncelleme: 2026-08-24
kaynak: docs/vault/ (kasanin kendisi)
---

# TRADINGBOT bilgi kasasi (Obsidian vault)

Bu klasor **saf Markdown** bir Obsidian kasasidir. Eklenti gerekmez, tema gerekmez.
Amaci tek: **yeni bir yapay zeka ya da muhendis 10 dakikada dogru zihinsel modeli
kursun ve yanlis varsayim yapmasin.**

## 30 saniyede kullanim

| Ne istiyorsun | Nereye git |
|---|---|
| Hicbir sey bilmiyorum, bastan basla | [[00-BASLA-BURADAN]] |
| Sistem nasil calisiyor | [[10-mimari/motor-scalper]] |
| Bu ayar neden boyle | [[20-kararlar/00-karar-indeksi]] |
| Bu sayi nereden geldi | [[30-deneyler/00-deney-indeksi]] |
| Bot bozuldu / deploy edecegim | [[40-isletme/sorun-giderme]] |
| Veri/log/metrik nerede | [[50-veri/veritabani-semasi]] |
| AI olarak neye dikkat etmeliyim | [[90-ai-icin/calisma-kurallari]] |

## Obsidian'da acma

1. Obsidian → **Open folder as vault** → bu klasoru sec: `docs/vault/`.
   (Repo kokunu degil; kok acilirsa `docs/` disindaki .md dosyalari da kasaya girer.)
2. Sag ustten **Graph view** ac. Notlar bol capraz bagli oldugu icin graf
   dogal olarak yedi kumeye ayrilir: mimari · kararlar · deneyler · isletme ·
   veri · AI · giris kapisi.
3. `Ctrl/Cmd+O` ile hizli gecis; not adlari sabit ve ASCII'dir.
4. Obsidian yoksa fark etmez: dosyalar duz Markdown, `[[bag]]` gosterimi
   GitHub'da tiklanabilir olmasa da okunabilir kalir.

## Kurallar (bu kasayi degistirecekler icin)

- **Wiki-link bicimi:** `[[not-adi]]` — uzantisiz. Klasorlu bicim de gecerli:
  `[[10-mimari/emir-yurutme]]`. Takma ad: `[[not-adi|gorunen metin]]`.
- **Her notun basinda YAML frontmatter olmali:** `tags`, `guncelleme`, `kaynak`.
- **Her iddia koddan dogrulanmis olmali.** Dogrulanamayan sey aynen
  "kodda dogrulanamadi" diye yazilir. Uydurma yasak.
- **Dosya:satir referanslari gercek olmali.** `tests/test_vault.py` hem her
  `[[bag]]`in cozuldugunu hem frontmatter'in varligini hem de referans verilen
  dosyanin var oldugunu ve satir numarasinin dosya icinde kaldigini dogrular.
- **Bu kasa kanit DEGILDIR, kanita giden HARITADIR.** Nihai gercek kaynagi:
  `CLAUDE.md`, `docs/DECISIONS.md`, `docs/RUNBOOK.md`, `docs/EXPERIMENTS.md`,
  `docs/ARCHITECTURE.md`, `docs/INTEGRATIONS.md` ve kodun kendisi.

## Testi kosmak

```bash
python3 -m pytest tests/test_vault.py -q
```
