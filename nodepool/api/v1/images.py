from pecan import conf, request, response, abort
from pecan.secure import secure
from pecan.decorators import expose
from pecan.rest import RestController
from nodepool import nodepool
from nodepool.api.auth import basic_auth


class ImagesController(RestController):

    _custom_actions = {
        'update': ['POST'],
    }

    @expose('json')
    def get_one(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        session = pool.getDB().getSession()

        image = session.getSnapshotImage(id)

        if not image:
            abort(404, "Image id not found")
        else:
            return image

    @expose('json')
    def get_all(self):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        session = pool.getDB().getSession()

        return session.getSnapshotImages()

    @secure(basic_auth.authenticated)
    @expose()
    def delete(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        pool.reconfigureManagers(pool.config)
        session = pool.getDB().getSession()

        image = session.getSnapshotImage(id)

        if not image:
            abort(404, 'Image id not found')
        else:
            pool.deleteImage(id)
            response.status = 204

    @secure(basic_auth.authenticated)
    @expose('json')
    def update(self, image_name):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        pool.reconfigureManagers(pool.config)
        session = pool.getDB().getSession()

        provider_name = request.POST.get('provider_name')

        if provider_name is None:
            abort(400, 'Request body parameter \'provider_name\' missing')
        elif provider_name not in pool.config.providers.keys():
            abort(404, 'Provider name not found')
        elif image_name not in \
                pool.config.providers[provider_name].images.keys():
            abort(404, 'Image name not found')
        else:
            pool.updateImage(session, provider_name, image_name)
            response.status = 201
