# Copyright 2018 Red Hat
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
import os
import time

from pyVmomi import vim
from pyVim.connect import SmartConnect

from nodepool.driver import Provider


class VmwareProvider(Provider):
    log = logging.getLogger("nodepool.driver.vmware.VmwareProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.zuul_public_key = provider.zuul_public_key
        self.client = None

    def start(self):
        self.log.debug("Starting")
        if self.client is None:
            # Use VMWARE_HOST, VMWARE_USERNAME, VMWARE_PASSWORD
            self.client = SmartConnect(
                host=os.environ["VMWARE_HOST"],
                user=os.environ["VMWARE_USER"],
                pwd=os.environ["VMWARE_PASSWORD"],
                port=int(os.environ.get("VMWARE_PORT", 443)))

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        # TODO
        return []

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

    def cleanupNode(self, server_id):
        if self.client is None:
            return False

    def waitForNodeCleanup(self, server_id):
        instance = self.client.content.searchIndex.FindByUuid(
            None, server_id, True, False)
        poweroff = instance.PowerOffVM_Task()
        while poweroff.info.state == vim.TaskInfo.State.running:
            time.sleep(1)
        destroy = instance.Destroy_Task()
        while destroy.info.state == vim.TaskInfo.State.running:
            time.sleep(1)

    def _get_obj(self, vimtype, name):
        obj = None
        content = self.client.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vimtype], True)
        for c in container.view:
            if name:
                if c.name == name:
                    obj = c
                    break
            else:
                obj = c
                break
        return obj

    def createInstance(self, hostname, label, nodepool_id):
        # Copied from https://github.com/sijis/pyvmomi-examples/
        template_vm = self._get_obj(vim.VirtualMachine, label.template)
        vmconf = vim.vm.ConfigSpec(
            numCPUs=label.num_cpu, memoryMB=label.memory_mb)
        adaptermap = vim.vm.customization.AdapterMapping()
        adaptermap.adapter = vim.vm.customization.IPSettings(
            ip=vim.vm.customization.DhcpIpGenerator(),
            dnsDomain='nodepool.local')
        globalip = vim.vm.customization.GlobalIPSettings()
        ident = vim.vm.customization.LinuxPrep(
            domain='nodepool.local',
            hostName=vim.vm.customization.FixedName(name=hostname))
        customspec = vim.vm.customization.Specification(
            nicSettingMap=[adaptermap],
            globalIPSettings=globalip,
            identity=ident)
        resource_pool = self._get_obj(
            vim.ResourcePool, self.provider.resource_pool)
        relocateSpec = vim.vm.RelocateSpec(pool=resource_pool)
        cloneSpec = vim.vm.CloneSpec(
            powerOn=True,
            template=False,
            location=relocateSpec,
            customization=customspec,
            config=vmconf)
        clone = template_vm.Clone(
            name=hostname,
            folder=template_vm.parent,
            spec=cloneSpec)
        while clone.info.state == vim.TaskInfo.State.running:
            time.sleep(1)
        return clone.info.result
