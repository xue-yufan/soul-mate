import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from app.config import load_settings
from app.storage.db_SQlite import init_db, fetch_all

async def main():
    s = load_settings()
    await init_db(s)
    rows = await fetch_all(
        s,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )
    print([r["name"] for r in rows])

asyncio.run(main())
