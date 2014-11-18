import base64
from pecan import conf, request


def authenticated():
    if not request.authorization:
        return False
    else:
        decoded_password = base64.decodestring(request.authorization[1])
        user, password = decoded_password.split(':')

        return user == conf.api_user and password == conf.api_password
