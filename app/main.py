import threading
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

import agent
import bot

if __name__ == "__main__":
    t = threading.Thread(target=agent.run, daemon=True, name="location-loop")
    t.start()
    bot.start()  # blocking — Socket Mode WebSocket
