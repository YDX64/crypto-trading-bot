# loguru kwargs envanteri (D45)

Taranan ağaç: `src/` altındaki `*.py` dosyalarının içerik özeti `sha256:8688973ebd5a`. Bu dosya `scan_logger_calls.py`'nin çıktısıdır; yeniden üretmek onu aynen yeniler:
`python3 -I docs/audits/2026-11-20-loguru-kwargs/scan_logger_calls.py src docs/audits/2026-11-20-loguru-kwargs/envanter.md`
Karar ve gerekçe: `docs/DECISIONS.md` D45. Sınıflandırma kuralları betiğin başındaki açıklamadadır (serbest metin değişken adına göre belirlenir: sezgiseldir).

- toplam log çağrısı: **956**; kwargs'lı: **116** (89 `extra=`, 27 `exc_info=`); başka kwarg: 0
- konumsal argümanlı çağrı (mesajdan sonra): 1 (mesaj türleri: {'const': 1})
- kwargs'lı çağrıda mesaj türü: {'fstring': 111, 'const': 3, 'concat/percent': 2}
- serbest metin taşıyan (istisna/neden/webhook): **53**
- `except` içinde: **39**; bunlardan serbest metinli: **38**
  - ardından güvenlik eylemi olan: **7**
  - ardından yalnız çıkış (`raise`/`return`/`continue`/`break`) olan: **9**
  - ardından çıkış + güvenlik eylemi olmayan başka ifade olan: 1
  - ardından başka ifade olan (eylem ya da çıkış dahil): 22
  - döngü içinde (log patlarsa görev ölür): **18**
  - log çağrısı işleyicinin son ifadesi (istisna çağırana kaçar): 16

## Öncelikli dosyalar

| dosya | kwargs'lı | serbest metin | except+serbest |
|---|---|---|---|
| strategies/scalper/executor.py | 4 | 0 | 0 |
| strategies/scalper/exits.py | 6 | 1 | 0 |
| strategies/scalper/engine.py | 32 | 23 | 14 |
| trading/position_manager.py | 8 | 1 | 0 |
| services/orchestrator.py | 8 | 5 | 5 |

## Diğer dosyalar

| dosya | kwargs'lı | serbest metin | except+serbest |
|---|---|---|---|
| strategies/follower/engine.py | 20 | 12 | 8 |
| strategies/follower/exits.py | 9 | 0 | 0 |
| strategies/follower/executor.py | 8 | 2 | 2 |
| main.py | 4 | 3 | 3 |
| services/signal_queue.py | 3 | 2 | 2 |
| trading/binance_client.py | 3 | 0 | 0 |
| trading/binance_client_improved.py | 3 | 0 | 0 |
| services/waiting_mode/monitor.py | 2 | 2 | 2 |
| strategies/follower/risk_halt.py | 2 | 1 | 1 |
| strategies/scalper/tracker.py | 2 | 0 | 0 |
| services/telegram_bot.py | 1 | 1 | 1 |
| services/tv_confluence.py | 1 | 0 | 0 |

## except içindeki serbest metinli çağrılar (satır satır)

| dosya:satır | fonksiyon | seviye | kwarg | gömülü | döngü | ardından |
|---|---|---|---|---|---|---|
| main.py:532 | lifespan | error | exc_info | label, e | evet | log son ifade |
| main.py:537 | lifespan | error | exc_info | e |  | log son ifade |
| main.py:2764 | manual_signal | error | exc_info | e |  | yalnız raise |
| services/orchestrator.py:269 | recover_open_positions | critical | extra | symbol, e | evet | yalnız continue |
| services/orchestrator.py:361 | _monitoring_loop | error | exc_info | e | evet | diğer ifade |
| services/orchestrator.py:516 | _monitor_single_position | critical | extra | position.symbol, e |  | yalnız raise |
| services/orchestrator.py:681 | _process_signal_locked | critical | extra | e |  | _entry_halt_reason =, _entry_halted =, reserve, set_status, raise |
| services/orchestrator.py:690 | _process_signal_locked | error | exc_info | e |  | set_status, return |
| services/signal_queue.py:83 | process_queue | error | exc_info | e | evet | yalnız break |
| services/signal_queue.py:93 | process_queue | error | exc_info | e | evet | diğer ifade |
| services/telegram_bot.py:278 | start | error | exc_info | e |  | diğer ifade + raise |
| services/waiting_mode/monitor.py:235 | _monitor_loop | error | exc_info | e | evet | diğer ifade |
| services/waiting_mode/monitor.py:353 | _monitor_signal | error | exc_info | waiting_signal_id, e |  | log son ifade |
| strategies/follower/engine.py:309 | _persist_entry_halt | critical | extra | path, exc |  | unlink |
| strategies/follower/engine.py:376 | _attempt_recovery | error | exc_info | exc |  | log son ifade |
| strategies/follower/engine.py:703 | _safety_loop | error | exc_info | exc | evet | log son ifade |
| strategies/follower/engine.py:719 | _safety_loop | error | exc_info | exc | evet | log son ifade |
| strategies/follower/engine.py:732 | _safety_loop | error | exc_info | exc | evet | log son ifade |
| strategies/follower/engine.py:1056 | handle_event | error | exc_info | event.symbol, exc |  | diğer ifade |
| strategies/follower/engine.py:1799 | risk_event_flatten | error | exc_info | message | evet | yalnız continue |
| strategies/follower/engine.py:1886 | _flatten_orphans | error | exc_info | symbol, exc | evet | yalnız continue |
| strategies/follower/executor.py:271 | open_position | critical | extra | symbol, exc |  | place_stop, return |
| strategies/follower/executor.py:686 | _finalize | critical | extra | symbol, exc |  | yalnız return |
| strategies/follower/risk_halt.py:199 | _persist | critical | extra | self.path, exc |  | unlink, return |
| strategies/scalper/engine.py:634 | _load_entry_halt | critical | extra | self._entry_halt_reason |  | log son ifade |
| strategies/scalper/engine.py:670 | _persist_entry_halt | critical | extra | path, e |  | unlink |
| strategies/scalper/engine.py:818 | _persist_risk_event_halt | critical | extra | path, e |  | unlink, return |
| strategies/scalper/engine.py:851 | _cancel_pending_for_risk_event | error | exc_info | cancel_error |  | log son ifade |
| strategies/scalper/engine.py:958 | risk_event_flatten | error | exc_info | msg | evet | yalnız continue |
| strategies/scalper/engine.py:1027 | _attempt_recovery | error | exc_info | e |  | log son ifade |
| strategies/scalper/engine.py:1083 | _exchange_loop | error | exc_info | e | evet | log son ifade |
| strategies/scalper/engine.py:1096 | _handle_user_order_update | error | exc_info | e |  | log son ifade |
| strategies/scalper/engine.py:1315 | _loop | error | exc_info | e | evet | log son ifade |
| strategies/scalper/engine.py:1358 | _safety_loop | error | exc_info | e | evet | log son ifade |
| strategies/scalper/engine.py:2056 | _scan_tick | error | exc_info | symbol, e | evet | log son ifade |
| strategies/scalper/engine.py:4061 | _apply_tv_event_exits | error | exc_info | symbol, e | evet | yalnız continue |
| strategies/scalper/engine.py:4117 | _apply_tv_event_exits | error | exc_info | symbol, e | evet | diğer ifade |
| strategies/scalper/engine.py:4736 | _latch_entry_halt | critical | extra | cancel_error |  | log son ifade |

## except DIŞINDA serbest metinli kwargs'lı çağrılar

- strategies/follower/engine.py:282 critical ['extra'] gömülü=['reason', 'path']
- strategies/follower/engine.py:326 critical ['extra'] gömülü=['source', 'error']
- strategies/follower/engine.py:1623 warning ['extra'] gömülü=['symbol', 'event.kind.upper()', 'amt']
- strategies/follower/engine.py:1737 info ['extra'] gömülü=['symbol', 'reason']
- strategies/scalper/engine.py:623 critical ['extra'] gömülü=['self._entry_halt_reason']
- strategies/scalper/engine.py:1911 critical ['extra'] gömülü=['reason']
- strategies/scalper/engine.py:1922 critical ['extra'] gömülü=['reason']
- strategies/scalper/engine.py:4085 info ['extra'] gömülü=['symbol', "event['detail']", 'action']
- strategies/scalper/engine.py:4217 info ['extra'] gömülü=['symbol', "event['detail']"]
- strategies/scalper/engine.py:4305 warning ['extra'] gömülü=['symbol', "event['detail']", 'state']
- strategies/scalper/engine.py:4322 info ['extra'] gömülü=['symbol', "event['detail']", 'state']
- strategies/scalper/engine.py:4689 critical ['extra'] gömülü=['source', 'error']
- strategies/scalper/engine.py:4701 critical ['extra'] gömülü=['source', 'error']
- strategies/scalper/exits.py:1379 info ['extra'] gömülü=['symbol', 'reason', 'target']
- trading/position_manager.py:454 critical ['extra'] gömülü=['symbol', 'last_error']
