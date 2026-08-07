#!/usr/bin/env python
"""
Demo (testnet) hesabı için komut satırı yönetim aracı.

Botu çalıştırmadan hesabın durumunu görmek, pozisyon açmak/kapatmak ve
koruma emirlerini denetlemek için kullanılır.

GÜVENLİK: Mainnet'e karşı çalışmayı reddeder.

Kullanım:
    .venv/bin/python demo_control.py status      # hesap + pozisyon + emirler
    .venv/bin/python demo_control.py orders      # açık emirler (normal + koşullu)
    .venv/bin/python demo_control.py history     # son işlemler
    .venv/bin/python demo_control.py close BTCUSDT   # pozisyonu kapat + emirleri temizle
    .venv/bin/python demo_control.py close-all       # tüm pozisyonları kapat
    .venv/bin/python demo_control.py protect BTCUSDT # korumasız pozisyona SL koy
"""

import asyncio
import sys
from datetime import datetime

from src.core.config import settings, TESTNET_HOSTS
from src.trading.binance_client_improved import ImprovedBinanceClient, BinanceAPIError
from src.trading.position_manager import PositionManager

# Varsayılan koruma mesafesi: SL, girişten bu kadar uzağa konur
DEFAULT_SL_PCT = 8.0


def guard_testnet() -> None:
    if not any(h in settings.binance_base_url for h in TESTNET_HOSTS):
        print(
            f"❌ GÜVENLİK: {settings.binance_base_url} bir demo/testnet adresi değil.\n"
            f"   Bu araç gerçek parayla çalıştırılamaz."
        )
        sys.exit(1)


def fmt(value, digits=2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


async def cmd_status(client: ImprovedBinanceClient) -> None:
    account = await client._request_with_retry("GET", "/fapi/v2/account", signed=True)
    positions = [p for p in account.get("positions", []) if float(p["positionAmt"]) != 0]

    print(f"\n🌐 Ortam : {settings.binance_base_url}")
    print(f"💰 Cüzdan: {fmt(account['totalWalletBalance'])} USDT   "
          f"Kullanılabilir: {fmt(account['availableBalance'])} USDT")
    print(f"📈 Gerçekleşmemiş K/Z: {fmt(account['totalUnrealizedProfit'])} USDT")

    if not positions:
        print("\n📭 Açık pozisyon yok")
        return

    print(f"\n📊 {len(positions)} açık pozisyon:")
    for p in positions:
        symbol = p["symbol"]
        amt = float(p["positionAmt"])
        side = "LONG " if amt > 0 else "SHORT"
        entry = float(p["entryPrice"])
        pnl = float(p["unrealizedProfit"])
        notional = abs(amt) * entry
        leverage = p.get("leverage", "?")

        print(f"\n  {symbol}  {side}  {leverage}x")
        print(f"    Miktar : {abs(amt)}   Giriş: {fmt(entry)}   Nominal: {fmt(notional)} USDT")
        print(f"    K/Z    : {fmt(pnl)} USDT")

        # Koruma emirleri koşullu emir ad alanındadır
        try:
            algo = await client.get_open_algo_orders(symbol)
        except Exception as e:
            print(f"    ⚠️ Koşullu emirler okunamadı: {e}")
            continue

        stops = [o for o in algo if o.get("orderType") in ("STOP_MARKET", "STOP")]
        tps = [o for o in algo if o.get("orderType") in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")]

        if stops:
            for s in stops:
                risk = (entry - float(s["triggerPrice"])) * abs(amt)
                if amt < 0:
                    risk = -risk
                print(f"    🛡️ SL   : {fmt(s['triggerPrice'])}  (risk ≈ {fmt(risk)} USDT)")
        else:
            print("    🚨 STOP-LOSS YOK — POZİSYON KORUMASIZ!")

        for t in tps:
            print(f"    🎯 TP   : {fmt(t['triggerPrice'])}  miktar={t.get('quantity')}")


async def cmd_orders(client: ImprovedBinanceClient, symbol: str = "BTCUSDT") -> None:
    normal = await client.get_open_orders(symbol)
    algo = await client.get_open_algo_orders(symbol)

    print(f"\n📋 {symbol} açık emirler")
    print(f"\n  Normal emirler ({len(normal)}):")
    for o in normal:
        print(f"    {o['type']:20s} {o['side']:5s} qty={o.get('origQty')} price={o.get('price')}")
    if not normal:
        print("    (yok)")

    print(f"\n  Koşullu emirler ({len(algo)}) — /fapi/v1/openOrders bunları GÖSTERMEZ:")
    for o in algo:
        print(f"    {o['orderType']:22s} {o['side']:5s} tetik={fmt(o.get('triggerPrice'))} "
              f"reduceOnly={o.get('reduceOnly')} algoId={o.get('algoId')}")
    if not algo:
        print("    (yok)")


async def cmd_history(client: ImprovedBinanceClient, symbol: str = "BTCUSDT") -> None:
    trades = await client._request_with_retry(
        "GET", "/fapi/v1/userTrades", params={"symbol": symbol, "limit": 25}, signed=True
    )
    print(f"\n🧾 {symbol} son {len(trades)} işlem:")
    total_pnl = 0.0
    total_fee = 0.0
    for t in trades:
        ts = datetime.fromtimestamp(t["time"] / 1000).strftime("%m-%d %H:%M:%S")
        pnl = float(t["realizedPnl"])
        fee = float(t["commission"])
        total_pnl += pnl
        total_fee += fee
        print(f"  {ts}  {t['side']:4s} {t['qty']:>9s} @ {fmt(t['price'])}  "
              f"K/Z={fmt(pnl)}  komisyon={fmt(fee, 4)}")
    print(f"\n  Toplam K/Z: {fmt(total_pnl)} USDT   Toplam komisyon: {fmt(total_fee, 4)} USDT")


async def cmd_close(client: ImprovedBinanceClient, symbol: str) -> None:
    pm = PositionManager(client)
    pos = await client.get_position_risk(symbol)
    if not pos or float(pos["positionAmt"]) == 0:
        print(f"📭 {symbol}: kapatılacak pozisyon yok")
    else:
        print(f"🔻 {symbol}: {pos['positionAmt']} kapatılıyor...")
        if not await pm._emergency_close(symbol):
            print(f"❌ {symbol}: kapatılamadı — ELLE MÜDAHALE GEREKİR")
            return
    await client.cancel_all_open_orders(symbol)
    print(f"✅ {symbol}: pozisyon kapalı, emirler temizlendi")


async def cmd_close_all(client: ImprovedBinanceClient) -> None:
    positions = await client.get_all_positions()
    if not positions:
        print("📭 Açık pozisyon yok")
        return
    for p in positions:
        await cmd_close(client, p["symbol"])


async def cmd_protect(client: ImprovedBinanceClient, symbol: str) -> None:
    """Korumasız bir pozisyona stop-loss koy."""
    pos = await client.get_position_risk(symbol)
    if not pos or float(pos["positionAmt"]) == 0:
        print(f"📭 {symbol}: açık pozisyon yok")
        return

    amt = float(pos["positionAmt"])
    entry = float(pos["entryPrice"])

    existing = [o for o in await client.get_open_algo_orders(symbol)
                if o.get("orderType") in ("STOP_MARKET", "STOP")]
    if existing:
        print(f"✅ {symbol}: zaten stop-loss var @ {fmt(existing[0]['triggerPrice'])}")
        return

    if amt > 0:
        stop_price = entry * (1 - DEFAULT_SL_PCT / 100)
        side = "SELL"
    else:
        stop_price = entry * (1 + DEFAULT_SL_PCT / 100)
        side = "BUY"

    try:
        order = await client.place_stop_loss(symbol, side, stop_price, close_position=True)
        print(f"🛡️ {symbol}: stop-loss kondu @ {fmt(stop_price)} "
              f"(girişten %{DEFAULT_SL_PCT}) algoId={order.get('algoId')}")
    except BinanceAPIError as e:
        print(f"❌ {symbol}: stop-loss konulamadı (kod={e.code}): {e.msg}")


async def main() -> None:
    guard_testnet()

    args = sys.argv[1:]
    command = args[0] if args else "status"
    target = args[1] if len(args) > 1 else "BTCUSDT"

    client = ImprovedBinanceClient()
    try:
        if command == "status":
            await cmd_status(client)
        elif command == "orders":
            await cmd_orders(client, target)
        elif command == "history":
            await cmd_history(client, target)
        elif command == "close":
            await cmd_close(client, target)
        elif command == "close-all":
            await cmd_close_all(client)
        elif command == "protect":
            await cmd_protect(client, target)
        else:
            print(__doc__)
            sys.exit(1)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
