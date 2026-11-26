from setuptools import find_packages, setup

setup(
    packages=find_packages(include=("finbotagents*", "cli*")) + ["finbotagents_assets"],
    package_dir={"finbotagents_assets": "assets"},
)
