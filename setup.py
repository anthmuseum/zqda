from setuptools import setup

setup(
    name='zqda',
    packages=['zqda'],
    include_package_data=True,
    install_requires=[
        'flask',
        'toml',
        'pyzotero',
        'flask-breadcrumbs',
        'json2table',
        'markdown',
        'flask_recaptcha'
    ],
)

# https://github.com/mardix/flask-recaptcha
