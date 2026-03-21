import asyncio
import aiohttp

async def main():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get('http://localhost:8000/restAPI/System/Ready') as r:
                print(f"Status: {r.status}")
                data = await r.json()
                print(f"JSON: {data}")
                print(f"ready type: {type(data.get('ready'))}")
    except Exception as e:
        print(f"Exception: {e}")

asyncio.run(main())
