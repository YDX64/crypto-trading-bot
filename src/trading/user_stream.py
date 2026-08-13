"""Binance USDⓈ-M user-data stream: reconnect + listenKey keepalive.

REST polling scalper için daima yedek kalır; bu sınıf yalnızca
ORDER_TRADE_UPDATE olayını daha düşük gecikmeyle callback'e iletir.
listenKey endpoint'leri ImprovedBinanceClient tarafından API-key header ile,
HMAC imzası olmadan çağrılır.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets

from src.core.logger import app_logger
from src.trading.binance_client_improved import ImprovedBinanceClient


OrderUpdateCallback = Callable[[Dict[str, Any]], Awaitable[Any]]


class BinanceUserDataStream:
    """Reconnect eden USDⓈ-M user stream yöneticisi."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        on_order_update: OrderUpdateCallback,
        *,
        ws_base_url: Optional[str] = None,
        keepalive_seconds: float = 1800.0,
        reconnect_min_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        connect_factory: Optional[Callable[..., Any]] = None,
    ):
        self.client = client
        self.on_order_update = on_order_update
        self.ws_base_url = (
            ws_base_url or self._derive_ws_base_url(client.base_url)
        ).rstrip("/")
        self.keepalive_seconds = max(60.0, float(keepalive_seconds))
        self.reconnect_min_seconds = max(0.1, float(reconnect_min_seconds))
        self.reconnect_max_seconds = max(
            self.reconnect_min_seconds, float(reconnect_max_seconds)
        )
        self._connect = connect_factory or websockets.connect
        self._task: Optional[asyncio.Task] = None
        self._websocket: Any = None
        self.running = False
        self.connected = False
        self.last_event_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.reconnect_count = 0
        self.logger = app_logger

    @staticmethod
    def _derive_ws_base_url(rest_base_url: str) -> str:
        host = rest_base_url.lower()
        if any(
            marker in host
            for marker in (
                "testnet.binancefuture.com",
                "demo-fapi.binance.com",
                "demo.binance.com",
            )
        ):
            return "wss://fstream.binancefuture.com"
        return "wss://fstream.binance.com"

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(
            self.run(), name="binance-user-data-stream"
        )

    async def run(self) -> None:
        """Bağlantı kopsa da running=True iken exponential backoff ile dön."""
        self.running = True
        backoff = self.reconnect_min_seconds
        while self.running:
            keepalive_task: Optional[asyncio.Task] = None
            try:
                listen_key = await self.client.create_listen_key()
                ws_url = f"{self.ws_base_url}/ws/{listen_key}"
                async with self._connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=5,
                    max_queue=1024,
                ) as websocket:
                    self._websocket = websocket
                    self.connected = True
                    self.last_error = None
                    backoff = self.reconnect_min_seconds
                    keepalive_task = asyncio.create_task(
                        self._keepalive_loop(),
                        name="binance-listen-key-keepalive",
                    )
                    self.logger.info("🔌 Binance user-data stream bağlandı")

                    async for raw_message in websocket:
                        if not self.running:
                            break
                        try:
                            event = json.loads(raw_message)
                        except (TypeError, json.JSONDecodeError) as e:
                            self.logger.warning(
                                f"Binance user stream geçersiz JSON atlıyor: {e}"
                            )
                            continue
                        self.last_event_at = time.time()
                        if event.get("e") == "listenKeyExpired":
                            self.client.invalidate_listen_key()
                            self.logger.warning(
                                "Binance listenKey süresi doldu; yeniden bağlanılacak"
                            )
                            break
                        if event.get("e") == "ORDER_TRADE_UPDATE":
                            await self.on_order_update(event)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self.logger.error(
                    f"Binance user-data stream koptu: {self.last_error}"
                )
            finally:
                self.connected = False
                self._websocket = None
                if keepalive_task is not None:
                    keepalive_task.cancel()
                    await asyncio.gather(keepalive_task, return_exceptions=True)

            if self.running:
                self.reconnect_count += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, self.reconnect_max_seconds)

    async def _keepalive_loop(self) -> None:
        while self.running and self.connected:
            await asyncio.sleep(self.keepalive_seconds)
            try:
                await self.client.keepalive_listen_key()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"keepalive {type(e).__name__}: {e}"
                self.logger.error(
                    f"Binance listenKey keepalive başarısız: {e}; WS yeniden kurulacak"
                )
                websocket = self._websocket
                if websocket is not None:
                    await websocket.close()
                return

    async def stop(self) -> None:
        self.running = False
        websocket = self._websocket
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            await self.client.delete_listen_key()
        except Exception as e:
            self.logger.warning(f"Binance listenKey kapatılamadı: {e}")
        self.connected = False
