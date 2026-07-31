from setuptools import setup, find_packages

setup(
    name="hostpanel-storage",
    version="1.0.8",
    packages=find_packages(),
    install_requires=["fastapi", "pydantic"],
    entry_points={
        "hostpanel.modules":          ["storage = hostpanel_storage.plugin"],
        "hostpanel.setup":            ["hostpanel-storage = hostpanel_storage.lifecycle:on_install"],
        "hostpanel.update":           ["hostpanel-storage = hostpanel_storage.lifecycle:on_update"],
        "hostpanel.lifecycle":        ["hostpanel-storage = hostpanel_storage.lifecycle:pre_uninstall"],
        "hostpanel.hooks.on_startup": ["hostpanel-storage = hostpanel_storage.lifecycle:on_startup"],
        "hostpanel.hooks.user_delete":["hostpanel-storage = hostpanel_storage.lifecycle:on_user_delete"],
    },
)
