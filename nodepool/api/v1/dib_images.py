from pecan import conf, request, response, abort
from pecan.decorators import expose
from pecan.secure import secure
from pecan.rest import RestController
from nodepool import nodepool
from nodepool.api.auth import basic_auth


class DibImagesController(RestController):

    _custom_actions = {
        'build': ['POST'],
        'upload': ['POST'],
    }

    @expose('json')
    def get_one(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        session = pool.getDB().getSession()

        dib_image = session.getDibImage(id)

        if not dib_image:
            abort(404, 'Dib image id not found')
        else:
            return dib_image

    @expose('json')
    def get_all(self):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        session = pool.getDB().getSession()

        dib_images = session.getDibImages()

        return dib_images

    @secure(basic_auth.authenticated)
    @expose()
    def delete(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        pool.reconfigureManagers(pool.config)
        session = pool.getDB().getSession()

        dib_image = session.getDibImage(id)

        if not dib_image:
            abort(404, "Dib image id not found")
        else:
            pool.deleteDibImage(dib_image)
            response.status = 204

    @secure(basic_auth.authenticated)
    @expose('json')
    def build(self, dib_image_name):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)

        if dib_image_name not in pool.config.diskimages.keys():
            abort(404, 'Dib image name not found')
        else:
            pool.reconfigureImageBuilder()
            pool.buildImage(pool.config.diskimages[dib_image_name])
            pool.waitForBuiltImages()
            response.status = 201

    @secure(basic_auth.authenticated)
    @expose('json')
    def upload(self, dib_image_name):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        pool.reconfigureManagers(pool.config)

        provider_name = request.POST.get('provider_name')

        if provider_name is None:
            abort(400, 'Request body parameter \'provider_name\' missing')
        elif provider_name not in pool.config.providers.keys():
            abort(404, 'Provider name not found')
        elif dib_image_name not in pool.config.diskimages.keys():
            abort(404, 'Dib image name not found')
        else:
            session = pool.getDB().getSession()
            pool.uploadImage(session, provider_name, dib_image_name)
            response.status = 201
