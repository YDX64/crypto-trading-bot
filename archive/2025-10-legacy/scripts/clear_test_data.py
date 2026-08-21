#!/usr/bin/env python3
"""Clear test data from database"""

import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.database import AsyncSessionLocal, init_db
from src.models.waiting_signal import WaitingSignalModel
from src.models.signal import SignalModel
from sqlalchemy import select, delete

async def clear_test_data():
    """Clear all test data"""
    await init_db()

    async with AsyncSessionLocal() as session:
        # Clear BTCUSDT test signals
        result = await session.execute(
            delete(WaitingSignalModel).where(WaitingSignalModel.symbol == "BTCUSDT")
        )
        print(f"Deleted {result.rowcount} BTCUSDT waiting signals")

        # Clear test signals from signals table
        result = await session.execute(
            delete(SignalModel).where(SignalModel.coin == "BTCUSDT")
        )
        print(f"Deleted {result.rowcount} BTCUSDT signals")

        await session.commit()
        print("✅ Test data cleared!")

if __name__ == "__main__":
    asyncio.run(clear_test_data())