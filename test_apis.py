import httpx
import asyncio
import os

async def test_api():
    base_url = "http://localhost:8000"
    admin_password = os.getenv("ADMIN_PASSWORD", "admin")

    async with httpx.AsyncClient() as client:
        print("1. Testing /api/login")
        resp = await client.post(
            f"{base_url}/api/login",
            data={"username": "admin", "password": admin_password},
        )
        if resp.status_code != 200:
            print("Login failed:", resp.text)
            return
            
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("Login successful! Token acquired.")
        
        print("\n2. Testing /api/symbols")
        resp = await client.get(f"{base_url}/api/symbols", headers=headers)
        print(f"Status: {resp.status_code}")
        print(resp.json())
        
        print("\n3. Testing /api/config")
        resp = await client.get(f"{base_url}/api/config", headers=headers)
        print(f"Status: {resp.status_code}")
        print(resp.json())
        
        print("\n4. Testing /api/bot/status")
        resp = await client.get(f"{base_url}/api/bot/status", headers=headers)
        print(f"Status: {resp.status_code}")
        print(resp.json())
        
        print("\n5. Testing /api/performance/summary")
        resp = await client.get(f"{base_url}/api/performance/summary", headers=headers)
        print(f"Status: {resp.status_code}")
        print(resp.json())

if __name__ == "__main__":
    asyncio.run(test_api())
