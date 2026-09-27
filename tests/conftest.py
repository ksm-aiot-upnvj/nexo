import socket
import asyncio
from urllib.parse import urlparse
import pytest
from db.session import DATABASE_URL, engine


def is_postgres_available() -> bool:
    """Check if PostgreSQL database is reachable on the configured host/port."""
    try:
        parsed = urlparse(DATABASE_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


requires_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL database is offline or unreachable",
)


@pytest.fixture(autouse=True, scope="function")
async def dispose_engine_after_test():
    yield
    await engine.dispose()

    pending = [t for t in asyncio.all_tasks() if t != asyncio.current_task()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    await asyncio.sleep(0)
