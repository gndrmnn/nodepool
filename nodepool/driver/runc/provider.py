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
import subprocess
import os
import sys
import time

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.nodeutils import nodescan
from nodepool.driver.runc.handler import RuncNodeRequestHandler


class RuncProvider(Provider):
    log = logging.getLogger("nodepool.driver.runc.RuncProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        self.pools = provider.pools
        self.containers = {}
        self._zk = None
        self._prepare_ansible_environment()
        for pool in self.pools.values():
            pool.use_rootfs = False
            pool.hostname = pool.name
            pool.public_ipv4 = None
            pool.public_ipv6 = None
            pool.inventory = os.path.join(
                self.data_path, "%s.inventory" % pool.name)
            pool.info_path = os.path.join(
                self.data_path, "%s.json" % pool.name)
            with open(pool.inventory, "w") as inv:
                inv.write(
                    "[hypervisor]\n" + pool.hostname +
                    " ansible_user=root"
                    " ansible_python_interpreter=/usr/bin/python\n"
                    "[localhost]\nlocalhost"
                    " ansible_connection=local"
                    " ansible_python_interpreter=%s\n" % sys.executable)

            if [True for label in pool.labels.values()
                if label.path == '/']:
                pool.use_rootfs = True
                break

    def _prepare_ansible_environment(self):
        self.playbook_path = "%s/playbooks" % os.path.dirname(__file__)
        self.data_path = os.path.expanduser("~/runc")
        self.ansible_cfg = os.path.join(self.data_path, "ansible.cfg")
        self.known_host = os.path.join(self.data_path, "known_hosts")
        if not os.path.isdir(self.data_path):
            os.makedirs(self.data_path, 0o700)
        with open(self.ansible_cfg, "w") as cfg:
            cfg.write("[defaults]\n"
                      "retry_files_enabled = False\n"
                      "gathering = explicit\n"
                      "internal_poll_interval = 0.01\n"
                      "library = %s/library\n" % self.playbook_path)
            cfg.write("[ssh_connection]\n"
                      "pipelining = True\n"
                      "ssh_args = -o UserKnownHostsFile=%s\n" %
                      self.known_host)

    def run_ansible(self, playbook, pool, extra_vars=[]):
        os.environ["ANSIBLE_CONFIG"] = self.ansible_cfg
        argv = ["ansible-playbook",
                "%s/%s.yml" % (self.playbook_path, playbook),
                "-i", pool.inventory,
                "-e", "use_rootfs=%s" % str(pool.use_rootfs),
                "-e", "zuul_console_dir=%s" % self.provider.zuul_console_dir,
                "-e", "hypervisor_info_file=%s" % pool.info_path]
        for extra_var in extra_vars:
            argv.extend(["-e", extra_var])
        cmd_debug = "env ANSIBLE_CONFIG=%s %s" % (
            self.ansible_cfg, " ".join(argv))
        self.log.debug("Running %s", cmd_debug)
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        if proc.wait():
            self.log.error("Failed to run '%s' stdout: %s, stderr: %s",
                           cmd_debug, proc.stdout.read(), proc.stderr.read())
            return False
        return True

    def start(self, zk_conn):
        self.log.debug("Starting")
        if os.path.isfile(self.known_host):
            os.unlink(self.known_host)
        for pool in self.pools.values():
            if not pool.hostkeys:
                pool.hostkeys = nodescan(pool.hostname)
            with open(self.known_host, "a") as knownhost:
                for key in pool.hostkeys:
                    knownhost.write("%s %s\n" % (pool.hostname, key))
            if not self.run_ansible("init", pool):
                return
            inf = json.load(open(pool.info_path))
            self.containers[pool.hostname] = set(inf["containers"])
            pool.public_ipv4 = inf["ipv4"] if len(inf["ipv4"]) else None
            pool.public_ipv6 = inf["ipv6"] if len(inf["ipv6"]) else None
        self._zk = zk_conn
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, name, hypervisor, provider):
                self.id = name
                self.name = name
                self.public_v4 = hypervisor
                self.metadata = {
                    'nodepool_provider_name': provider.name,
                    'nodepool_node_id': self.id.split('-', 1)[0],
                }

            def get(self, name, default=None):
                return getattr(self, name, default)
        for pool in self.pools.values():
            for server_name in self.containers[pool.hostname]:
                servers.append(FakeServer(
                    server_name, pool.hostname, self.provider))
        return servers

    def labelReady(self, name):
        # Labels are always ready
        return True

    def join(self):
        # RUNC Provider doesn't have sub thread
        pass

    def cleanupLeakedResources(self):
        for pool in self.pools.values():
            self.run_ansible("clean", pool)

    def cleanupNode(self, server_id, pool=None):
        if not self.ready:
            return False
        server_pool = None
        if pool:
            server_pool = pool
        else:
            for pool in self.pools.values():
                if server_id in self.containers[pool.hostname]:
                    server_pool = pool
                    break
        if not server_pool:
            self.log.info("No pool match container %s" % server_id)
            return True
        if not self.run_ansible(
                "delete", server_pool, ["container_id=%s" % server_id]):
            raise exceptions.ServerDeleteException(
                "server %s deletion failed" % server_id)
        self.containers[server_pool.hostname].remove(server_id)

    def waitForNodeCleanup(self, server_id):
        # Open Container cleanup is synchronous
        pass

    def createContainer(self, pool, hostid, port, label):
        if not self.ready:
            self.log.warning("Creating container when provider isn't ready")
            for retry in range(60):
                if self.ready:
                    break
                time.sleep(1)
            if retry == 59:
                raise RuntimeError(
                    "Manager %s failed to initialized" % self.provider.name)

        spec_path = os.path.join(self.data_path, "%s.config" % hostid)
        with open(spec_path, "w") as spec_file:
            spec_file.write(self.render_config(
                hostid, port, label, self.provider.zuul_console_dir))
        created = self.run_ansible("create", pool, [
            "container_id=%s" % hostid, "container_spec=%s" % spec_path,
            "worker_username=%s" % label.username,
            "worker_homedir=%s" % label.homedir,
            "host_addr=%s" % pool.hostname, "container_port=%s" % port])
        os.unlink(spec_path)
        if not created:
            raise exceptions.LaunchNodepoolException(
                "%s creation failed" % hostid)

        try:
            key = nodescan(pool.hostname, port=port, timeout=15)
        except Exception:
            try:
                self.cleanupNode(hostid, pool)
            except Exception:
                self.log.error("%s cleanup failed" % hostid)
            raise exceptions.LaunchKeyscanException(
                "Can't scan container %s key" % hostid)
        self.containers[pool.hostname].add(hostid)
        return key

    @staticmethod
    def render_config(hostid, port, label, zuul_console_dir):
        if label.path == '/':
            rootfs = '/srv/host-rootfs'
        else:
            rootfs = label.path
        config = {
            "ociVersion": "1.0.0",
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
                    "destination": "/var/tmp",
                    "type": "tmpfs",
                    "source": "shm",
                    "options": [
                        "nosuid",
                        "nodev",
                        "mode=1777",
                        "size=2G"
                    ]
                },
                {
                    "destination": "/var/run",
                    "type": "tmpfs",
                    "source": "shm",
                    "options": [
                        "nosuid",
                        "noexec",
                        "nodev",
                        "mode=0755",
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
                    "destination": "%s" % label.homedir,
                    "type": "bind",
                    "source": "/var/lib/nodepool/runc/%s/" % hostid,
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
        if zuul_console_dir != "/tmp":
            # If zuul_console isn't in tmp, then mount a tmpfs first
            config["mounts"].append({
                "destination": "/tmp",
                "type": "tmpfs",
                "source": "shm",
                "options": [
                    "nosuid",
                    "nodev",
                    "mode=1777",
                    "size=2G"
                ]
            })
        # Then bind mount host zuul_console directory in the container
        config["mounts"].append({
            "destination": zuul_console_dir,
            "type": "bind",
            "source": zuul_console_dir,
            "options": ["bind", "rw"],
        })

        # Simple sshd container
        config["process"]["args"] = [
            "/sbin/sshd", "-p", str(port), "-e", "-D",
            "-o", "UsePrivilegeSeparation=no",
            "-o", "UsePAM=no",
            "-o", "UseDNS=no",
            "-o", "PidFile=none",
        ]
        for cap in config["process"]["capabilities"].values():
            for cap_need in ("CAP_SETUID", "CAP_SETGID", "CAP_IPC_LOCK",
                             "CAP_CHOWN", "CAP_SYS_CHROOT"):
                if cap_need not in cap:
                    cap.append(cap_need)
        config["hostname"] = hostid
        return json.dumps(config)

    def getRequestHandler(self, poolworker, request):
        return RuncNodeRequestHandler(poolworker, request)
