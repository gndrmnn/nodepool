import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class DigitalOceanProviderConfig(ProviderConfig):
    def getSchema(self):
        pool_label = {
            v.Required('name'): str,
            v.Required('cloud-image'): str,
            v.Required('size'): str,
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [pool_label],
        })

        provider_cloud_images = {
            'name': str,
            'connection-type': str,
            'connection-port': int,
            'image-id': v.Any(str, int),
            'username': str,
            'ssh-keys': [str],
            'python-path': str,
        }

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
            v.Required('region'): str,
            'cloud-images': [provider_cloud_images],
            'boot-timeout': int,
            'launch-retries': int,
            'rate-limit': int
        })
        return v.Schema(provider)
