#!/usr/bin/env python

#
#  setup.py
#  
#  Copyright The GRS Demodulator Contributors.
#  
#  This file is part of GRS Demodulator.
#
#  GRS Demodulator is free software; you can redistribute it
#  and/or modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of the
#  License, or (at your option) any later version.
#  
#  GRS Demodulator is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#  
#  You should have received a copy of the GNU General Public
#  License along with GRS Demodulator; if not, see <http://www.gnu.org/licenses/>.
#  
#

import setuptools

#from sphinx.setup_command import BuildDoc

# The posix-only guard that used to live here is gone. Nothing in this package
# is posix-specific -- it is numpy, scipy and pyzmq, all of which ship Windows
# wheels. The guard blocked `pip install -e .` on the machines where the
# ground station is actually developed, which meant the station's end-to-end
# test could not import the demodulator and had to be run inside a container.
#
# It was also broken on its own terms: it called sys.exit() without importing
# sys, so instead of the friendly message it raised NameError.

exec(open('grs_demodulator/version.py').read())

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name                            = "grs_demodulator",
    version                         = __version__,
    author                          = "Gabriel Mariano Marcelino",
    author_email                    = "gabriel.mm8@gmail.com",
    maintainer                      = "Gabriel Mariano Marcelino",
    maintainer_email                = "gabriel.mm8@gmail.com",
    url                             = "https://github.com/spacelab-ufsc/grs-demodulator",
    license                         = "GPLv3",
    description                     = "GRS Demodulator",
    long_description                = long_description,
    long_description_content_type   = "text/markdown",
    platforms                       = ["Linux"],
    classifiers                     = [
        "Development Status :: 1 - Planning",
        "Environment :: Console",
        "Intended Audience :: Education",
        "Intended Audience :: End Users/Desktop",
        "Intended Audience :: Telecommunications Industry",
        "Intended Audience :: Science/Research"
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Natural Language :: English",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Topic :: Communications :: Ham Radio",
        "Topic :: Education",
        "Topic :: Scientific/Engineering",
        ],
    download_url                    = "https://github.com/spacelab-ufsc/grs-demodulator/releases",
    packages                        = setuptools.find_packages(),
    install_requires                = ['numpy','scipy','zmq'],
    entry_points                    = {
        'console_scripts': [
            'grs-demodulator = grs_demodulator.__main__:main'
            ]
        },
    data_files                      = [
        ('share/spacelab_decoder/', ['spacelab_decoder/data/satellites/catarina-a2.json']),
        ],
    #cmdclass                        = {'build_sphinx': BuildDoc},
)
