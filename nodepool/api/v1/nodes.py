from pecan import conf, request, response, abort
from pecan.secure import secure
from pecan.rest import RestController
from pecan.decorators import expose
from nodepool import nodepool, nodedb
from nodepool.api.auth import basic_auth


class NodesController(RestController):

    @expose('json')
    def get_one(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        session = pool.getDB().getSession()

        node = session.getNode(id)

        if not node:
            abort(404, "Node id not found")
        else:
            return node

    @expose('json')
    def get_all(self):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        session = pool.getDB().getSession()

        return session.getNodes()

    @secure(basic_auth.authenticated)
    @expose()
    def delete(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        pool.reconfigureManagers(pool.config)
        session = pool.getDB().getSession()

        node = session.getNode(id)

        if not node:
            abort(404, "Node id not found")
        else:
            node.state = nodedb.DELETE
            response.status = 204

    @secure(basic_auth.authenticated)
    @expose('json')
    def put(self, id):
        pool = nodepool.NodePool(conf.nodepool_conf_file)
        config = pool.loadConfig()
        pool.reconfigureDatabase(config)
        pool.setConfig(config)
        pool.reconfigureManagers(pool.config)
        session = pool.getDB().getSession()

        node = session.getNode(id)

        state = request.POST.get('state')

        if not node:
            abort(404, 'Node id not found')
        elif state is None:
            abort(400, 'Request body parameter \'state\' missing')
        elif state not in ('hold', 'ready'):
            abort(400, 'Node state not permitted')
        elif state == 'ready' and node.state != nodedb.HOLD:
            abort(400, 'State \'ready\' can only be set on nodes '
                       'in \'hold\' state')
        elif state == 'ready':
            node.state = nodedb.READY
            response.status = 200
        else:
            node.state = nodedb.HOLD
            response.status = 200
