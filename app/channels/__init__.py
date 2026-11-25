from .mock import MockProvider
from .whatsapp_cloud import WhatsAppCloudProvider

# `provider` é o canal SIMULADO (números de teste). Números reais usam `cloud`.
provider = MockProvider()
cloud = WhatsAppCloudProvider()


def provider_for(channel):
    """Escolhe o provedor de acordo com o canal: teste -> simulado; real -> Cloud API da Meta."""
    return provider if channel.is_test else cloud
