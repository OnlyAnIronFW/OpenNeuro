"""Discord platform adapter (not implemented)"""

from .base import PlatformAdapter


class DiscordAdapter(PlatformAdapter):
    async def connect(self) -> bool:
        print("[Discord] Not implemented")
        return False

    async def disconnect(self) -> None:
        pass

    async def send_message(self, text: str) -> bool:
        return False
