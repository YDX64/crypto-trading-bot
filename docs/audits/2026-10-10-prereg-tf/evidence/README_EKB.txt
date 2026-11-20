E17 evidence/ index (PLAN Ek B). Public-repo safe: no server SCALPER_* dump, no provenance.scalper_config.

Scratchpad root "SP" = the planning scratchpad (not kept); "SP/e17" = SP/e17. Paths below are origin -> evidence path.

Plain copies (scripts and .txt outputs)
  SP/e17/stat2/pipe_power3.py, pipe_power2.py, out3_fine.txt, out3_main.txt  -> stat2/
  SP/e17/stat2/g12probe.py, mc_size.py, mc_size_687.txt, mc_size_365.txt     -> stat2/
  SP/rev_g12.py, SP/rev_fee2.py, SP/rev_mc.py                                 -> ./ (top level; Ek B lists them as SP/rev_*.py, they live one level above SP/e17)
  SP/e17/rev_sim/boot.py, dlcount.py, uniq.py, amb.py                         -> rev_sim/
  SP/e17/stat/measure.py, atrsig.py, btc_months.py, run_measure.sh            -> stat/
  SP/rev_btc/BTCUSDT-1d-2023-01 .. 2025-12 .zip (public Binance market data,  -> rev_btc/
      the independent regime-label recomputation inputs; no script was kept there)
  SP/e17/fix/gen_variants.py, verify_variants2.py                              -> fix/
  pre-lock tooling written for this lock step                                  -> prelock/
      btc1d_fill.py (BTCUSDT 1d cache fill via fill_klines_cache_from_vision internals),
      make_cal_constants.py, archive_probe.py
  test / verification / rehearsal summaries                                    -> e17_tests_prelock.txt,
      verify_variants_prelock.txt, fill_prova.txt

Bulk archive ekb_bulk.tgz (PnL-bearing or bulky JSON; extract with `tar -xzf`; paths under e17/)
  e17/stat/m_*.json            CAL measurement outputs (20 files; signal-time ATR% + counts, no PnL fields)
  e17/stat/mlog_*/             their harness logs
  e17/runs/*                   60 pre-draft classic-window reports (AYI/YATAY/BOGA) -- PnL INCLUDED (PLAN 12.12)
  e17/lens-minimal/t_runs3/*   T_B15_{Z0,dly,fee} reports + ledger.jsonl
  e17/stat2/res3_*.json, res_*_*.json   power-simulation raw outputs
  Sanitization: 63 report JSONs had provenance.scalper_config replaced by
  "scalper_config_removed": "herkese açık repo: sunucu ayar dökümü çıkarıldı" (E16 precedent).
  Consequence: the sha256 values in e17/lens-minimal/t_runs3/ledger.jsonl refer to the ORIGINAL (unstripped) reports and
  no longer match the stripped copies. Excluded on purpose: env_*.txt, dummy.env (carry the server snapshot / secret-shaped keys).
  Archive is deterministic (sorted, fixed mtime, uid/gid 0, gzip -n).
