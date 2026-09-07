"""Execute the shipped dashboard JavaScript with a small, offline DOM stub.

Node is optional on Python-only deployments; local/CI environments with Node
run these behavioural contracts without a browser, exchange, or HTTP request.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="dashboard JS tests need Node.js")


def dashboard_js(body, *, tz="Europe/Stockholm"):
    harness = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[1], 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const source = script.slice(0, script.indexOf('  qs("refresh-toggle").addEventListener'));
class Element {
  constructor() {
    this.children = []; this.textContent = ''; this.className = ''; this.title = ''; this.style = {};
    const classes = new Set();
    this.classList = {add(x) { classes.add(x); }, remove(x) { classes.delete(x); }, contains(x) { return classes.has(x); }};
  }
  set innerHTML(value) { this._html = value; this.children = []; }
  get innerHTML() { return this._html || ''; }
  appendChild(value) { this.children.push(value); return value; }
}
const nodes = {};
const document = {
  getElementById(id) { return nodes[id] || (nodes[id] = new Element()); },
  createElement() { return new Element(); }
};
const RealDate = Date;
class FixedDate extends RealDate {
  constructor(...args) { super(...(args.length ? args : ['2026-09-07T21:10:00Z'])); }
  static now() { return RealDate.parse('2026-09-07T21:10:00Z'); }
}
const testTimers = new Map();
let nextTimer = 0;
const context = { document, Date: FixedDate, console, assert, AbortController, testTimers,
  setTimeout(fn) { testTimers.set(++nextTimer, fn); return nextTimer; },
  clearTimeout(id) { testTimers.delete(id); }
};
vm.createContext(context);
vm.runInContext(source + '\n' + process.argv[2] + '\n})();', context);
if (context.testPromise) {
  const watchdog = setTimeout(() => { throw new Error('async dashboard test did not finish'); }, 2000);
  context.testPromise.then(() => clearTimeout(watchdog), error => {
    clearTimeout(watchdog); throw error;
  });
}
"""
    result = subprocess.run(
        [NODE, "-e", harness, str(ROOT / "static/dashboard.html"), body],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "TZ": tz},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_header_includes_scalper_owned_unrealized():
    dashboard_js("""
      var p = {ok:true, data:{positions:[]}};
      var s = {ok:true, data:{as_of:'2026-09-07T21:09:55Z', tracked:[
        {symbol:'BTCUSDT', direction:'SHORT', unrealized_pnl:-4.25,
         unrealized_pnl_status:'ok', valuation_as_of:'2026-09-07T21:09:55Z',
         current_price:100, roi_pct:-2}
      ]}};
      renderTopbar(null, null, p, s);
      assert.equal(qs('stat-unrealized').textContent, fmtUsdt(-4.25));
    """)


@pytest.mark.parametrize("tz", ["Europe/Stockholm", "UTC", "America/New_York"])
def test_naive_database_timestamp_is_utc(tz):
    dashboard_js("""
      assert.equal(fmtAge('2026-09-07T20:10:00'), '1 sa 0 dk');
      assert.equal(fmtAge('2026-09-07T20:10:00.123456'), '59 dk');
      assert.equal(fmtAge('2026-09-07T20:10:00'), fmtAge('2026-09-07T20:10:00Z'));
      assert.equal(fmtClock('2026-09-07T20:10:00'), fmtClock('2026-09-07T20:10:00Z'));
      assert.equal(fmtDateTime('2026-09-07T20:10:00'), fmtDateTime('2026-09-07T20:10:00Z'));
    """, tz=tz)


def test_enabled_daily_breaker_is_not_labelled_off():
    dashboard_js("""
      renderSysbar({ok:true, data:{kill_switch_active:false, daily_limit_pct:1, risk_ready:true}});
      var item = qs('sysbar').children.find(x => x.children[0] && x.children[0].innerHTML === 'Günlük kesici');
      assert.match(item.children[1].innerHTML, /hazır/);
      assert.doesNotMatch(item.children[1].innerHTML, /kapalı/);
    """)


SNAPSHOT_SETUP = """
  var row = {symbol:'BTCUSDT', direction:'SHORT', strategy:'C', entry_price:100,
    current_price:101, roi_pct:-20, unrealized_pnl:-4.25, unrealized_pnl_status:'ok',
    valuation_as_of:'2026-09-07T21:09:55Z', opened_at:'2026-09-07T20:10:00'};
  var p = {ok:true, data:{positions:[]}};
  var s = {ok:true, data:{as_of:'2026-09-07T21:09:55Z', tracked:[row]}};
"""


@pytest.mark.parametrize("mutation", [
    "p.ok = false;",
    "s.ok = false;",
    "delete p.data.positions;",
    "delete s.data.tracked;",
    "delete s.data.as_of;",
    "s.data.as_of = '2026-09-07T21:08:39Z';",
    "s.data.as_of = '2026-09-07T21:10:06Z';",
    "row.unrealized_pnl = null;",
    "row.unrealized_pnl = NaN;",
    "row.unrealized_pnl = Infinity;",
    "delete row.unrealized_pnl;",
    "delete row.direction;",
    "delete row.unrealized_pnl_status;",
    "row.unrealized_pnl_status = 'stale';",
    "row.unrealized_pnl_status = 'unobserved';",
    "row.unrealized_pnl_status = 'invalid';",
    "row.unrealized_pnl_status = 'position_mismatch';",
    "row.valuation_as_of = '2026-09-07T21:08:39Z';",
    "row.valuation_as_of = null;",
    "delete row.valuation_as_of;",
    "s.data.tracked.push(null);",
    "s.data.tracked.push({symbol:''});",
])
def test_incomplete_or_unknown_values_never_become_a_zero_or_partial_total(mutation):
    dashboard_js(SNAPSHOT_SETUP + mutation + """
      assert.equal(positionSnapshot(p, s).total, null);
      renderTopbar(null, {ok:true, data:{}}, p, s);
      assert.equal(qs('stat-unrealized').textContent, '—');
      assert.match(qs('stat-unrealized').title, /ölçülemedi/);
    """)


def test_successful_empty_lists_are_the_only_known_zero():
    dashboard_js(SNAPSHOT_SETUP + """
      s.data.tracked = [];
      assert.equal(positionSnapshot(p, s).total, 0);
      renderPositions(p, s);
      assert.equal(qs('positions-wrap').children[0].innerHTML, 'Açık pozisyon yok');
      s.ok = false;
      renderPositions(p, s);
      assert.match(qs('positions-wrap').children[0].innerHTML, /bilinmiyor/);
    """)


def test_valuation_with_zero_pnl_is_valid_not_missing():
    dashboard_js(SNAPSHOT_SETUP + """
      row.unrealized_pnl = 0;
      assert.equal(positionSnapshot(p, s).total, 0);
    """)


def test_both_owners_use_one_shared_union_and_scalper_wins_duplicates():
    dashboard_js(SNAPSHOT_SETUP + """
      p.data.positions = [
        {symbol:'BTCUSDT', side:'SHORT', entry_price:99, current_price:999, unrealized_pnl:777},
        {symbol:'ETHUSDT', side:'LONG', entry_price:100, current_price:101, unrealized_pnl:2}
      ];
      var combined = positionSnapshot(p, s);
      assert.equal(combined.total, -2.25);
      assert.equal(combined.rows.length, 2);
      assert.equal(combined.rows[0].entry, 100);
      assert.equal(combined.rows[0].current, 101);
      assert.equal(combined.rows[0].strategy, 'C');
      renderPositions(p, s);
      assert.equal(qs('positions-count').textContent, '(2)');
      assert.equal(qs('positions-wrap').innerHTML.split('BTCUSDT').length - 1, 1);
    """)


def test_identical_duplicates_are_counted_once_within_each_source():
    dashboard_js(SNAPSHOT_SETUP + """
      s.data.tracked.push({...row});
      var old = {symbol:'ETHUSDT', side:'LONG', unrealized_pnl:2};
      p.data.positions = [old, {...old}];
      var combined = positionSnapshot(p, s);
      assert.equal(combined.total, -2.25);
      assert.equal(combined.rows.length, 2);
    """)


@pytest.mark.parametrize("mutation", [
    "s.data.tracked.push({...row, unrealized_pnl:90});",
    "s.data.tracked.push({...row, direction:'LONG'});",
    "p.data.positions = [{symbol:'BTCUSDT', side:'LONG', unrealized_pnl:5}];",
])
def test_conflicting_duplicates_do_not_silently_choose_a_pnl(mutation):
    dashboard_js(SNAPSHOT_SETUP + mutation + """
      var combined = positionSnapshot(p, s);
      assert.equal(combined.total, null);
      assert.equal(combined.rows.length, 1);
      assert.equal(combined.rows[0].current, null);
      renderPositions(p, s);
      assert.match(qs('positions-wrap').innerHTML, /VERİ BEKLENİYOR/);
    """)


def test_nonok_valuation_hides_stale_price_and_roi_but_preserves_protection():
    dashboard_js(SNAPSHOT_SETUP + """
      row.unrealized_pnl_status = 'stale';
      row.current_stoploss = 110;
      row.tp1_done = true;
      row.trailing_active = true;
      var combined = positionSnapshot(p, s);
      assert.equal(combined.rows[0].current, null);
      assert.equal(combined.rows[0].roi, null);
      assert.equal(combined.rows[0].sl, 110);
      renderPositions(p, s);
      assert.match(qs('positions-wrap').innerHTML, /TP1 ✓/);
      assert.match(qs('positions-wrap').innerHTML, /TRAILING/);
      assert.match(qs('positions-wrap').innerHTML, /VERİ BEKLENİYOR/);
    """)


def test_freshness_uses_80_second_monitoring_cycle_tolerance():
    dashboard_js(SNAPSHOT_SETUP + """
      s.data.as_of = '2026-09-07T21:08:40Z';
      row.valuation_as_of = '2026-09-07T21:08:40Z';
      assert.equal(positionSnapshot(p, s).total, -4.25);
      row.valuation_as_of = '2026-09-07T21:08:39Z';
      assert.equal(positionSnapshot(p, s).total, null);
    """)


def test_partial_position_uses_venue_pnl_not_initial_quantity_recalculation():
    dashboard_js(SNAPSHOT_SETUP + """
      row.quantity = 100;
      row.remaining_quantity = 20;
      row.mark_price = 102;
      row.current_price = 101;
      row.unrealized_pnl = -40;
      assert.equal(positionSnapshot(p, s).total, -40);
      assert.equal(positionSnapshot(p, s).rows[0].current, 102);
      row.unrealized_pnl_status = 'unobserved';
      assert.equal(positionSnapshot(p, s).total, null);
    """)


def test_unknown_scalper_owner_never_falls_back_to_old_orchestrator_pnl():
    dashboard_js(SNAPSHOT_SETUP + """
      row.unrealized_pnl = null;
      row.unrealized_pnl_status = 'unobserved';
      p.data.positions = [{symbol:'BTCUSDT', side:'SHORT', unrealized_pnl:77}];
      assert.equal(positionSnapshot(p, s).rows.length, 1);
      assert.equal(positionSnapshot(p, s).total, null);
    """)


def test_total_does_not_truncate_at_a_table_page_limit_and_rejects_overflow():
    dashboard_js(SNAPSHOT_SETUP + """
      s.data.tracked = Array.from({length:40}, (_, i) => ({...row, symbol:'COIN'+i, unrealized_pnl:i}));
      assert.equal(positionSnapshot(p, s).total, 780);
      assert.equal(positionSnapshot(p, s).rows.length, 40);
      s.data.tracked.forEach(x => x.unrealized_pnl = Number.MAX_VALUE);
      assert.equal(positionSnapshot(p, s).total, null);
      assert.equal(positionSnapshot(p, s).complete, false);
    """)


def test_missing_virtual_equity_does_not_display_testnet_wallet_as_strategy_capital():
    dashboard_js(SNAPSHOT_SETUP + """
      s.data.virtual_capital_enabled = true;
      s.data.sizing_equity_usdt = null;
      renderTopbar(null, {ok:true,data:{account:{balance:10000}}}, p, s);
      assert.equal(qs('stat-balance-label').textContent, 'Strateji Sermayesi');
      assert.equal(qs('stat-balance').textContent, '—');
    """)


def test_unavailable_daily_pnl_is_not_a_known_zero():
    dashboard_js(SNAPSHOT_SETUP + """
      s.data.daily_pnl = 0;
      s.data.daily_pnl_source = 'unavailable';
      renderTopbar(null, null, p, s);
      assert.equal(qs('stat-daily').textContent, '—');
      s.data.daily_pnl_source = 'ledger';
      renderTopbar(null, null, p, s);
      assert.equal(qs('stat-daily').textContent, fmtUsdt(0));
    """)


def test_forensics_observation_and_registration_are_not_exchange_fill_time():
    dashboard_js("""
      var result = renderForensicsCard({has_forensics:true, id:1, symbol:'BTCUSDT',
        entry:{fill_price:100, at:'2026-09-07T20:10:12Z',
          fill_latency_sec:null, fill_latency_source:'unmeasured_exchange_time',
          fill_observed_at:'2026-09-07T20:10:01Z', fill_observed_latency_sec:1,
          protection_registration_latency_sec:11}, exit:{}, verdict:[]});
      assert.ok(result.includes('Sinyal → dolum</span><span class="v ">—</span>'));
      assert.ok(result.includes('Sinyal → dolum gözlemi</span><span class="v ">1.0 sn'));
      assert.ok(result.includes('Dolum gözlemi → koruma kaydı</span><span class="v ">11.0 sn'));
      assert.ok(result.includes('Dolum gözlemi 100,00'));
      assert.ok(result.includes('22:10:01'));
      assert.ok(!result.includes('22:10:12'));
      var legacy = renderForensicsCard({has_forensics:true, entry:{at:'2026-09-07T20:10:12Z'}, exit:{}});
      assert.ok(legacy.includes('Giriş kaydı'));
      assert.ok(!legacy.includes('Sinyal → dolum gözlemi'));
    """)


def test_fetch_http_result_and_success_clear_the_deadline():
    dashboard_js("""
      globalThis.testPromise = (async function(){
        globalThis.fetch = async () => ({ok:true,status:200,json:async () => ({value:7})});
        var result = await fetchJSON('/fixture');
        assert.equal(result.data.value, 7);
        assert.equal(testTimers.size, 0);
        globalThis.fetch = async () => ({ok:false,status:503,json:async () => ({detail:'offline'})});
        result = await fetchJSON('/fixture');
        assert.equal(result.ok, false);
        assert.equal(result.networkError, false);
        assert.equal(result.status, 503);
        assert.equal(testTimers.size, 0);
      })();
    """)


@pytest.mark.parametrize("hang", ["fetch", "json"])
def test_fetch_timeout_covers_network_and_json_body_even_if_abort_is_ignored(hang):
    dashboard_js("""
      globalThis.testPromise = (async function(){
        var release, signal;
        var hung = new Promise(resolve => release = resolve);
        globalThis.fetch = async (_url, options) => {
          signal = options.signal;
    """ + ("return hung;" if hang == "fetch" else "return {ok:true,status:200,json:() => hung};") + """
        };
        var promise = fetchJSON('/fixture');
        for (var i=0; i<8; i++) await Promise.resolve();
        assert.equal(testTimers.size, 1);
        Array.from(testTimers.values()).forEach(fn => fn());
        var result = await promise;
        assert.equal(result.ok, false);
        assert.equal(result.timedOut, true);
        assert.equal(result.data, null);
        assert.equal(signal.aborted, true);
        assert.equal(testTimers.size, 0);
        release({ok:true,status:200,json:async () => ({stale:true})});
        for (var i=0; i<8; i++) await Promise.resolve();
        assert.equal(result.data, null);
      })();
    """)


def test_tick_timeout_clears_stale_header_table_and_allows_next_refresh_without_overlap():
    dashboard_js(SNAPSHOT_SETUP + """
      globalThis.testPromise = (async function(){
        renderTopbar(null, null, p, s);
        renderPositions(p, s);
        assert.equal(qs('stat-unrealized').textContent, fmtUsdt(-4.25));
        // Exercise the real topbar/positions/fetch/tick path; unrelated cards
        // are isolated because they have independent source contracts.
        renderSysbar = renderFollower = renderAiGate = renderStrategyCards =
          renderRegimeMap = renderTrades = renderImpact = renderWaiting = function(){};
        var releases = [], calls = 0;
        globalThis.fetch = function(){
          calls++;
          return new Promise(resolve => releases.push(resolve));
        };
        var first = tick();
        assert.equal(tick(), first);
        await Promise.resolve();
        assert.equal(calls, 9);
        Array.from(testTimers.values()).forEach(fn => fn());
        await first;
        assert.equal(tickInFlight, false);
        assert.equal(qs('stat-unrealized').textContent, '—');
        assert.match(qs('positions-wrap').children[0].innerHTML, /bilinmiyor/);
        assert.equal(qs('conn-banner').classList.contains('show'), true);
        row.unrealized_pnl = 6;
        globalThis.fetch = async function(url){
          return {ok:true,status:200,json:async () => url === '/positions' ? p.data :
            (url === '/scalper/status' ? s.data : {})};
        };
        await tick();
        assert.equal(qs('stat-unrealized').textContent, fmtUsdt(6));
        assert.equal(qs('positions-count').textContent, '(1)');
        assert.equal(qs('conn-banner').classList.contains('show'), false);
        // Late completions from the aborted older refresh cannot repaint.
        releases.forEach(resolve => resolve({ok:true,status:200,json:async () => ({positions:[],tracked:[]})}));
        for (var i=0; i<12; i++) await Promise.resolve();
        assert.equal(qs('stat-unrealized').textContent, fmtUsdt(6));
        assert.equal(tickInFlight, false);
        assert.equal(testTimers.size, 0);
      })();
    """)


def test_tick_render_exception_still_releases_the_refresh_guard():
    dashboard_js("""
      globalThis.testPromise = (async function(){
        globalThis.fetch = async () => ({ok:true,status:200,json:async () => ({})});
        renderTopbar = function(){throw new Error('fixture renderer failure');};
        await tick();
        assert.equal(tickInFlight, false);
        assert.equal(tickPromise, null);
        assert.equal(qs('conn-banner').classList.contains('show'), true);
        assert.equal(testTimers.size, 0);
      })();
    """)


def test_backend_valuation_payload_renders_without_a_mirrored_ui_only_contract():
    """Catch API/UI field-name drift using the real engine projection."""
    from test_position_valuation import _manager, _observe, _sp
    from test_runtime_liveness import _make_engine

    sp = _sp()
    manager = _manager(sp)
    _observe(manager, sp)
    engine = _make_engine()
    engine.exits = manager
    row = engine.snapshot()["tracked"][0]
    # The VM has a deterministic browser wall clock; only align the fixture's
    # observation timestamp, never rebuild/rename the backend value fields.
    row["valuation_as_of"] = "2026-09-07T21:09:55Z"
    dashboard_js("var row = " + json.dumps(row) + ";" + """
      var p = {ok:true,data:{positions:[],as_of:'2026-09-07T21:09:55Z'}};
      var s = {ok:true,data:{tracked:[row],as_of:'2026-09-07T21:09:55Z'}};
      assert.equal(positionSnapshot(p, s).total, 0.6);
      renderTopbar(null, null, p, s);
      assert.equal(qs('stat-unrealized').textContent, fmtUsdt(0.6));
      assert.equal(positionSnapshot(p, s).rows[0].current, 101);
    """)


def test_timestamps_preserve_offsets_and_reject_invalid_or_future_ages():
    dashboard_js("""
      ['2026-09-07T22:10:00+02:00', '2026-09-07T22:10:00+0200',
       '2026-09-07T16:10:00-04:00', '2026-09-07 20:10:00',
       '2026-09-07T20:10:00Z'].forEach(x => assert.equal(fmtAge(x), '1 sa 0 dk'));
      [null, '', 'garbage', '2026-02-30T20:10:00', '2026-09-07T24:10:00',
       '2026-09-07T21:10:06Z'].forEach(x => assert.equal(fmtAge(x), '—'));
      assert.notEqual(apiTime('2024-02-29T20:10:00'), null);
      assert.equal(apiTime('2025-02-29T20:10:00'), null);
      assert.equal(fmtAge('2026-09-07T21:10:04Z'), '0 dk');
    """)


def test_last_update_parses_naive_utc_before_selecting_oldest_snapshot():
    dashboard_js("""
      setLastUpdate({ok:true,data:{as_of:'2026-09-07T20:10:00'}},
                    {ok:true,data:{as_of:'2026-09-07T22:20:00+02:00'}});
      assert.match(qs('last-update').textContent, /22:10:00/);
    """)


@pytest.mark.parametrize(("fields", "label", "style"), [
    ("{kill_switch_active:true, daily_limit_pct:1}", "AKTİF", "bad"),
    ("{kill_switch_active:false, daily_limit_pct:1, risk_ready:true}", "hazır", "ok"),
    ("{kill_switch_active:false, daily_limit_pct:1, risk_ready:false}", "risk verisi bekleniyor", "warn"),
    ("{kill_switch_active:false, daily_limit_pct:1}", "risk verisi bekleniyor", "warn"),
    ("{kill_switch_active:false, daily_limit_pct:0}", "devre dışı", "off"),
    ("{kill_switch_active:false}", "bilinmiyor", "warn"),
    ("{daily_limit_pct:1, risk_ready:true}", "bilinmiyor", "warn"),
])
def test_daily_breaker_does_not_confuse_enabled_triggered_and_unknown(fields, label, style):
    dashboard_js(f"""
      renderSysbar({{ok:true, data:{fields}}});
      var item = qs('sysbar').children.find(x => x.children[0] && x.children[0].innerHTML === 'Günlük kesici');
      assert.ok(item.children[1].innerHTML.includes('{label}'));
      assert.equal(item.className, 'sysbar-item {style}');
    """)
