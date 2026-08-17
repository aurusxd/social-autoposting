from aiogram import Bot


class TelegramService:
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)

    async def post_message(self, chat_id: int, text: str):
        await self.bot.send_message(chat_id=chat_id, text=text)
