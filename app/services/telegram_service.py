from app.core.config import TelegramAPIConfig
from app.services.telegram_client import build_telegram_bot


class TelegramService:
    def __init__(self, bot_token: str, api_config: TelegramAPIConfig):
        self.bot = build_telegram_bot(bot_token, api_config)

    async def post_message(self, chat_id: int, text: str):
        await self.bot.send_message(chat_id=chat_id, text=text)

    async def close(self) -> None:
        await self.bot.session.close()
