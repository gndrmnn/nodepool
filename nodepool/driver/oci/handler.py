# Copyright 2017 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import json
import socket
import random

from nodepool import zk
from nodepool import nodeutils as utils
from nodepool.driver import NodeRequestHandler


class OpenContainerNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.oci.handler."
                            "OpenContainerNodeRequestHandler")

    def run_handler(self):
        self._setFromPoolWorker()
        label = None
        for pool in self.provider.pools.values():
            for node_type in self.request.node_types:
                if node_type in pool.labels:
                    label = pool.labels[node_type]

        if label:
            self.log.debug("Starting container for %s" % self.request)
            hostid = "%s-%s" % (label.name, self.request.id)
            client, port = self.start_container(hostid)
            node = zk.Node()
            node.state = zk.READY
            node.external_id = hostid
            node.hostname = self.provider.hypervisor
            node.interface_ip = socket.gethostbyname(self.provider.hypervisor)
            node.public_ipv4 = node.interface_ip
            node.host_keys = utils.keyscan(node.interface_ip, port=port)
            if not node.host_keys:
                raise RuntimeError("Couldn't get host key")
            node.ssh_port = port
            node.client = client
            node.provider = self.provider.name
            node.type = label.name
            self.nodeset.append(node)
            self.zk.storeNode(node)
        else:
            self.log.warning("No containers can handle %s" % self.request)
            self.request.declined_by.append(self.launcher_id)
            self.unlockNodeSet(clear_allocation=True)
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True

    def start_container(self, hostid):
        # TODO: better port selection...
        port = random.randint(22022, 52022)
        bundle_path = "/var/lib/nodepool/oci/%s" % hostid
        cmds = [
            "mkdir -p %s/rootfs" % bundle_path,
            "cat > %s/config.json" % bundle_path,
            "mount -o bind,ro --make-private / %s/rootfs" % bundle_path,
        ]
        self.log.debug("Executing %s" % cmds)
        client = self.manager.getClient()
        stdin, _, _ = client.exec_command("; ".join(cmds))
        stdin.write(self.render_config(hostid, port))
        stdin.close()
        client.close()
        client = self.manager.getClient()
        cmd = 'runc run --bundle %s %s' % (bundle_path, hostid)
        self.log.info("Executing %s" % cmd)
        client.exec_command(cmd)
        return client, port

    def render_config(self, hostid, port):
        config = {
            "ociVersion": "1.0.0-rc5",
            "platform": {"os": "linux", "arch": "amd64"},
            "process": {
                "terminal": False,
                "user": {"uid": 0, "gid": 0},
                "args": [],
                "env": ["PATH=/sbin:/bin", "TERM=xterm"],
                "cwd": "/",
                "capabilities": {
                    "bounding": [
                        "CAP_AUDIT_WRITE", "CAP_KILL", "CAP_NET_BIND_SERVICE"],
                    "effective": [
                        "CAP_AUDIT_WRITE", "CAP_KILL", "CAP_NET_BIND_SERVICE"],
                    "inheritable": [
                        "CAP_AUDIT_WRITE", "CAP_KILL", "CAP_NET_BIND_SERVICE"],
                    "permitted": [
                        "CAP_AUDIT_WRITE", "CAP_KILL", "CAP_NET_BIND_SERVICE"],
                    "ambient": [
                        "CAP_AUDIT_WRITE", "CAP_KILL", "CAP_NET_BIND_SERVICE"],
                },
                "rlimits": [
                    {"type": "RLIMIT_NOFILE", "hard": 1024, "soft": 1024},
                ],
                "noNewPrivileges": False
            },
            "root": {"path": "rootfs", "readonly": True},
            "hostname": "runc",
            "mounts": [
                {
                    "destination": "/proc",
                    "type": "proc",
                    "source": "proc"
                },
                {
                    "destination": "/dev",
                    "type": "tmpfs",
                    "source": "tmpfs",
                    "options": [
                        "nosuid",
                        "strictatime",
                        "mode=755",
                        "size=65536k"
                    ]
                },
                {
                    "destination": "/dev/pts",
                    "type": "devpts",
                    "source": "devpts",
                    "options": [
                        "nosuid",
                        "noexec",
                        "newinstance",
                        "ptmxmode=0666",
                        "mode=0620",
                        "gid=5"
                    ]
                },
                {
                    "destination": "/dev/shm",
                    "type": "tmpfs",
                    "source": "shm",
                    "options": [
                        "nosuid",
                        "noexec",
                        "nodev",
                        "mode=1777",
                        "size=65536k"
                    ]
                },
                {
                    "destination": "/dev/mqueue",
                    "type": "mqueue",
                    "source": "mqueue",
                    "options": [
                        "nosuid",
                        "noexec",
                        "nodev"
                    ]
                },
                {
                    "destination": "/sys",
                    "type": "sysfs",
                    "source": "sysfs",
                    "options": [
                        "nosuid",
                        "noexec",
                        "nodev",
                        "ro"
                    ]
                },
                {
                    "destination": "/sys/fs/cgroup",
                    "type": "cgroup",
                    "source": "cgroup",
                    "options": [
                        "nosuid",
                        "noexec",
                        "nodev",
                        "relatime",
                        "ro"
                    ]
                },
                {
                    "destination": "/tmp",
                    "type": "tmpfs",
                    "source": "shm",
                    "options": [
                        "nosuid",
                        "noexec",
                        "nodev",
                        "mode=1777",
                        "size=2G"
                    ]
                },
            ],
            "linux": {
                "resources": {
                    "devices": [{"allow": False, "access": "rwm"}],
                },
                "namespaces": [
                    {"type": "pid"},
                    {"type": "ipc"},
                    {"type": "uts"},
                    {"type": "mount"},
                ],
                "maskedPaths": [
                    "/proc/kcore",
                    "/proc/latency_stats",
                    "/proc/timer_list",
                    "/proc/timer_stats",
                    "/proc/sched_debug",
                    "/sys/firmware"
                ],
                "readonlyPaths": [
                    "/proc/asound",
                    "/proc/bus",
                    "/proc/fs",
                    "/proc/irq",
                    "/proc/sys",
                    "/proc/sysrq-trigger"
                ]
            }
        }

        # Simple sshd container
        config["process"]["args"] = [
            "/sbin/sshd", "-p", str(port), "-e", "-D",
            "-o", "UsePrivilegeSeparation=no", "-o", "UsePAM=no"
        ]
        for cap in config["process"]["capabilities"].values():
            for cap_need in ("CAP_SETUID", "CAP_SETGID", "CAP_IPC_LOCK"):
                if cap_need not in cap:
                    cap.append(cap_need)
        config["hostname"] = hostid
        return json.dumps(config)
