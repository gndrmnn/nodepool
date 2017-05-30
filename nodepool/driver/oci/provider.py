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
import paramiko
import paramiko.client
import random

from nodepool.driver import ProviderManager


class OpenContainerProviderManager(ProviderManager):
    log = logging.getLogger("nodepool.driver.oci.provider."
                            "OpenContainerProviderManager")

    def __init__(self, provider):
        self.provider = provider

    def _getClient(self):
        client = paramiko.client.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.load_system_host_keys()
        client.connect(self.provider.hypervisor, username='root')
        return client

    def start(self):
        self.log.debug("Starting...")

    def stop(self):
        self.log.debug("Stopping...")

    def listNodes(self):
        client = self._getClient()
        stdin, stdout, stderr = client.exec_command('runc list -q')
        servers = []
        while True:
            line = stdout.readline()
            if not line:
                break
            servers.append({'name': line})
        client.close()
        return servers

    def cleanupNode(self, server_id):
        client = self._getClient()
        cmds = [
            'runc kill %s KILL' % server_id,
            'umount /var/lib/nodepool/oci/%s/rootfs' % server_id,
        ]
        client.exec_command(';'.join(cmds))
        self.log.warning("Running %s" % ';'.join(cmds))
        client.close()
        return True

    def waitForNodeDeletion(self, server_id):
        return True

    def createContainer(self, hostid):
        # TODO: better port selection...
        port = random.randint(22022, 52022)
        bundle_path = "/var/lib/nodepool/oci/%s" % hostid
        cmds = [
            "mkdir -p %s/rootfs" % bundle_path,
            "cat > %s/config.json" % bundle_path,
            "mount -o bind,ro --make-private / %s/rootfs" % bundle_path,
        ]
        self.log.debug("Executing %s" % cmds)
        client = self._getClient()
        stdin, _, _ = client.exec_command("; ".join(cmds))
        stdin.write(self.render_config(hostid, port))
        stdin.close()
        client.close()
        client = self.manager.getClient()
        cmd = 'runc run --bundle %s %s' % (bundle_path, hostid)
        self.log.info("Executing %s" % cmd)
        client.exec_command(cmd)
        # TODO: manage client instance
        return port

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
