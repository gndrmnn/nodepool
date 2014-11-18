from nodepool.api.v1.nodes import NodesController
from nodepool.api.v1.images import ImagesController
from nodepool.api.v1.dib_images import DibImagesController


class V1Controller(object):
    nodes = NodesController()
    images = ImagesController()
    dib_images = DibImagesController()
