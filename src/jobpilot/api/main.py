"""ASGI entry point: `uvicorn jobpilot.api.main:app`."""

from jobpilot.api.app import create_app
from jobpilot.core.config import AppConfig, get_settings
from jobpilot.core.context import AppContext

app = create_app(AppContext(settings=get_settings(), config=AppConfig.load()))
