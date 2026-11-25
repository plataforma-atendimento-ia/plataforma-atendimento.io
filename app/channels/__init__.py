from .bridge import BridgeProvider
from .mock import MockProvider
from .whatsapp_cloud import WhatsAppCloudProvider

# `provider` é o canal SIMULADO (números de teste). Números reais usam `cloud` (Meta) ou `qr` (ponte não oficial).
provider = MockProvider()
cloud = WhatsAppCloudProvider()
qr = BridgeProvider()


def provider_for(channel):
    """Escolhe o provedor de acordo com o canal: teste -> simulado; QR -> ponte; demais -> Cloud API da Meta."""
    if channel.is_test:
        return provider
    return qr if channel.via == "qr" else cloud
