from setuptools import setup, find_packages

setup(
    name="syntextai",
    version="0.1.0",
    packages=find_packages(where="api"),
    package_dir={"": "api"},
    include_package_data=True,
    install_requires=[
        # Dependencies will be installed from requirements.txt
    ],
    python_requires=">=3.10",
)
