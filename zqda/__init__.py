import os
import toml
from flask import Flask
from flask_breadcrumbs import Breadcrumbs

instance_path = os.path.expanduser('~/.zqda')
app = Flask(__name__, instance_relative_config=True,
            instance_path=instance_path)

app.config.from_mapping(
    SECRET_KEY='dev',
    TITLE="Zotero QDA tools",
    ADDRESS="Culture and Development Research Centre",
    LICENSE="Content available under a Creative Commons Attribution-ShareAlike 4.0 License, unless otherwise indicated.",
    DESCRIPTION="This is a set of tools for Zotero QDA analysis.",
    RECAPTCHA_SITE_KEY=None,
    RECAPTCHA_SECRET_KEY=None,
    LIBRARY = []
    )

Breadcrumbs(app=app)

try:
    os.makedirs(app.instance_path)
except OSError:
    pass

cfg = os.path.join(app.instance_path, 'config.toml')
# app.config.from_file() available in flask > 2.0
t = toml.load(cfg)
app.config.from_mapping(t)


# These imports must come at the bottom of the file
import zqda.core
import zqda.annotation_viewer
import zqda.tag_grouper
import zqda.tag_renamer
