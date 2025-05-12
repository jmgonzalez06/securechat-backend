import asyncio
from server import app, start_server
from hypercorn.asyncio import serve # type: ignore
from hypercorn.config import Config # type: ignore

async def main():
    # Start WebSocket server
    print("[Main] Launching WebSocket server...")
    await start_server
    print("[Main] WebSocket server started.")

# Prepare Flask config and WebSocket task
if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    # WebSocket server coroutine
    loop.create_task(main())

    # Run Flask app via Hypercorn
    config = Config()
    config.bind = [f"0.0.0.0:{app.config['PORT']}"]
    loop.run_until_complete(serve(app, config))
