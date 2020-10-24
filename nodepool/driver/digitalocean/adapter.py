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
            result = self.manager.get_all_droplets()

        return servers

    def deleteInstance(self, task_manager, droplet_id):
        with task_manager.rateLimit():
            droplets = self.manager.get_all_droplets()
            for droplet in droplets:
                if droplet.id == droplet_id:
                    droplet.destroy()
                    break
