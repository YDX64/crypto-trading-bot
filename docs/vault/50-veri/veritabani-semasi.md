---
tags: [veri, veritabani, sqlite, sema]
guncelleme: 2026-08-24
kaynak: src/models/scalp_trade.py, src/models/position.py, src/core/database.py
---
# Veritabani semasi

## Baglanti

`DATABASE_URL` varsayilani `sqlite:///./tradingbot.db`
(`src/core/config.py:134`). SQLite icin **WAL** modu, `synchronous=NORMAL`,
`foreign_keys=ON`, `busy_timeout=5000` (`src/core/database.py:29` blogu).

**Idempotent migration:** `create_all` mevcut tabloya yeni sutun EKLEMEZ;
eksik sutunlar `src/core/database.py:76` icinde tamamlanir
(`entry_order_id`, `tp3_algo_id`, `forensics`).

## ⚠️ EN SIK TUZAK: `scalp_trades` ≠ `positions`

| Tablo | Model | Ne tutar |
|---|---|---|
| **`scalp_trades`** | `src/models/scalp_trade.py:16` | **Scalper (ve AlgoPro takipci) islem defteri** — ACIK + KAPANMIS, strateji etiketli, ROI/MAE/MFE dahil |
| `positions` | `src/models/position.py:35` | **AYRI tablo** — eski Telegram/orchestrator akisinin CANLI pozisyon durumu |
| `signals` | `src/models/signal.py:35` | eski sinyal akisi |
| `waiting_signals` / `indicator_snapshots` / `waiting_mode_config` | `src/models/waiting_signal.py:26` · `:92` · `:124` | bekleme modu |

> "Pozisyonlari sorgula" denince `positions` tablosuna bakmak **yanlistir**;
> scalper defteri `scalp_trades`'tedir.

## `scalp_trades` kolonlari

| Kolon | Tip | Not |
|---|---|---|
| `id` | INT PK | |
| `strategy` | STR idx | `"A"|"B"|"C"` … ve **`"AP"` = AlgoPro takipci** |
| `symbol` · `direction` | STR | `LONG`/`SHORT` |
| `entry_price` · `exit_price` · `quantity` · `leverage` · `margin_usdt` | FLOAT | |
| `realized_pnl` · `roi_pct` | FLOAT | |
| `exit_reason` | STR | `SL` · `TP_LADDER` · `TRAIL` · **`TRAIL_MARKET`** · **`BE_MARKET`** · `RISK_EVENT` · `TV_EVENT` · `MANUAL` · `UNKNOWN` |
| `signal_reason` | STR | giris gerekcesi (takipcide `lev`/`sl_pct`/`sl_roi`/`margin` de burada) |
| `mae_pct` · `mfe_pct` | FLOAT | en kotu / en iyi uc |
| `status` | STR idx | `OPEN` · `CLOSED` · **`SHADOW`** |
| `opened_at` · `closed_at` | DATETIME | |
| `sl_algo_id` · `tp1_algo_id` · `tp2_algo_id` | STR | algo emir kimlikleri |
| `tp3_algo_id` | STR | **yalniz takipci**; scalper NULL birakir |
| `entry_order_id` | STR | **KALICI** — restart sonrasi income/userTrades dogrulamasi icin |
| `notes` | STR | `exit_fill=…`, `close_verification=…`, `shadow_mode` … |
| `forensics` | TEXT/JSON | D21 belgesi; **eski satirlarda NULL**, hicbir karar yolu OKUMAZ |

## Tuzaklar

- **`SHADOW` satirlari istatistiklerden kendiliginden dislanir**
  (`stats()`/`open_trades()` yalniz `CLOSED`/`OPEN` sorgular);
  `/scalper/trades` varsayilani da gostermez → `?include_shadow=1`.
- **`strategy="AP"` satirlarini scalper istatistigine karistirma.**
- **`forensics` NULL ≠ temiz** — D21 oncesi islemler olculmemistir.
- Golge halkasinin defteri **AYRI dosyadir** (`tradingbot_shadow.db`);
  ayri takipci halkasinin defteri `tradingbot_ap.db`.

ILGILI: [[10-mimari/defter-ve-muhasebe]] · [[50-veri/metrikler]] · [[50-veri/loglar]] · [[20-kararlar/D21-islem-adli-kaydi]]
