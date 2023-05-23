import os
import toml
from flask import Flask, abort
from flask_breadcrumbs import Breadcrumbs

app = Flask(__name__)

# XDG_CONFIG_HOME is ~/.config
app.config_path = os.path.expanduser('~/.config/zqda')

# XDG_DATA_HOME is ~/.local/share
app.data_path = os.path.expanduser('~/.local/share/zqda')

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
    os.makedirs(app.config_path)
    os.makedirs(app.data_path)
except OSError:
    pass

cfg = os.path.join(app.config_path, 'config.toml')
# app.config.from_file() available in flask > 2.0
try:
    t = toml.load(cfg)
except FileNotFoundError:
    abort(500, 'No configuration available')
app.config.from_mapping(t)


# These imports must come at the bottom of the file
import zqda.core
import zqda.annotation_viewer
import zqda.tag_grouper
import zqda.tag_renamer
