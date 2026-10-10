#!/usr/bin/env bash
# E17 koşu kitaplığı (PLAN §4, §5.2, §6, Ek A) — `source` edilir, tek başına çalışmaz.
#
# Sıkı kip: hata = tüm zincir exit 97 ile durur (hata yutan kuyruk komutu YOK; E16'da hata yalnız
# loglanıyordu). Konsola YALNIZ 'BT OK|FAIL|SKIP', 'CHECK OK|FAIL|SKIP', 'FILL OK|FAIL',
# 'PHASE ...', 'G6 arm sayisi: N' ve 'DRY ...' yazılır: hiçbir PnL/PF/işlem sayısı yankılanmaz (mühür).
# (PLAN §5.2 yalnız 'BT OK|FAIL' der; diğerleri kabul edilen üst kümedir, sonuç taşımaz — yalnız aşama
# durumu / arm adı. CHECK satırını analizci değil bu sarmalayıcı basar.)
# Sunucu snapshot'ının (E17_SNAPSHOT) içeriği HİÇBİR YERE yazılmaz; ERR tuzağı
# BASH_COMMAND'ı BİLEREK basmaz (env -i satırı snapshot değerlerini taşır).
#
# Ortam sözleşmesi (hepsi isteğe bağlı değil ise belirtilmiştir):
#   E17_SNAPSHOT   (ZORUNLU) sunucu SCALPER_* env dosyası yolu
#   E17_ROOT       varsayılan $HOME/e17_root (cache/ dl/ sealed/ ledger.jsonl ...)
#   E17_VARIANTS   varsayılan <depo>/docs/audits/2026-10-10-prereg-tf/variants.json
#   E17_PY         python yolu (varsayılan <depo>/.venv/bin/python, yoksa python3)
#   E17_PRELOCK=1  kilit öncesi duman (YALNIZ run_smoke.sh / run_prova.sh): LOCK_REF.json/LOCK.json
#                  aranmaz, analizci HİÇ çağrılmaz (check-run/manifest/smoke atlanır), E17_H_COMMIT şart.
#                  run_dev/val/final bunu REDDEDER (kilitsiz koşu yok, PLAN §9.1).
#   E17_H_COMMIT   H commit'i (LOCK_REF.json `h` / `h_commit` ile çelişirse hata)
#   E17_CLONE_DIR  hazır H klonu (yoksa E17_ROOT/clone_H, E17_REPO'dan klonlanır)
#   E17_REPO       kilit commit'i L'yi (LOCK.json, lockdir) taşıyan depo; varsayılan: bu betiklerin deposu.
#                  Analizciye E17_REPO olarak geçer (analizci L'yi ve lockdir'i orada arar); H klonunda
#                  L YOKTUR (L, H'den sonra gelir).
#   E17_ANALYZER   (yalnız E17_PRELOCK=1) analizci yolu; kilit sonrası REDDEDİLİR (varsayılan:
#                  <klon>/scripts/e17/analyze_e17.py). E17_LOCK_JSON da öyle.
#   E17_WINDOW_REPO  (yalnız test) VAL boşluk hesabı için depo kökü (varsayılan: H klonu)
#
# Kod bütünlüğü: betiklerin çalışma kopyası (scripts/e17/*) H klonundaki kopyalarla bayt bayt AYNI olmalı
# (e17_ensure_clone); aksi halde kilit sonrası bir düzenleme sessizce davranışı değiştirirdi.
#   E17_FILL_WORKERS (8) / E17_FILL_FALLBACK_WORKERS (4)  doldurma işçileri
#   E17_BT_ATTEMPTS (2)  altyapı yeniden denemesi (yalnız rapor üretilmediyse)
#   E17_DRY_RUN=1  komutları çalıştırmadan 'DRY ...' satırları olarak yazdır

set -Eeuo pipefail
export PYTHONHASHSEED=0

E17_SCRIPTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
E17_PINNED_SYMS=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT
E17_PLAN_REL=docs/audits/2026-10-10-prereg-tf
E17_DRY_RUN=${E17_DRY_RUN:-0}
E17_CLEAN=()
E17_ARM_ENV=()
E17_DUMMY=()
E17_BASE_ENV=()
E17_TMPARR=()
E17_CLONE_OK=0
E17_LOCKLINK=""
E17_REPO_L=""

# ── Hata / günlük ───────────────────────────────────────────────────────────

die() {
  printf 'E17 HATA: %s\n' "$*" >&2
  exit 97
}

e17_on_err() {
  # BASH_COMMAND BİLEREK yok (snapshot değerleri sızabilir).
  printf 'E17 ERR: satır %s, rc=%s, işlev=%s\n' "$2" "$1" "${3:-ana}" >&2
  exit 97
}

e17_cleanup() {
  local d
  for d in ${E17_CLEAN[@]+"${E17_CLEAN[@]}"}; do
    if [ -n "$d" ]; then rm -rf "$d"; fi
  done
  if [ -n "$E17_LOCKLINK" ] && [ "$(readlink "$E17_LOCKLINK" 2>/dev/null)" = "$$" ]; then
    rm -f "$E17_LOCKLINK"
  fi
}

# SIGTERM/SIGINT/SIGHUP: arka plandaki işçileri (doldurma, backtest) öldür, sonra çık (EXIT tuzağı kilidi bırakır).
e17_on_term() {
  local p
  trap '' TERM INT HUP
  for p in $(jobs -p); do
    if kill "$p" 2>/dev/null; then :; fi
  done
  exit 143
}

trap 'e17_on_err $? $LINENO "${FUNCNAME[0]:-ana}"' ERR
trap e17_cleanup EXIT
trap e17_on_term TERM INT HUP

e17_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

e17_chain() {
  printf '%s %s\n' "$(e17_utc)" "$*" >> "$E17_ROOT/CHAIN.log"
}

e17_dry() { printf 'DRY %s\n' "$*"; }

e17_phase() { printf 'PHASE %s\n' "$*"; }

# ── Yardımcılar ─────────────────────────────────────────────────────────────

et() { "$E17_PY" "$E17_SCRIPTS_DIR/envtool.py" "$@"; }

# NUL ayraçlı stdin'i E17_TMPARR'a okur (mapfile bash 3.2'de yok).
e17_read_nul() {
  local x
  E17_TMPARR=()
  while IFS= read -r -d '' x; do E17_TMPARR+=("$x"); done
}

e17_tmpdir() {
  local d
  d=$(mktemp -d "${TMPDIR:-/tmp}/e17.XXXXXX")
  chmod 700 "$d"
  E17_CLEAN+=("$d")
  E17_NEWTMP=$d
}

e17_require_file() {
  [ -f "$1" ] || die "aşama dosyası yok: $1 ($2)"
}

# ── Başlatma ────────────────────────────────────────────────────────────────

e17_init() {
  E17_REPO_ROOT=$(cd "$E17_SCRIPTS_DIR/../.." && pwd)
  : "${E17_ROOT:=${HOME:-/tmp}/e17_root}"
  export E17_ROOT
  if [ -z "${E17_PY:-}" ]; then
    if [ -x "$E17_REPO_ROOT/.venv/bin/python" ]; then E17_PY="$E17_REPO_ROOT/.venv/bin/python"; else E17_PY=python3; fi
  fi
  [ -n "${E17_SNAPSHOT:-}" ] || die "E17_SNAPSHOT (sunucu SCALPER_* env dosyası) tanımlı değil"
  [ -r "$E17_SNAPSHOT" ] || die "E17_SNAPSHOT okunamıyor"
  export E17_SNAPSHOT
  : "${E17_VARIANTS:=$E17_REPO_ROOT/$E17_PLAN_REL/variants.json}"
  [ -r "$E17_VARIANTS" ] || die "variants.json okunamıyor: $E17_VARIANTS"
  export E17_VARIANTS
  E17_SYMS=$E17_PINNED_SYMS
  mkdir -p "$E17_ROOT/cache" "$E17_ROOT/dl" "$E17_ROOT/sealed/runs" "$E17_ROOT/sealed/logs" "$E17_ROOT/fill_logs"
  chmod 700 "$E17_ROOT/sealed"
  touch "$E17_ROOT/CHAIN.log"

  # Kilit sonrası (PRELOCK değil) denetimsiz yol/komut geçersiz kılmaları REDDEDİLİR.
  if [ "${E17_PRELOCK:-0}" != 1 ]; then
    [ -z "${E17_ANALYZER:-}" ] || die "E17_ANALYZER kilit sonrası kullanılamaz (yalnız E17_PRELOCK=1)"
    [ -z "${E17_LOCK_JSON:-}" ] || die "E17_LOCK_JSON kilit sonrası kullanılamaz (yalnız E17_PRELOCK=1)"
  fi
  # Analizcinin depo kökü: L'yi ve lockdir'i taşıyan checkout (H klonunda L yoktur).
  E17_REPO_L=${E17_REPO:-$E17_REPO_ROOT}
  E17_CLONE=${E17_CLONE_DIR:-$E17_ROOT/clone_H}
  if [ "$E17_DRY_RUN" = 1 ]; then
    E17_H=${E17_H_COMMIT:-DRYRUN}
  else
    e17_resolve_h
    e17_lock_guard
    e17_take_lock
    e17_ensure_clone
  fi
  E17_ANALYZER=${E17_ANALYZER:-$E17_CLONE/scripts/e17/analyze_e17.py}
  et dummy > /dev/null || die "envtool çalışmıyor"
}

e17_resolve_h() {
  local ref="$E17_ROOT/LOCK_REF.json" from_ref=""
  if [ -f "$ref" ]; then
    # Analizcinin `lock` yazdığı anahtar `h`; eski/elle yazılan kayıtlar için `h_commit` yedeği.
    from_ref=$(et json-get "$ref" h) || from_ref=""
    if [ -z "$from_ref" ]; then from_ref=$(et json-get "$ref" h_commit) || from_ref=""; fi
  fi
  if [ -z "${E17_CLONE_DIR:-}" ] && [ -f "$ref" ]; then
    # LOCK_REF.json isteğe bağlı `clone_dir` taşıyabilir (H klonunun yolu).
    local cd_ref
    cd_ref=$(et json-get "$ref" clone_dir) || cd_ref=""
    if [ -n "$cd_ref" ]; then E17_CLONE=$cd_ref; fi
  fi
  if [ "${E17_PRELOCK:-0}" = 1 ]; then
    [ -n "${E17_H_COMMIT:-}" ] || die "E17_PRELOCK=1 için E17_H_COMMIT gerekli"
    E17_H=$E17_H_COMMIT
  else
    [ -n "$from_ref" ] || die "LOCK_REF.json (h) yok: kilit olmadan koşu yok (kilit öncesi duman için E17_PRELOCK=1)"
    E17_H=$from_ref
    if [ -n "${E17_H_COMMIT:-}" ] && [ "$E17_H_COMMIT" != "$from_ref" ]; then
      die "E17_H_COMMIT, LOCK_REF.json h ile çelişiyor"
    fi
  fi
  [[ $E17_H =~ ^[0-9a-f]{40}$ ]] || die "H commit'i 40 haneli sha değil"
}

# Erken uyarı (yetkili doğrulama analizcinin `verify-lock`'ıdır).
e17_lock_guard() {
  [ "${E17_PRELOCK:-0}" = 1 ] && return 0
  local lock=${E17_LOCK_JSON:-$E17_REPO_ROOT/$E17_PLAN_REL/LOCK.json}
  [ -f "$lock" ] || die "LOCK.json yok: $lock"
  et guard --lock "$lock" --variants "$E17_VARIANTS" --snapshot "$E17_SNAPSHOT" || die "kilit ön-kontrolü başarısız"
}

e17_take_lock() {
  # Sembolik bağ = atomik oluşturma + içerik (pid) tek adımda: mkdir+pid dosyası yarışı yok.
  local lk="$E17_ROOT/.chain.lock" pid
  if [ -d "$lk" ] && [ ! -L "$lk" ]; then rm -rf "$lk"; fi     # eski biçim (dizin) kalıntısı
  if ! ln -s "$$" "$lk" 2>/dev/null; then
    pid=$(readlink "$lk" 2>/dev/null) || pid=""
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      die "başka bir E17 zinciri çalışıyor (pid $pid)"
    fi
    e17_chain "STALE LOCK devralındı (pid ${pid:-?})"
    rm -f "$lk"
    ln -s "$$" "$lk" 2>/dev/null || die "zincir kilidi alınamadı (başka zincir yarıştı)"
  fi
  E17_LOCKLINK=$lk
}

e17_ensure_clone() {
  [ "$E17_CLONE_OK" = 1 ] && return 0
  local src head
  if [ ! -e "$E17_CLONE/.git" ]; then
    [ -z "${E17_CLONE_DIR:-}" ] || die "E17_CLONE_DIR bir git klonu değil"
    src=${E17_REPO:-$E17_REPO_ROOT}
    git clone --quiet --no-hardlinks "$src" "$E17_CLONE" > /dev/null 2>&1 || die "H klonu oluşturulamadı"
    git -C "$E17_CLONE" -c advice.detachedHead=false checkout --quiet --detach "$E17_H" > /dev/null 2>&1 || die "H commit'i klonda yok"
  fi
  head=$(git -C "$E17_CLONE" rev-parse HEAD) || die "klon HEAD okunamadı"
  [ "$head" = "$E17_H" ] || die "klon HEAD'i H değil"
  [ -z "$(git -C "$E17_CLONE" status --porcelain)" ] || die "H klonu kirli (untracked/değişmiş dosya)"
  # Çalıştırılan betik kodu (çalışma kopyası) H'dekiyle AYNI olmalı (PLAN §5.3: scripts/e17/* H blob'ları).
  local f
  for f in "$E17_SCRIPTS_DIR"/*; do
    [ -f "$f" ] || continue
    cmp -s "$f" "$E17_CLONE/scripts/e17/$(basename "$f")" \
      || die "betik çalışma kopyası H klonundan farklı: scripts/e17/$(basename "$f")"
  done
  E17_CLONE_OK=1
}

# ── Env kurma ───────────────────────────────────────────────────────────────

# e17_prepare_arm_env ARM [--overlay X ...]  → E17_ARM_ENV, E17_DUMMY (+ rol doğrulaması)
e17_prepare_arm_env() {
  local arm=$1; shift
  local info
  info=$(et arm --variants "$E17_VARIANTS" --snapshot "$E17_SNAPSHOT" --variant "$arm" "$@") \
    || die "rol/TF üçlüsü / kapı doğrulaması başarısız: $arm"
  E17_TRIPLE=${info% *}
  E17_GATE=${info##* }
  e17_tmpdir
  local d=$E17_NEWTMP
  ( umask 077; et env --variants "$E17_VARIANTS" --snapshot "$E17_SNAPSHOT" --variant "$arm" "$@" > "$d/env.nul" ) \
    || die "env çözülemedi: $arm"
  e17_read_nul < "$d/env.nul"
  E17_ARM_ENV=("${E17_TMPARR[@]}")
  rm -f "$d/env.nul"
  if [ "${#E17_DUMMY[@]}" -eq 0 ]; then       # sahte gizliler sabit: süreç başına bir kez
    et dummy > "$d/dummy.nul" || die "dummy env okunamadı"
    e17_read_nul < "$d/dummy.nul"
    E17_DUMMY=("${E17_TMPARR[@]}")
    rm -f "$d/dummy.nul"
  fi
}

# e17_base_env HOMEDIR HASHSEED [net]  → E17_BASE_ENV (env -i geçiş listesi, PLAN §5.2)
e17_base_env() {
  local home=$1 hs=$2 net=${3:-}
  E17_BASE_ENV=(
    "PATH=$PATH"
    "HOME=$home"
    "PYTHONPATH=$E17_CLONE"
    "PYTHONHASHSEED=$hs"
    "TRADINGBOT_LOG_DIR=$home"
  )
  E17_BASE_ENV+=("${E17_DUMMY[@]}")
  E17_BASE_ENV+=("${E17_ARM_ENV[@]}")
  if [ "$net" = net ]; then
    local n
    for n in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE; do
      if [ -n "${!n+x}" ]; then E17_BASE_ENV+=("$n=${!n}"); fi
    done
  fi
}

# ── Analizci ────────────────────────────────────────────────────────────────

# e17_analyzer ALTKOMUT [ARGS...]  → `python scripts/e17/analyze_e17.py <komut> ... --root E17_ROOT`
#   H klonundan çalışır (analizci kendi blob'unu H ile karşılaştırır); depo kökü olarak L'yi taşıyan
#   checkout'u (E17_REPO) görür. PRELOCK'ta (kilit yok) analizci HİÇ çağrılmaz.
e17_analyzer() {
  if [ "$E17_DRY_RUN" = 1 ]; then e17_dry "ANALYZER $*"; return 0; fi
  if [ "${E17_PRELOCK:-0}" = 1 ]; then
    e17_chain "ANALYZER atlandı (E17_PRELOCK=1): $1"
    return 0
  fi
  ( cd "$E17_CLONE" && E17_REPO="$E17_REPO_L" "$E17_PY" "$E17_ANALYZER" "$@" --root "$E17_ROOT" )
}

e17_verify_lock() {
  [ "${E17_PRELOCK:-0}" = 1 ] && return 0
  e17_analyzer verify-lock || die "verify-lock başarısız (kilit uyuşmazlığı)"
}

# Kilit sonrası aşamalar PRELOCK kabul etmez (yalnız run_smoke.sh / run_prova.sh).
e17_forbid_prelock() {
  [ "${E17_PRELOCK:-0}" != 1 ] || die "E17_PRELOCK=1 yalnız run_smoke.sh/run_prova.sh için: kilitsiz DEV/VAL/FINAL koşusu yok"
}

# DEV'e geçiş kapısı (PLAN §5.2 "engelleyici"): 13 duman koşusunun HEPSİ ledger'da VE analizcinin
# `smoke` hükmü (SMOKE.json pass) true. Koşuların varlığı yetmez: ledger yalnız check-run'dan geçişi gösterir.
E17_SMOKE_LABELS="SMOKE_B0_AYI SMOKE_B0_V1 SMOKE_S15_h0 SMOKE_S15_h1 SMOKE_S5_W SMOKE_T5_W SMOKE_S15_W SMOKE_T15_W SMOKE_S30_W SMOKE_T30_W SMOKE_S60_W SMOKE_T60_W SMOKE_R1_M1"

e17_require_smoke() {
  local lbl v
  for lbl in $E17_SMOKE_LABELS; do
    e17_in_ledger "$lbl" || die "duman testi tamamlanmadı ($lbl ledger'da yok): önce run_smoke.sh"
  done
  e17_require_file "$E17_ROOT/SMOKE.json" "analizci smoke hükmü: önce run_smoke.sh"
  v=$(et json-get "$E17_ROOT/SMOKE.json" pass) || v=""
  [ "$v" = true ] || die "duman testi GEÇMEDİ (SMOKE.json pass != true): DEV başlamaz"
}

# e17_manifest SET → analizci `manifest <smoke|dev|val|final|runs>` (küçük harf)
e17_manifest() {
  local t
  t=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  e17_analyzer manifest "$t" || die "manifest $t başarısız"
}

e17_in_ledger() {
  [ -f "$E17_ROOT/ledger.jsonl" ] && grep -Fq "\"$1\"" "$E17_ROOT/ledger.jsonl"
}

# ── Önbellek koruması (PLAN §4.8, §5.2: backtest yalnız OKUR; ıska = iptal) ──────────

# Backtest öncesi önbelleği salt-okunur yap (yazan süreç PermissionError alır); doldurucu için yeniden aç.
e17_cache_ro() { chmod -R a-w "$E17_ROOT/cache"; }
e17_cache_rw() { chmod -R u+w "$E17_ROOT/cache"; }

e17_check_run() {
  local label=$1 chk="$E17_ROOT/sealed/logs/$1.check.log" rc
  if [ "$E17_DRY_RUN" = 1 ]; then e17_dry "ANALYZER check-run $label"; return 0; fi
  if [ "${E17_PRELOCK:-0}" = 1 ]; then
    # Kilit yok: analizcinin check-run'ı kilidi doğrulamadan çalışmaz. Rapor mühürlü kalır, ledger'a GİRMEZ.
    e17_chain "CHECK atlandı (E17_PRELOCK=1) $label"
    printf 'CHECK SKIP %s\n' "$label"
    return 0
  fi
  if e17_analyzer check-run "$label" > "$chk" 2>&1; then
    printf 'CHECK OK %s\n' "$label"
  else
    rc=$?
    rm -f "$E17_ROOT/sealed/runs/$label.json"
    e17_chain "CHECK FAIL $label rc=$rc (rapor silindi; ayrıntı $chk)"
    printf 'CHECK FAIL %s\n' "$label"
    exit 97
  fi
}

# ── Backtest koşusu ─────────────────────────────────────────────────────────

# run_bt ETIKET ARM BAŞLANGIÇ BİTİŞ [KAPLAMA...]
#   E17_RUN_HASHSEED=1 run_bt ...  (yalnız S2 belirlenimcilik duman testi)
run_bt() {
  local label=$1 arm=$2 start=$3 end=$4
  shift 4
  local overlays=("$@") ovargs=() o
  for o in ${overlays[@]+"${overlays[@]}"}; do ovargs+=(--overlay "$o"); done
  [[ $label =~ ^[A-Za-z0-9_]+$ ]] || die "geçersiz koşu etiketi: $label"
  local hs=${E17_RUN_HASHSEED:-0}
  local runs="$E17_ROOT/sealed/runs/$label.json" logf="$E17_ROOT/sealed/logs/$label.log"

  # Tamamlanmış koşu (ledger'da) asla tekrarlanmaz.
  if e17_in_ledger "$label"; then
    [ -f "$runs" ] || die "ledger'da var ama rapor yok: $label"
    e17_chain "SKIP $label (ledger'da)"
    printf 'BT SKIP %s\n' "$label"
    return 0
  fi
  # Bayat çıktı olamaz.
  rm -f "$runs" "$logf" "$E17_ROOT/sealed/logs/$label.check.log"

  e17_prepare_arm_env "$arm" ${ovargs[@]+"${ovargs[@]}"}
  local d=$E17_NEWTMP
  ( umask 077; et cli --variants "$E17_VARIANTS" ${ovargs[@]+"${ovargs[@]}"} > "$d/cli.nul" ) \
    || die "cli çözülemedi: $arm"
  e17_read_nul < "$d/cli.nul"
  local cli=("${E17_TMPARR[@]}")

  if [ "$E17_DRY_RUN" = 1 ]; then
    e17_dry "BT $label arm=$arm window=$start..$end overlays=${overlays[*]-} triple=${E17_TRIPLE// //} hashseed=$hs cli=${cli[*]}"
    e17_dry "ARGV $label python -m src.strategies.scalper.backtest ${cli[*]} --start $start --end $end --cache-dir $E17_ROOT/cache"
    e17_dry "ENV $label env -i keys=PATH,HOME,PYTHONPATH,PYTHONHASHSEED,TRADINGBOT_LOG_DIR,dummy(${#E17_DUMMY[@]}),arm(${#E17_ARM_ENV[@]})"
    return 0
  fi
  e17_ensure_clone

  mkdir "$d/home"
  e17_base_env "$d/home" "$hs"
  local attempt=1 max=${E17_BT_ATTEMPTS:-2} rc n report f t0=$SECONDS pid fp0 fp1
  e17_cache_ro
  fp0=$(et cache-fp "$E17_ROOT/cache") || die "önbellek parmak izi alınamadı"
  while :; do
    rm -rf "$d/cwd"
    mkdir "$d/cwd"
    # Arka planda + wait: SIGTERM geldiğinde e17_on_term çocuğu hemen öldürebilsin.
    ( cd "$d/cwd" && exec env -i "${E17_BASE_ENV[@]}" "$E17_PY" -m src.strategies.scalper.backtest \
        "${cli[@]}" --start "$start" --end "$end" --cache-dir "$E17_ROOT/cache" ) > "$logf" 2>&1 &
    pid=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    fp1=$(et cache-fp "$E17_ROOT/cache") || die "önbellek parmak izi alınamadı"
    if [ "$fp0" != "$fp1" ]; then
      # Backtest önbelleğe YAZDI (ıska -> ağdan çekim): doğrulanmamış veri; zincir durur.
      e17_chain "FAIL $label önbellek backtest sırasında DEĞİŞTİ (ağdan çekim?); günlük: $logf"
      printf 'BT FAIL %s\n' "$label"
      exit 97
    fi
    n=0
    report=""
    for f in "$d"/cwd/logs/backtest_*.json; do
      if [ -f "$f" ]; then n=$((n + 1)); report=$f; fi
    done
    if [ "$rc" -eq 0 ] && [ "$n" -eq 1 ]; then
      break
    fi
    if [ "$n" -eq 0 ] && [ "$attempt" -lt "$max" ]; then
      e17_chain "RETRY $label deneme=$attempt rc=$rc (rapor üretilmedi; altyapı)"
      attempt=$((attempt + 1))
      continue
    fi
    e17_chain "FAIL $label rc=$rc raporlar=$n deneme=$attempt (günlük: $logf)"
    printf 'BT FAIL %s\n' "$label"
    exit 97
  done
  cp "$report" "$runs"
  chmod 0400 "$runs"
  printf 'BT OK %s %s\n' "$label" "$((SECONDS - t0))"
  e17_check_run "$label"
  rm -rf "$d"
}

# ── Doldurma ────────────────────────────────────────────────────────────────

# Sembolleri N işçiye dağıt (round-robin); boş grup atlanır. → E17_GROUPS
e17_split_symbols() {
  local nw=$1 i=0 s
  local -a syms
  IFS=, read -r -a syms <<< "$E17_SYMS"
  E17_GROUPS=()
  while [ "$i" -lt "$nw" ]; do E17_GROUPS+=(""); i=$((i + 1)); done
  i=0
  for s in "${syms[@]}"; do
    local k=$((i % nw))
    if [ -z "${E17_GROUPS[$k]}" ]; then E17_GROUPS[$k]=$s; else E17_GROUPS[$k]="${E17_GROUPS[$k]},$s"; fi
    i=$((i + 1))
  done
}

# Tek geçiş: nw işçi paralel; herhangi biri başarısızsa 1 döner (die ETMEZ).
_e17_fill_pass() {
  local tag=$1 start=$2 end=$3 nw=$4 gateflag=$5
  local d=$E17_NEWTMP i=0 g failed=0 pid
  local pids=()
  e17_split_symbols "$nw"
  for g in "${E17_GROUPS[@]}"; do
    if [ -n "$g" ]; then
      mkdir -p "$d/w$i"
      e17_base_env "$d/w$i" 0 net
      local lg="$E17_ROOT/fill_logs/$tag.w$i.log"
      local extra=()
      if [ -n "$gateflag" ]; then extra+=("$gateflag"); fi
      ( cd "$d/w$i" && exec env -i "${E17_BASE_ENV[@]}" "$E17_PY" "$E17_CLONE/scripts/fill_klines_cache_from_vision.py" \
          --symbols "$g" --start "$start" --end "$end" --cache-dir "$E17_ROOT/cache" \
          --download-cache "$E17_ROOT/dl" ${extra[@]+"${extra[@]}"} ) > "$lg" 2>&1 &
      pids+=($!)
    fi
    i=$((i + 1))
  done
  for pid in "${pids[@]}"; do
    if wait "$pid"; then :; else failed=1; fi
  done
  return "$failed"
}

# fill_window ETIKET ARM BAŞLANGIÇ BİTİŞ → 0/1 (hata durumunda ÇAĞIRAN karar verir;
# 8 işçi başarısızsa 1 kez 4 işçiyle yeniden dener — PLAN §4.5). `--tf-*` ASLA.
fill_window() {
  local tag=$1 arm=$2 start=$3 end=$4
  local nw=${E17_FILL_WORKERS:-8} fb=${E17_FILL_FALLBACK_WORKERS:-4}
  e17_prepare_arm_env "$arm"
  local gateflag=""
  if [ "$E17_GATE" = 1 ]; then gateflag="--market-gate"; fi
  if [ "$E17_DRY_RUN" = 1 ]; then
    local g
    e17_split_symbols "$nw"
    for g in "${E17_GROUPS[@]}"; do
      if [ -n "$g" ]; then e17_dry "FILL $tag arm=$arm window=$start..$end symbols=$g gate=$E17_GATE flag=${gateflag:--} workers=$nw"; fi
    done
    return 0
  fi
  e17_ensure_clone
  local t0=$SECONDS
  rm -f "$E17_ROOT/fill_logs/$tag".w*.log
  e17_cache_rw
  if _e17_fill_pass "$tag" "$start" "$end" "$nw" "$gateflag"; then
    e17_cache_ro
    printf 'FILL OK %s %s\n' "$tag" "$((SECONDS - t0))"
    return 0
  fi
  if [ "$nw" -gt "$fb" ]; then
    e17_chain "FILL RETRY $tag işçi $nw -> $fb"
    rm -f "$E17_ROOT/fill_logs/$tag".w*.log
    if _e17_fill_pass "$tag" "$start" "$end" "$fb" "$gateflag"; then
      e17_cache_ro
      printf 'FILL OK %s %s\n' "$tag" "$((SECONDS - t0))"
      return 0
    fi
  fi
  e17_cache_ro
  e17_chain "FILL FAIL $tag ($start..$end; günlükler: $E17_ROOT/fill_logs/$tag.w*.log)"
  printf 'FILL FAIL %s\n' "$tag"
  return 1
}

fill_or_die() {
  fill_window "$@" || exit 97
}

# ── Pencere / küme yardımcıları ─────────────────────────────────────────────

# e17_window ADI  → E17_WS E17_WE
e17_window() {
  local out
  out=$(et window --variants "$E17_VARIANTS" --name "$1") || die "pencere çözülemedi: $1"
  read -r E17_WS E17_WE <<< "$out"
}

# e17_lines CMD... → satırları E17_TMPARR'a koy (hata = die)
e17_lines() {
  local out x
  out=$("$@") || die "komut başarısız: $1 $2"
  E17_TMPARR=()
  while IFS= read -r x; do
    if [ -n "$x" ]; then E17_TMPARR+=("$x"); fi
  done <<< "$out"
}

e17_family_ids() {
  e17_lines et ids --variants "$E17_VARIANTS" --group family
  E17_FAMILY=("${E17_TMPARR[@]}")
}

e17_triple_of() {
  et triple --variants "$E17_VARIANTS" --snapshot "$E17_SNAPSHOT" --variant "$1"
}
