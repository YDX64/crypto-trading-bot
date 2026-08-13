"""TvConfluence — çoklu-kaynak sağlama motoru testleri (2026-08-13).

Sözleşme: pencere içinde FARKLI kaynak sayısı >= required → tetiklenir;
aynı kaynağın tekrarı sayıyı artırmaz; ters yön oyu tüm oyları sıfırlar;
tetiklenince o sembol+yön temizlenir (çifte tetik yok).
"""

import pytest

from src.services.tv_confluence import TvConfluence


def _c(required=2, window=180.0) -> TvConfluence:
    return TvConfluence(required=required, window_seconds=window)


class TestVoting:
    def test_single_vote_below_threshold_does_not_trigger(self):
        c = _c()
        v = c.vote("BTCUSDT", "LONG", "algopro")
        assert v["triggered"] is False
        assert v["votes"] == 1 and v["required"] == 2

    def test_two_distinct_sources_trigger(self):
        c = _c()
        c.vote("BTCUSDT", "LONG", "algopro")
        v = c.vote("BTCUSDT", "LONG", "luxalgo")
        assert v["triggered"] is True
        assert sorted(v["sources"]) == ["algopro", "luxalgo"]

    def test_same_source_twice_does_not_trigger(self):
        c = _c()
        c.vote("BTCUSDT", "LONG", "algopro")
        v = c.vote("BTCUSDT", "LONG", "algopro")
        assert v["triggered"] is False
        assert v["votes"] == 1

    def test_trigger_clears_votes_no_double_fire(self):
        c = _c()
        c.vote("BTCUSDT", "LONG", "algopro")
        assert c.vote("BTCUSDT", "LONG", "luxalgo")["triggered"] is True
        # Aynı mumda üçüncü kaynak gelirse sıfırdan saymalı.
        v = c.vote("BTCUSDT", "LONG", "osc")
        assert v["triggered"] is False and v["votes"] == 1

    def test_symbols_and_directions_are_isolated(self):
        c = _c()
        c.vote("BTCUSDT", "LONG", "algopro")
        v = c.vote("ETHUSDT", "LONG", "luxalgo")
        assert v["triggered"] is False

    def test_required_one_triggers_immediately(self):
        c = _c(required=1)
        assert c.vote("BTCUSDT", "SHORT", "algopro")["triggered"] is True


class TestConflictAndWindow:
    def test_opposite_direction_clears_all_votes(self):
        c = _c()
        c.vote("BTCUSDT", "LONG", "algopro")
        v = c.vote("BTCUSDT", "SHORT", "luxalgo")
        assert v["triggered"] is False
        assert v["votes"] == 1  # LONG oyları düştü, SHORT tek başına
        # LONG tarafı da temizlenmiş olmalı: yeni LONG oyu 1'den başlar.
        v2 = c.vote("BTCUSDT", "LONG", "osc")
        assert v2["votes"] == 1

    def test_expired_votes_drop_out_of_window(self, monkeypatch):
        import src.services.tv_confluence as m

        fake_now = [1000.0]
        monkeypatch.setattr(m.time, "time", lambda: fake_now[0])
        c = _c(window=60.0)
        c.vote("BTCUSDT", "LONG", "algopro")
        fake_now[0] += 120.0  # pencere aşıldı
        v = c.vote("BTCUSDT", "LONG", "luxalgo")
        assert v["triggered"] is False
        assert v["sources"] == ["luxalgo"]

    def test_fresh_vote_refreshes_timestamp(self, monkeypatch):
        import src.services.tv_confluence as m

        fake_now = [1000.0]
        monkeypatch.setattr(m.time, "time", lambda: fake_now[0])
        c = _c(window=60.0)
        c.vote("BTCUSDT", "LONG", "algopro")
        fake_now[0] += 50.0
        c.vote("BTCUSDT", "LONG", "algopro")  # tazeleme
        fake_now[0] += 50.0  # ilk oydan 100s, tazelemeden 50s
        v = c.vote("BTCUSDT", "LONG", "luxalgo")
        assert v["triggered"] is True

    def test_snapshot_reports_pending(self):
        c = _c()
        c.vote("BTCUSDT", "LONG", "algopro")
        snap = c.snapshot()
        assert len(snap) == 1
        assert snap[0]["symbol"] == "BTCUSDT"
        assert snap[0]["sources"] == ["algopro"]
