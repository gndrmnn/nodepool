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

import paramiko
import paramiko.client

from nodepool.driver import Provider
from nodepool.nodeutils import keyscan

USED_PORTS='ss --t -l -n | cut -d: -f2 | awk \'/[0-9]/ { print $1 }\''


class OpenContainerProvider(Provider):
    log = logging.getLogger("nodepool.driver.oci.OpenContainerProvider")

    def __init__(self, provider):
        self.provider = provider
        self.hypervisor = socket.gethostbyname(provider.hypervisor)
        self.ready = False
        self.ports = set()
        self.container_count = 0
        self.containers = {}

    def _getClient(self, port=22):
        client = paramiko.client.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.load_system_host_keys()
        client.connect(self.hypervisor, port=port, username='root')
        return client

    def start(self):
        client = None
        try:
            client = self._getClient()
            _, stdout, _ = client.exec_command('which runc')
            if not stdout.read().startswith("/"):
                self.log.error("%s: runc is not installed" %
                               self.provider.hypervisor)
                return
            _, stdout, _ = client.exec_command(USED_PORTS)
            for port in stdout.readlines():
                self.ports.add(int(port))
            client.exec_command('grep \/mnt /proc/mounts || '
                                'mount -o bind,ro --make-private / /mnt')
            self.ready = True
        except:
            self.log.exception("Can't connect to hypervisor")
        finally:
            if client:
                client.close()

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        if not self.ready:
            return []

        client = self._getClient()
        stdin, stdout, stderr = client.exec_command('runc list -q')
        servers = []
        while True:
            line = stdout.readline()
            if not line:
                break
            servers.append({'name': line})
        client.close()
        self.container_count = len(servers)
        return servers

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupNode(self, server_id):
        if not self.ready:
            return False

        self.container_count -= 1
        if server_id in self.containers:
            self.ports.remove(self.containers[server_id])
            del self.containers[server_id]
        client = self._getClient()
        cmds = [
            'runc kill --all %s KILL' % server_id,
            'runc delete --force %s' % server_id,
            'rm -Rf /var/lib/zuul/work/%s' % server_id,
            'rm -Rf /var/lib/nodepool/oci/%s' % server_id,
        ]
        client.exec_command(";".join(cmds))
        self.log.debug("Running %s" % cmds)
        client.close()
        return True

    def waitForNodeCleanup(self, server_id):
        if not self.ready:
            return False
        return True

    def createContainer(self, pool, hostid):
        if not self.ready:
            return None, None

        if pool.max_servers and self.container_count >= pool.max_servers:
            self.log.warning("Max container reached (%d)" %
                             self.container_count)
            return None, None

        for retry in range(10):
            port = random.randint(22022, 52022)
            if port not in self.ports:
                break
        if retry == 9:
            self.log.error("Couldn't find a free port")
            return None, None
        bundle_path = "/var/lib/nodepool/oci/%s" % hostid
        cmds = [
            "mkdir -p %s" % bundle_path,
            "mkdir -p /var/lib/zuul/work/%s" % hostid,
            "chown zuul:zuul /var/lib/zuul/work/%s" % hostid,
            "cat > %s/config.json" % bundle_path,
        ]
        self.log.debug("Executing %s" % cmds)
        client = self._getClient()
        stdin, _, _ = client.exec_command("; ".join(cmds))
        stdin.write(self.render_config(hostid, port))
        stdin.close()
        client.close()
        client = self._getClient()
        cmd = 'runc run --detach --bundle %s %s' % (bundle_path, hostid)
        self.log.debug("Executing %s" % cmd)
        client.exec_command(cmd)
        client.close()

        # Check container is active
        try:
            key = keyscan(self.hypervisor, port=port, timeout=15)
            client = self._getClient(port=port)
            client.exec_command('echo okay')
            client.close()
        except:
            self.log.exception("Can't connect to container")
            return None, None
        finally:
            client.close()
        self.ports.add(port)
        self.container_count += 1
        self.containers[hostid] = port
        return port, key

    @staticmethod
    def render_config(hostid, port):
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
            "root": {"path": "/mnt", "readonly": True},
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
                {
                    "destination": "/var/lib/zuul/src",
                    "type": "bind",
                    "source": "/var/lib/zuul/work/%s" % hostid,
                    "options": [
                        "bind",
                        "rw",
                        "nodev"
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
            for cap_need in ("CAP_SETUID", "CAP_SETGID", "CAP_IPC_LOCK",
                             "CAP_CHOWN"):
                if cap_need not in cap:
                    cap.append(cap_need)
        config["hostname"] = hostid
        return json.dumps(config)
