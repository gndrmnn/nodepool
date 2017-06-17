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
import subprocess
import random
import os

from nodepool.driver import Provider
from nodepool.nodeutils import keyscan


class OpenContainerProvider(Provider):
    log = logging.getLogger("nodepool.driver.oci.OpenContainerProvider")

    def __init__(self, provider):
        self.provider = provider
        self.hypervisor = socket.gethostbyname(provider.hypervisor)
        self.ready = False
        self.ports = set()
        self.container_count = 0
        self.containers = {}
        self.use_rootfs = False
        for pool in self.provider.pools.values():
            if [True for label in pool.labels.values() if label['path'] == '/']:
                self.use_rootfs = True
                break
        self.prepare_ansible_environment()

    def prepare_ansible_environment(self):
        self.playbook_path = "%s/playbooks" % os.path.dirname(__file__)
        self.data_path = os.path.expanduser("~/oci")
        self.inventory = os.path.join(self.data_path,
                                      "%s.inventory" % self.hypervisor)
        self.info_path = os.path.join(self.data_path,
                                      "%s.json" % self.hypervisor)
        if not os.path.isdir(self.data_path):
            os.makedirs(self.data_path, 0o700)
        with open(self.inventory, "w") as inv:
            inv.write("[hypervisor]\n%s ansible_user=root " % self.hypervisor +
                      "ansible_python_interpreter=/usr/bin/python\n"
                      "[localhost]\nlocalhost ansible_connection=local "
                      "ansible_python_interpreter=/usr/bin/python\n")

    def run_ansible(self, playbook, extra_vars=[]):
        # TODO: replace this by a hypervisor_host_key provider setting
        os.environ["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        argv = ["ansible-playbook",
                "%s/%s.yml" % (self.playbook_path, playbook),
                "-i", self.inventory,
                "-e", "use_rootfs=%s" % str(self.use_rootfs),
                "-e", "hypervisor_info_file=%s" % self.info_path]
        for extra_var in extra_vars:
            argv.extend(["-e", extra_var])
        self.log.debug("Running '%s'" % " ".join(argv))
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        if proc.wait():
            self.log.error("Failed to run '%s' stdout: %s, stderr: %s" % (
                " ".join(argv), proc.stdout.read(), proc.stderr.read()))
            return False
        return True

    def start(self):
        self.listNodes()

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        if not self.run_ansible("init"):
            return []
        inf = json.load(open(self.info_path))
        servers = []
        for server_name in inf["containers"]:
            servers.append({'name': server_name})
        self.container_count = len(servers)
        self.ports = set(map(int, inf["ports"]))
        return servers

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        pass

    def cleanupNode(self, server_id):
        self.container_count -= 1
        if server_id in self.containers:
            self.ports.remove(self.containers[server_id])
            del self.containers[server_id]
        return self.run_ansible("delete", ["container_id=%s" % server_id])

    def waitForNodeCleanup(self, server_id):
        return True

    def createContainer(self, pool, hostid, rootfs, username, workdir):
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

        spec_path = os.path.join(self.data_path, "%s.config" % hostid)
        with open(spec_path, "w") as spec_file:
            spec_file.write(self.render_config(hostid, port, rootfs, workdir))
        created = self.run_ansible("create", [
            "container_id=%s" % hostid, "container_spec=%s" % spec_path,
            "worker_username=%s" % username,
            "host_addr=%s" % self.hypervisor, "container_port=%s" % port])
        os.unlink(spec_path)
        if not created:
            return None, None

        try:
            key = keyscan(self.hypervisor, port=port, timeout=15)
        except:
            self.log.exception("Can't scan container key")
            return None, None
        self.ports.add(port)
        self.container_count += 1
        self.containers[hostid] = port
        return port, key

    @staticmethod
    def render_config(hostid, port, rootfs, workdir):
        if rootfs == '/':
            rootfs = '/var/lib/nodepool/oci/host-rootfs'
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
            "root": {"path": rootfs, "readonly": True},
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
                # Bind mount user's .ssh
                {
                    "destination": "/root/.ssh",
                    "type": "bind",
                    "source": "/root/.ssh",
                    "options": ["bind", "ro"]
                },
                {
                    "destination": "%s" % workdir,
                    "type": "bind",
                    "source": "/var/lib/nodepool/oci/%s/" % hostid,
                    "options": ["bind", "rw", "nodev"],
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
            "-o", "UsePrivilegeSeparation=no",
            "-o", "UsePAM=no",
            "-o", "UseDNS=no",
        ]
        for cap in config["process"]["capabilities"].values():
            for cap_need in ("CAP_SETUID", "CAP_SETGID", "CAP_IPC_LOCK",
                             "CAP_CHOWN"):
                if cap_need not in cap:
                    cap.append(cap_need)
        config["hostname"] = hostid
        return json.dumps(config)
