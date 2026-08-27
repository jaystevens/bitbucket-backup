#!/usr/bin/env python
from setuptools import find_packages, setup

INSTALL_REQUIRES = ["requests"]

SCRIPTS = ["bitbucket_backup.py", "bitbucket-backup"]

setup(
    name="bitbucket-backup",
    version="0.0.2",
    pyhton_requires=">3.6",
    # author="Sam Kuehn",
    # author_email="samkuehn@gmail.com",
    # url="https://github.com/samkuehn/bitbucket-backup",
    description="Python script to backup Bitbucket repos",
    long_description=__doc__,
    scripts=SCRIPTS,
    zip_safe=False,
    install_requires=INSTALL_REQUIRES,
    include_package_data=True,
    classifiers=[
        "Intended Audience :: System Administrators",
        "Operating System :: OS Independent",
        "Topic :: System :: Systems Administrationt",
        "Programming Language :: Python",
    ],
)
