from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация бота, читается из .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str = ""
    GROUP_CHAT_ID: int = 0  # ID закрытого чата специалистов (например, -1001234567890)
    ADMIN_IDS: str = ""  # Telegram ID администраторов через запятую

    @property
    def admin_ids(self) -> list[int]:
        """Распарсенный список ID администраторов."""
        ids = []
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids
    DB_PATH: str = "./data/bot_database.db"
    PHOTOS_DIR: str = "./data/photos"

    # Сколько фото максимум можно прикрепить к заявке в ТП
    MAX_PHOTOS: int = 3

    # Веб-админка (запускается вместе с ботом, если WEB_ENABLED и задан WEB_PASSWORD)
    WEB_ENABLED: bool = False
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8080
    WEB_PASSWORD: str = ""


settings = Settings()
