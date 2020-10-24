from nodepool.driver.simple import SimpleTaskManagerAdapter
from nodepool.driver.simple import SimpleTaskManagerInstance

class Droplet(SimpleTaskManagerInstance):
    def load(self, data):
        if data['status'] == 'active':
            self.ready = True

    def getQuotaInformation(self):
        pass

class DigitalOceanAdapter(SimpleTaskManagerAdapter):
    log = logging.getLogger("nodepool.driver.gce.GCEAdapter")

    def __init__(self, provider):
        self.provider = provider
        self.manager = digitalocean.Manager()

    def listInstances(self, task_manager):
        servers = []

        with task_manager.rateLimit():
            servers = self.manager.get_all_droplets(tag_name='nodepool-managed')

        return servers

    def createInstance(self, task_manager, hostname, metadata, label_config):
        tags = ["nodepool-managed"]
        droplet = self.manager.Droplet(
            name='Nodepool',
            region='fra1',
            image='ubuntu-20-04-x64',
            size_slug='s-1vcpu-1gb',
            tags=tags)

        with task_manager.rateLimit():
            droplet.create()

        return hostname

    def getQuotaForLabel(self, task_manager, label_config):
        pass

    def getQuotaLimits(self, task_manager):
        pass

    def deleteInstance(self, task_manager, droplet_id):
        with task_manager.rateLimit():
            droplets = self.manager.get_all_droplets(tag_name='nodepool-managed')
            for droplet in droplets:
                if droplet.id == droplet_id:
                    droplet.destroy()
                    break
