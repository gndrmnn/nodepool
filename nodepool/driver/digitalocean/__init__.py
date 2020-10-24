from nodepool.driver.simple import SimpleTaskManagerDriver
from nodepool.driver.digitalocean.config import DigitalOceanProviderConfig
from nodepool.driver.digitalocean.adapter import DigitalOceanAdapter


class DigitalOceanAdapterDriver(SimpleTaskManagerDriver):
    def getProviderConfig(self, provider):
        return DigitalOceanProviderConfig(self, provider)

    def getAdapter(self, provider_config):
        return DigitalOceanAdapter(provider_config)
