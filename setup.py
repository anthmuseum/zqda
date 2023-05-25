from setuptools import setup

setup(
    name='zqda',
    packages=['zqda'],
    include_package_data=True,
    install_requires=[
        'flask',
        'toml',
        'pyzotero',
        'json2table',
        'markdown',
    ],
)

# https://github.com/mardix/flask-recaptcha
