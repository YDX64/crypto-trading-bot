"""Primary performance must describe the same cohort as strategy capital."""

import pytest

from tests.test_dashboard_data import dashboard_js


SETUP = """
var good = {trades:10,wins:7,winrate:70,total_pnl:501.88,profit_factor:2,pnl_basis:'binance_income_net'};
var cohort = {trades:4,wins:2,winrate:50,total_pnl:-100.47,profit_factor:0.5,pnl_basis:'mixed',verified_trades:3,fallback_trades:1};
var stats = {ok:true,data:{combined:good,strategies:{C:good},performance_scope:{
 enabled:true,kind:'virtual_capital_cohort',start_trade_id:278,base_capital_usdt:1000,
 eligible_realized_pnl:-100.47,capital_usdt:899.53,combined:cohort,strategies:{C:cohort},
 excluded_positive_fallback:2,excluded_legacy:1}}};
var rendered = [];
strategyCardHtml = function(key, value, leader) {rendered.push({key,value,leader});return document.createElement('div');};
"""


def test_primary_cards_use_capital_cohort_not_unrelated_historical_gains():
    dashboard_js(SETUP + r"""
      renderStrategyCards(stats);
      assert.equal(rendered.find(x=>x.key==='combined').value.total_pnl,-100.47);
      assert.equal(rendered.find(x=>x.key==='C').value.total_pnl,-100.47);
      assert.equal(rendered.some(x=>x.leader),false);
      assert.match(qs('strat-updated').textContent, /Sermaye dönemi #278/);
      assert.match(qs('pnl-quality').textContent, /Tüm geçmiş K\/Z \(ayrı dönem\)/);
      assert.match(qs('pnl-quality').textContent, /1 işlem tahmini brüt/);
    """)


@pytest.mark.parametrize("mutation", [
    "delete stats.data.performance_scope.combined;",
    "stats.data.performance_scope.kind='unknown';",
    "stats.data.performance_scope.eligible_realized_pnl=null;",
    "stats.data.performance_scope.start_trade_id=null;",
])
def test_broken_enabled_cohort_never_silently_reverts_to_all_history(mutation):
    dashboard_js(SETUP + mutation + """
      renderStrategyCards(stats);
      assert.equal(rendered.find(x=>x.key==='combined').value,null);
      assert.match(qs('strat-updated').textContent, /ölçülemedi/);
    """)


@pytest.mark.parametrize("mutation", [
    "delete stats.data.performance_scope;",
    "stats.data.performance_scope={enabled:false,kind:'all_history'};",
])
def test_unscoped_compatibility_is_explicitly_all_history(mutation):
    dashboard_js(SETUP + mutation + """
      renderStrategyCards(stats);
      assert.equal(rendered.find(x=>x.key==='combined').value.total_pnl,501.88);
      assert.match(qs('strat-updated').textContent, /Tüm geçmiş/);
    """)


def test_running_but_daily_paused_is_not_presented_as_unqualified_healthy():
    dashboard_js("""
      renderTopbar({ok:true,data:{network:'testnet',status:'healthy'}},null,null,
        {ok:true,data:{kill_switch_active:true}});
      assert.match(qs('health-text').textContent, /yeni girişler durduruldu/);
      assert.match(qs('health-text').title, /kârlılık göstergesi değildir/);
    """)


def test_verified_but_losing_strategy_does_not_receive_winner_badge():
    dashboard_js(SETUP + """
      cohort.pnl_basis='binance_income_net';
      renderStrategyCards(stats);
      assert.equal(rendered.some(x=>x.leader),false);
    """)


def test_intentional_entry_block_explains_why_gate_data_is_not_refreshed():
    dashboard_js("""
      renderSysbar({ok:true,data:{entries_blocked_by:'kill_switch',kill_switch_active:true,
        market_gate:{enabled:true,gate_effective:false,stale:true,stale_reason:'entries_blocked'}}});
      var item=qs('sysbar').children.find(x=>x.children[0] && x.children[0].innerHTML==='Kapı');
      assert.equal(item.children[1].innerHTML,'girişler kapalı · veri yenilenmiyor');
      assert.doesNotMatch(item.children[1].innerHTML,/tarama durdu/);
    """)
