.. _diskimage-builder:

Diskimage-builder
=================

Diskimage-builder is a tool built to build disk images quickly in a
reliable and reproducible manner. Build actions are organized into
several build steps and logically collected in "elements".

More info can be found at the diskimage-builder documentation:
https://docs.openstack.org/diskimage-builder/latest/

Why diskimage-builder?
----------------------

There are a number of reasons for why Nodepool has chosen diskimage-builder
as the supported tool for building cloud images with Nodepool.

* Does not require emulation or virtualization.
  Nodepool is built to provision pools of instances many of which come
  from cloud providers. Nested virtualization is not always available or
  reliable on cloud prividers and emulation is quite slow. Avoiding
  dependency on these features is quite beneficial in this case.
* Caching of build contents. Diskimage-builder aggressively caches
  image components speeding up builds and reducing network IO
  requirements.
* Cross platform build tool. You can build Ubuntu images on CentOS and
  CentOS images on Ubuntu. Nodepool aims to support many distros and not
  needing different builders for each platform helps simplify things.
* Support for many output formats. Nodepool supports clouds that use
  different image formats. Diskimage-builder will output images in a
  variety of formats like raw, qcow2, and vhd from a single source image
  build.
* Images are not dependent on upstream image choices. While
  diskimage-builder can build images atop existing published images you
  also have the choice of building images from a "minimal" base. This
  is beneficial because it makes it easier to build images on many
  distros that are similar to each other. Image builds are also far more
  predictable and don't change randomly when an upstream makes changes.
* Finally, diskimage-builder is a flexible abstraction layer for
  describing what should go in an image. The details of how you write
  those bits are flexible enough to allow using the appropriate tool for
  the job.
