from ..config import settings
from .mock import MockProvider
from .whatsapp_cloud import WhatsAppCloudProvider

provider = WhatsAppCloudProvider() if settings.channel_provider == "whatsapp" else MockProvider()
