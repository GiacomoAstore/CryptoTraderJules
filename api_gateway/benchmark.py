import asyncio
import time
import os
import urllib.request
import urllib.parse
from unittest.mock import patch
import aiohttp

def mock_urlopen(req, timeout=5):
    time.sleep(0.05)

def send_telegram_alert_sync(message: str):
    bot_token = "dummy_token"
    chat_id = "dummy_chat"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        pass

async def benchmark_sync():
    start = time.time()
    tasks = []
    for i in range(100):
        tasks.append(asyncio.get_event_loop().run_in_executor(None, send_telegram_alert_sync, f"msg {i}"))
    await asyncio.gather(*tasks)
    return time.time() - start

async def mock_post(*args, **kwargs):
    await asyncio.sleep(0.05)
    class MockResponse:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    return MockResponse()

async def send_telegram_alert_async(session, message: str):
    bot_token = "dummy_token"
    chat_id = "dummy_chat"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': message}
    try:
        async with session.post(url, data=data, timeout=5) as response:
            pass
    except Exception as e:
        pass

async def benchmark_async():
    start = time.time()
    async with aiohttp.ClientSession() as session:
        # Patching post
        session.post = mock_post
        tasks = []
        for i in range(100):
            tasks.append(send_telegram_alert_async(session, f"msg {i}"))
        await asyncio.gather(*tasks)
    return time.time() - start

async def main():
    with patch('urllib.request.urlopen', side_effect=mock_urlopen):
        sync_dur = await benchmark_sync()
        print(f"Sync (with executor) 100 requests took: {sync_dur:.4f} seconds")

    async_dur = await benchmark_async()
    print(f"Async (with aiohttp) 100 requests took: {async_dur:.4f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
