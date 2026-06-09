import asyncio
import time
import os
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import aiohttp

# Mock the environment to point to a local server instead of actual telegram
os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"

class MockTelegramHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass # Suppress logging

def run_server(server_class=ThreadingHTTPServer, handler_class=MockTelegramHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    httpd.serve_forever()

def send_telegram_alert_sync(message: str, url: str):
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        pass

async def benchmark_sync(n_requests, url):
    loop = asyncio.get_running_loop()
    start_time = time.time()
    tasks = []
    # simulate event loop running concurrently
    for i in range(n_requests):
        tasks.append(loop.run_in_executor(None, send_telegram_alert_sync, f"test {i}", url))
    await asyncio.gather(*tasks)
    return time.time() - start_time

async def send_telegram_alert_async(message: str, url: str, session):
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    data = {'chat_id': chat_id, 'text': message}
    try:
        async with session.post(url, data=data, timeout=5) as response:
            await response.read()
    except Exception as e:
        pass

async def benchmark_async(n_requests, url):
    start_time = time.time()
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(n_requests):
            tasks.append(send_telegram_alert_async(f"test {i}", url, session))
        await asyncio.gather(*tasks)
    return time.time() - start_time

if __name__ == "__main__":
    # Start mock server
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Give it a moment to start
    time.sleep(1)

    url = "http://localhost:8080/sendMessage"

    n_requests = 1000
    print(f"Running benchmark with {n_requests} requests...")

    duration_sync = asyncio.run(benchmark_sync(n_requests, url))
    print(f"Sync approach time (using run_in_executor): {duration_sync:.4f} seconds")
    print(f"Requests per second: {n_requests / duration_sync:.2f}")

    duration_async = asyncio.run(benchmark_async(n_requests, url))
    print(f"Async approach time: {duration_async:.4f} seconds")
    print(f"Requests per second: {n_requests / duration_async:.2f}")

    improvement = ((duration_sync - duration_async) / duration_sync) * 100
    print(f"Performance Improvement: {improvement:.2f}%")
