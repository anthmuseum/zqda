import os
from zqda import app
import dbm
import pickle
import operator
from flask import render_template, redirect, url_for, abort, request, make_response, escape, flash, jsonify
from markupsafe import Markup
from pyzotero import zotero
import json
import json2table
from werkzeug.utils import import_string
from werkzeug.security import generate_password_hash, check_password_hash
from flask_breadcrumbs import register_breadcrumb
import markdown
import flask_recaptcha
recaptcha = flask_recaptcha.ReCaptcha(app)


from flask import json
from werkzeug.exceptions import HTTPException

@app.errorhandler(HTTPException)
def handle_exception(e):
    response = e.get_response()
    return render_template('base.html', content=e.description, title=e.name)


@app.route('/set_key', methods = ['POST', 'GET'])
def set_key():
    """Set a password for operations on a library. This endpoint should not be
    accessed directly, but called as needed by a password-protected page
    along with a redirect target as the URL parameter."""
    args = request.values
    library_id = args.get('library_id', None)
    target = args.get('target', None)
    if library_id is None or target is None:
        abort(400, 'Incomplete request')
    key = args.get('key', None)

    verified = recaptcha.verify()
    if request.method == 'POST':
        if not key:
            flash("Please supply a valid password/key.", "danger")
        elif not verified:
            flash("Please complete the captcha.", "danger")
        else:
            r = make_response(redirect(url_for(target, library_id=library_id)))
            r.set_cookie('key', generate_password_hash(key))
            return r

    return render_template(
        'password.html', library_id=library_id, target=target)
    return r

def _check_key(library_id):
    """Check the user cookies for a valid access key."""

    key = request.cookies.get('key')
    if not key:
        return False
    valid_keys = app.config['LIBRARY'][library_id].get('keys', [])
    if len(valid_keys) == 0:
        return False
    for k in valid_keys:
        if check_password_hash(key, k):
            return True
    return False


def _sync_items(library_id):
    """Synchronize all items in a single group library. Store item data
    for updated items in the file "items_LIBRARY-ID.db" within the application
    data directory. The latest local version number for each library is stored 
    in the file "versions.pkl" in the application data directory.
    """
    local_ver = 0
    zot = zotero.Zotero(library_id, 'group')
    remote_ver = zot.last_modified_version()

    pkl = os.path.join(app.instance_path, 'versions.pkl')
    data = {}
    if os.path.exists(pkl):
        with open(pkl, 'rb') as f:
            data = pickle.load(f)
            local_ver = data.get(library_id, 0)
    if not remote_ver > local_ver:
        return "No changes."

    items = zot.everything(zot.items(since=local_ver, include='bib,data'))
    item_cache = os.path.join(
        app.instance_path, 'items_{}.db'.format(library_id))

    with dbm.open(item_cache, 'c') as db:
        for item in items:
            db[item['key']] = json.dumps(item)

    data[library_id] = remote_ver
    with open(pkl, 'wb') as f:
        data = pickle.dump(data, f)

    return "Updated {} items.".format(len(items))


def _sync_item(library_id, item_key):
    """Force (re-)sync of a specific item."""
    zot = zotero.Zotero(library_id, 'group')
    item_cache = os.path.join(
        app.instance_path, 'items_{}.db'.format(library_id))

    item = zot.item(item_key, include='bib,data')

    with dbm.open(item_cache, 'c') as db:
        db[item['key']] = json.dumps(item)

    return "Updated!"


@app.route('/tags/<library_id>')
def show_tags(library_id):
    """List all the tags in a library."""
    data = _get_tags(library_id)
    table_attributes = {"style": "width:100%", "class": "table"}
    return render_template('base.html',
                           content=Markup(json2table.convert(
                               data, table_attributes=table_attributes)),
                           title='Item {}'.format(data.get('title', ''))
                           )

def _get_tags(library_id):
    """Retrieve tags from the stored item metadata for a library.
    Although the Zotero API can return a list of tags, if there is a large
    number of them in the library it is much faster to open the stored database
    entry for each item and retrieve the tags list from there. 
    """
    tags = {}

    item_cache = os.path.join(
        app.instance_path, 'items_{}.db'.format(library_id))

    if not os.path.exists(item_cache):
        return tags
        
    with dbm.open(item_cache, 'r') as db:
        for key in db.keys():
            i = json.loads(db[key])
            item_tags = i['data'].get('tags', None)
            if not item_tags:
                continue
            for tag in item_tags:
                tag = tag['tag']
                if not tag in tags:
                    tags[tag] = list()
                tags[tag].append(i['data']['key'])

    return tags


def _get_items(library_id):
    """Retrieve the item metadata from the database associated with a group
    library."""
    
    items = []
    item_cache = os.path.join(
        app.instance_path, 'items_{}.db'.format(library_id))
    if not os.path.exists(item_cache):
        return items

    with dbm.open(item_cache, 'r') as db:
        for key in db.keys():
            i = json.loads(db[key])
            items.append(i)

    return items


def _get_item(library_id, item_key, data='data'):
    """Retrieve the metadata for a single item from the database associated 
    with a group library."""
    item_cache = os.path.join(
        app.instance_path, 'items_{}.db'.format(library_id))
    if not os.path.exists(item_cache):
        return None
    with dbm.open(item_cache, 'r') as db:
        try:
            i = json.loads(db[item_key])
        except KeyError:
            return None
    return i[data]


@app.route('/item/<library_id>/<item_key>')
def show_item(library_id, item_key):
    """Show a table presenting the metadata for a single item."""
    data = _get_item(library_id, item_key)
    if not data:
        data = {"key": item_key, "description": "Item not in database"}
    table_attributes = {"style": "width:100%", "class": "table"}
    return render_template('base.html',
                           content=Markup(json2table.convert(
                               data, table_attributes=table_attributes)),
                           title='Item {}'.format(data.get('title', ''))
                           )

@app.route('/sync')
def sync():
    """Synchronize data with the zotero.org server. Retrieves the metadata
    for any items that have been created or updated since the last sync."""
    out = []
    libraries = app.config['LIBRARY']
    for library_id in libraries:
        out.append('Synchronizing {}...'.format(library_id))
        r = _sync_items(library_id)
        out.append(r)
    return render_template('base.html',
                           content=Markup('<br>'.join(out)),
                           title='Library synchronization'
                           )

@app.route('/sync/<library_id>/<item_key>')
def sync_item(library_id, item_key):
    """Synchronize a single item."""
    r = _sync_item(library_id, item_key)
    return redirect(url_for('show_item', library_id=library_id, item_key=item_key))

@register_breadcrumb(app, '.', 'home')
@app.route('/')
def index():
    """Home page of the application."""
    content = markdown.markdown(app.config['DESCRIPTION'])
    return render_template('base.html', content=Markup(content))

@app.route('/help', methods=['GET'])
def help():
    """Print all defined routes for the application and their endpoint 
    docstrings.
    """

    rules = list(app.url_map.iter_rules())
    rules = sorted(rules, key=operator.attrgetter('rule'))
    rule_methods = [
        ", ".join(sorted(rule.methods - set(("HEAD", "OPTIONS"))))
        for rule in rules
    ]

    rule_docs = []
    for rule in rules:
    
        if hasattr(app.view_functions[rule.endpoint], 'import_name'):
            o = import_string(app.view_functions[rule.endpoint]).import_name
            rule_docs.append(o.__doc__)
        else:
            rule_docs.append(app.view_functions[rule.endpoint].__doc__) 
    out = []
    out.append('<table class="table">')
    out.append('<tr><th>Rule</th><th>Methods</th><th>Description</th></tr>')

    for rule, methods, docs in zip(rules, rule_methods, rule_docs):
        if rule.rule.startswith('/static/'):
            continue
        if '<' in rule.rule:
            rulename = escape(rule.rule)
        else:
            rulename = '<a href="{}">{}</a>'.format(url_for(rule.endpoint), rule.rule)
        out.append(
            '<tr><td>{}</td><td>{}</td><td>{}</td></tr>'.format(rulename, methods, docs or ''))
    out.append('</table>')

    content = ' '.join(out)
    return render_template('base.html', content=Markup(content))
