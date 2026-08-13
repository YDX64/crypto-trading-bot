from src.trading.symbol_reservations import SymbolReservationRegistry


def test_foreign_owner_cannot_claim_or_release_symbol():
    registry = SymbolReservationRegistry()

    assert registry.reserve("btcusdt", "scalper") is True
    assert registry.reserve("BTCUSDT", "telegram") is False
    assert registry.release("BTCUSDT", "telegram") is False
    assert registry.owner("BTCUSDT") == "scalper"
    assert registry.release("BTCUSDT", "scalper") is True


def test_capacity_combines_exchange_positions_and_pending_reservations():
    registry = SymbolReservationRegistry()

    assert registry.reserve(
        "ETHUSDT", "scalper", capacity=2, exchange_symbols={"BTCUSDT"}
    ) is True
    assert registry.reserve(
        "SOLUSDT", "telegram", capacity=2, exchange_symbols={"BTCUSDT"}
    ) is False


def test_same_owner_reacquire_is_idempotent_even_at_capacity():
    registry = SymbolReservationRegistry()

    assert registry.reserve("BTCUSDT", "scalper", capacity=1) is True
    assert registry.reserve("BTCUSDT", "scalper", capacity=1) is True
    assert registry.snapshot() == {"BTCUSDT": "scalper"}
