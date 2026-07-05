import os


class Config:
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
    DEFAULT_PERIOD = os.getenv("DEFAULT_PERIOD", "6mo")
    CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "900"))
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")
