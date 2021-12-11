# Copyright (C) 2021 Acme Gating, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# The Python SDK for IBM Cloud uses a fork of boto which is
# incompatible with moto.

# So that we can mock our tests, this implements a fake IBM boto
# client.

import uuid
import datetime

import ibm_botocore.exceptions


class FakeIBMBoto:
    def __init__(self):
        self.buckets = {}

    def _make_object(self, key, data):
        uid = str(uuid.uuid4().hex)
        data = {
            'Key': 'fedora35.qcow2',
            'LastModified': datetime.datetime.utcnow(),
            'ETag': f'"{uid}"',
            'Size': len(data),
            'StorageClass': 'STANDARD',
            'Owner': {'DisplayName': 'something', 'ID': 'something'}
        }
        return data

    def list_objects(self, Bucket):
        if Bucket not in self.buckets:
            raise ibm_botocore.exceptions.ClientError({}, 'ListObjects')

        ret = {
            'ResponseMetadata': {
                'RequestId': 'something',
                'HostId': '',
                'HTTPStatusCode': 200,
                'HTTPHeaders': {},
                'RetryAttempts': 0
            },
            'IBMSSEKPEnabled': False,
            'IsTruncated': False,
            'Marker': '',
            'Contents': list(self.buckets[Bucket].values()),
            'Name': Bucket,
            'Prefix': '',
            'Delimiter': '',
            'MaxKeys': 1000
        }
        return ret

    def get_bucket_location(self, Bucket):
        ret = {
            'ResponseMetadata': {
                'RequestId': 'something',
                'HostId': '',
                'HTTPStatusCode': 200,
                'HTTPHeaders': {},
                'RetryAttempts': 0
            },
            'LocationConstraint': 'us-south-smart',
        }
        return ret

    def upload_fileobj(self, fobj, bucket_name, object_filename):
        data = fobj.read()
        obj = self._make_object(object_filename, data)
        objects = self.buckets.setdefault(bucket_name, {})
        objects[object_filename] = obj

    def delete_object(self, Bucket, Key):
        del self.buckets[Bucket][Key]


def client(*args, **kw):
    return FakeIBMBoto()
