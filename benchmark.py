import asyncio
import aiohttp
import time
from aiohttp import web

async def handle(request):
    return web.Response(text="ok")

async def start_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()
    return runner

async def benchmark_inside(url, retries=500):
    start = time.time()
    for _ in range(retries):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                await response.text()
    return time.time() - start

async def benchmark_outside(url, retries=500):
    start = time.time()
    async with aiohttp.ClientSession() as session:
        for _ in range(retries):
            async with session.get(url) as response:
                await response.text()
    return time.time() - start

async def main():
    runner = await start_server()
    url = "http://localhost:8080/"

    inside_time = await benchmark_inside(url, 1000)
    print(f"Inside loop (unpooled): {inside_time:.4f}s")

    outside_time = await benchmark_outside(url, 1000)
    print(f"Outside loop (pooled): {outside_time:.4f}s")

    await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
