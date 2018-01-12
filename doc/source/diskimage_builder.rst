.. _diskimage-builder:

Diskimage-builder
=================

Diskimage-builder is a tool that the TripleO project built to build
disk images quickly in a reliable and reproducible manner. Build
actions are organized into several build steps and logically collected
in "elements".

More info can be found at the diskimage-builder documentation:
https://docs.openstack.org/diskimage-builder/latest/

Why diskimage-builder?
----------------------

There are a number of reasons for why we have gone with diskimage-builder
as the supported tool for building cloud images with Nodepool.

* Does not require emulation or virtualization. We run Nodepool in the
  clouds that Nodepool boots instances in. Nested virtualization is not
  always available or reliable and emulation is quite slow. Avoiding
  dependency on these features is quite beneficial in this case.
* Caching of build contents. Diskimage-builder aggressively caches
  image components speeding up builds and reducing network IO
  requirements.
* Cross platform build tool. You can build Ubuntu images on CentOS and
  CentOS images on Ubuntu. We support many distros and not needin multiple
  builders helps simplify things.
* Support for many output formats. Nodepool consumes resources in many
  clouds that support different image formats. Diskimage-builder will
  output images in a variety of formats like raw, qcow2, and vhd from a
  single source image build. You can even produce docker images.
* Images are not dependent on upstream image choices. While
  diskimage-builder can build images atop existing published images you
  also have the choice of building images from a "minimal" base. This
  is beneficial because it allows you to avoid cloud or distro specific
  choices around tools like cloud-init. Image builds are also far more
  predictable and don't change randomly when an upstream makes changes.
* Finally, diskimage-builder is a flexible abstraction layer for
  describing what should go in an image. The details of how you write
  those bits are flexible enough to allow using the appropriate tool for
  the job.

Why were snapshots removed?
---------------------------

Previous versions of Nodepool supported building images via snapshotting
of instances in the Nodepool cloud providers. There were a number of
reasons for removing this functionality, and we get asked often enough
for them that we should just have them here.

* Snapshot builds are based on preexisting images in the cloud. These
  images may all share a common name like "Ubuntu Xenial", but have
  vastly different contents. Nova-agent vs cloud init. Base kernel vs
  the hardware enablement kernel. Different cloud init configs resulting
  in different behaviors. This is just a small number of the different
  base image problems we had.
* Snapshot builds are tightly coupled to the cloud they were built for.
  You can't necessarily boot an image built in one cloud in another
  when snapshots are used.
* Due to the previous issues snapshot builds are much harder to test
  locally. You need cloud accounts in each of the clouds you are
  attempting to properly test the images for.
