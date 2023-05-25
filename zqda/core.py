from werkzeug.exceptions import HTTPException
from flask import json
import os
from zqda import app
import dbm
import pickle
import operator
import re
from flask import render_template, redirect, url_for, abort, request, make_response, escape, flash, jsonify, send_file
from markupsafe import Markup
from pyzotero import zotero, zotero_errors
import json
import json2table
from werkzeug.utils import import_string
from werkzeug.security import generate_password_hash, check_password_hash
import markdown
# https://stackoverflow.com/questions/71804258/flask-app-nameerror-name-markup-is-not-defined
from bs4 import BeautifulSoup
import urllib.parse
import toml


class Z(zotero.Zotero):
    """Local version of pyzotero Zotero class with additional functions"""

    def group(self, **kwargs):
        """Get group data. This is not currently supported in pyzotero."""
        query_string = "/groups/{u}"
        return self._build_query(query_string)


@app.errorhandler(HTTPException)
def handle_exception(e):
    response = e.get_response()
    return render_template('base.html', content=e.description, title=e.name)


@app.route('/login/<library_id>')
def login(library_id):
    if not _check_key(library_id):
        return redirect(url_for('set_key', library_id=library_id, target='library_view'))
    return redirect(url_for('library_view', library_id=library_id))


@app.route('/set_key', methods=['POST', 'GET'])
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

    if request.method == 'POST':
        if not key:
            flash("Please supply a valid password/key.", "danger")
        else:
            r = make_response(redirect(url_for(target, library_id=library_id)))
            r.set_cookie('key', generate_password_hash(key))
            return r

    return render_template(
        'password.html', library_id=library_id, target=target)


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

def _sync_library_data(library_id, api_key):
    """Retrieve the remote metadata for the group library."""
    # this is not wrapped by pyzotero
    # https://api.zotero.org/groups/{library_id}/
    # data['name'], data['description']
    zot = Z(library_id, 'group', api_key)
    try:
        r = zot._retrieve_data(zot.group()).json()
    except zotero_errors.UserNotAuthorised as e:
        print(e)
        return None
    data = r.get('data', None)
    if not data:
        return
    cfg = os.path.join(app.config_path, 'config.toml')
    t = toml.load(cfg)
    t['LIBRARY'][library_id]['title'] = data['name']
    t['LIBRARY'][library_id]['description'] = data.get('description', ' ')
    with open(cfg, 'w') as f:
        toml.dump(t, f)
    app.config.from_mapping(t)
    return

    

# FIXME: Use json instead of pickle, so we can update manually if needed
# e.g., to resync from a specific version
def _sync_items(library_id):
    """Synchronize all items in a single group library. Store item data
    for updated items in the file "items_LIBRARY-ID.db" within the application
    data directory. The latest local version number for each library is stored 
    in the file "versions.pkl" in the application data directory.
    """
    local_ver = 0
    api_key = app.config['LIBRARY'][library_id]['api_key']
    zot = zotero.Zotero(library_id, 'group', api_key)

    remote_ver = zot.last_modified_version()
    library_data = _sync_library_data(library_id, api_key)

    pkl = os.path.join(app.data_path, 'versions.pkl')
    data = {}
    if os.path.exists(pkl):
        with open(pkl, 'rb') as f:
            data = pickle.load(f)
            local_ver = data.get(library_id, 0)
    if not remote_ver > local_ver:
        return "No changes."

    items = zot.everything(zot.items(since=local_ver, include='bib,data'))
    collections = zot.everything(zot.collections(since=local_ver))

    
    for c in collections:
        c['data']['itemType'] = 'collection'
        c['data']['items'] = zot.collection_items(c['data']['key'])
        collection_items = zot.collection_items(c['data']['key'])
        subcollections = zot.collections_sub(c['data']['key'])
        for sub in subcollections:
            sub['data']['itemType'] = 'collection'
        c['data']['items'] = collection_items + subcollections

    items = items + collections #+ library_data

    item_cache = os.path.join(app.data_path, 'items_{}.db'.format(library_id))

    with dbm.open(item_cache, 'c') as db:
        for item in items:
            db[item['key']] = json.dumps(item)
            if item['data']['itemType'] == 'attachment':
                _load_attachment(zot, item)

    data[library_id] = remote_ver
    with open(pkl, 'wb') as f:
        data = pickle.dump(data, f)

    return "Updated {} items.".format(len(items))


def _sync_item(library_id, item_key, item_type='item'):
    """Force (re-)sync of a specific item."""
    api_key = app.config['LIBRARY'][library_id]['api_key']
    zot = zotero.Zotero(library_id, 'group', api_key)
    item_cache = os.path.join(
        app.data_path, 'items_{}.db'.format(library_id))
    data = _get_item(library_id, item_key)
    if data and data.get('itemType', '') == 'collection':
        item_type = 'collection'
    if item_type == 'collection':
        try:
            item = zot.collection(item_key)
            item['data']['itemType'] = 'collection'
            collection_items = zot.collection_items(item['data']['key'])
            subcollections = zot.collections_sub(item['data']['key'])
            for c in subcollections:
                c['data']['itemType'] = 'collection'
            item['data']['items'] = collection_items + subcollections
            
        except zotero_errors.ResourceNotFound:
            abort(404)

    else:
        try:
            item = zot.item(item_key, include='bib,data')
        except zotero_errors.ResourceNotFound:
            abort(404)

    with dbm.open(item_cache, 'c') as db:
        db[item['key']] = json.dumps(item)
    
    if item['data']['itemType'] == 'attachment':
        _load_attachment(zot, item)

    return "Updated!"



# FIXME: cache output
def _get_collections(library_id):
    """Retrieve collections from the stored item metadata for a library.
    Although the Zotero API can return a list of collections, this may be
    faster. 
    """
    collections = {'top':[]}

    item_cache = os.path.join(
        app.data_path, 'items_{}.db'.format(library_id))

    if not os.path.exists(item_cache):
        return collections

    with dbm.open(item_cache, 'r') as db:
        for key in db.keys():
            i = json.loads(db[key])
            item_collections = i['data'].get('collections', []) 
            if i['data'].get('parentCollection', None):
                item_collections.append(i['data']['parentCollection'])
            if len(item_collections) == 0 and not i['data'].get('parentItem', None):
                item_collections.append('top')

            for c in item_collections:
                if not c in collections:
                    collections[c] = list()
                collections[c].append(key)

    return collections


def _load_attachment(zot, item):
    key = item['data']['key']
    if item['data'].get('linkMode') in ('linked_file', 'linked_url'):
        return
    dir = os.path.join(app.data_path, key)
    # Note this will actually be a zip file if it ends with .html
    filename = item['data']['filename']
    filepath = os.path.join(dir, filename)
    if os.path.exists(filepath):
        return
    if not os.path.exists(dir):
        os.makedirs(dir)
    try:
        blob = zot.file(key)
    except:
        return
    with open(filepath, 'wb') as f:
        f.write(blob)

# FIXME: cache output
def _get_tags(library_id):
    """Retrieve tags from the stored item metadata for a library.
    Although the Zotero API can return a list of tags, if there is a large
    number of them in the library it is much faster to open the stored database
    entry for each item and retrieve the tags list from there. 
    """
    tags = {}

    item_cache = os.path.join(
        app.data_path, 'items_{}.db'.format(library_id))

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


# FIXME: cache output
def _get_children(library_id):
    """Update the list of children for each item based on parentItem.
    """
    relations = {}

    item_cache = os.path.join(
        app.data_path, 'items_{}.db'.format(library_id))

    if not os.path.exists(item_cache):
        return relations

    with dbm.open(item_cache, 'r') as db:
        for key in db.keys():
            i = json.loads(db[key])
            parentItem = i['data'].get('parentItem', None)
            if not parentItem:
                continue
            if not parentItem in relations:
                relations[parentItem] = list()
            relations[parentItem].append(i['data']['key'])

    return relations


def _get_items(library_id):
    """Retrieve the item metadata from the database associated with a group
    library."""

    items = []
    item_cache = os.path.join(
        app.data_path, 'items_{}.db'.format(library_id))
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
        app.data_path, 'items_{}.db'.format(library_id))
    if not os.path.exists(item_cache):
        return None
    with dbm.open(item_cache, 'r') as db:
        try:
            i = json.loads(db[item_key])
        except KeyError:
            return None
    return i[data]


def _translate_zotero_uri(uri):
    # http://zotero.org/groups/4711671/items/UJ8WGSFR
    m = re.match('^.*zotero.org/groups/(.*?)/items/(.*)', uri)
    if m:
        library = m.group(1)
        key = m.group(2)
        return '/view/{}/{}'.format(library, key)
    return uri


def _process_citations(txt):
    # <span class="citation" data-citation="{"citationItems":[{"uris":["http://zotero.org/groups/4711671/items/GXPF7VK9"]},{"uris":["http://zotero.org/groups/4711671/items/UJ8WGSFR"]}],"properties":{}}"> <span class="citation-item">...</span>...</span>
    soup = BeautifulSoup(txt, 'html.parser')
    citations = soup.find_all('span', 'citation')
    for c in citations:
        data = urllib.parse.unquote(c.get('data-citation', ''))
        if not 'citationItems' in data:
            # TODO - perform more robust error checking
            continue
        j = json.loads(data)
        uris = [i['uris'][0] for i in j['citationItems']]
        n = 0
        for ci in c.find_all('span', 'citation-item'):
            ci.name = 'a'
            ci['href'] = _translate_zotero_uri(uris[n])
            n = n+1
    return str(soup)


@app.route('/raw/<library_id>/<item_key>')
def blob(library_id, item_key):
    """Download a binary attachment. This may be an item
    attachment or an inline image attached to a note."""
    item = _get_item(library_id, item_key)
    if item['itemType'] != 'attachment':
        abort(404)
    dir = os.path.join(app.data_path, item_key)
    filepath = os.path.join(dir, item['filename'])
    if not os.path.exists(filepath):
        abort(404)
    mimetype = item['contentType']
    if mimetype == 'text/html':
        mimetype = 'application/zip'
    if not app.config['LIBRARY'][library_id].get('allow_downloads', False):
        # always allow images embedded in notes
        if item['linkMode'] != 'embedded_image' and _check_key(library_id) is False:
            abort(401)

    return send_file(filepath, mimetype)


def _dict2table(library_id, data):
    """Convert a dictionary to tabular form."""

    for k, v in data.items():
        if k == 'creators':
            c = []
            for creator in v:  # list of dicts
                if creator.get('name', None):  # single name field
                    c.append('{} ({})'.format(
                        creator['name'], creator.get('creatorType')))
                else:  # has lastName and firstName fields
                    c.append('{}, {} ({})'.format(creator.get('lastName', ''),
                                              creator.get('firstName', ''),
                                              creator.get('creatorType')
                                              ))
            data[k] = c
        elif k == 'tags':  # list of {'tag': tagName} dicts
            data[k] = [
                _a(url_for('tag_list', library_id=library_id, tag_name=t['tag']), t['tag']) for t in v]
        elif k == 'collections':  # list of itemKeys
            c = []
            for i in v:
                collection_data = _get_item(library_id, i)
                if not collection_data:
                    continue
                name = collection_data['name']
                c.append(_a(url_for('html', library_id=library_id, item_key=i), name))
            data[k] = c
        elif k == 'url':
            data[k] = _a(v, v)
        elif k == 'parentItem':
            parent_data = _get_item(library_id, v)
            title = parent_data.get('title', '[untitled]')
            data[k] = _a(v, title)
        elif k == 'childItem':
            c = []
            for child in v:
                child_data = _get_item(library_id, child)
                title = child_data.get('title', child_data.get('name', child_data.get('filename', child_data['itemType'])))
                c.append(_a(child, title))
            data[k] = c

        elif k == 'filename':
            url = url_for('blob', library_id=library_id, item_key=data['key'])
            data[k] = _a(url, v)

    for k in ('relations', 'annotationType', 'annotationColor', 'annotationSortIndex', 'annotationPosition'):
        if k in data.keys():
            del data[k]

    table_attributes = {"style": "width:100%",
          "class": "table table-sm mt-4"}
    j = json2table.convert(data, table_attributes=table_attributes)
    j = j.replace('<ul>', '<ul class="mb-0 ms-0 ps-0" style="list-style-type:none">')
    return j

def _hr():
    return '<hr class="mt-5 border border-primary border-3 opacity-75"">'

def _note(library_id, data):
    content = data['note']
    m = re.search(r'<h1>(.*?)</h1>', data['note'])
    if m:
        data['title'] = m.group(1)
        content = re.sub(r'<h1>(.*?)</h1>', '', content, count=1)
    else:
        data['title'] = 'Note'
    del data['note']  # don't show in the metadata table
    content = re.sub(r'data-attachment-key="(.*?)"',
                     'src="/raw/{}/\g<1>" class="img-fluid"'.format(library_id), content)
    content = _process_citations(content)

    metadata = _dict2table(library_id, data)
    return content + _hr() + metadata, data


@app.route('/view/<library_id>')
def library_view(library_id):
    """View an html representation of the library."""

    description = app.config['LIBRARY'][library_id]['description']
    title = app.config['LIBRARY'][library_id]['title']
    collections = _get_collections(library_id)
    items = collections['top']
    links = []

    icon = '<i class="bi bi-arrow-return-left h2"></i>'
    links.append(
        '<!-- _up --><tr><td>{}</td><td>{}</td></tr>'.format(icon, _a(url_for('index'), 'Top')))

    for item in items:
        links.append(_link(library_id, item))

    content = '<p>{}</p>{}<table class="table">{}</table>'.format(
        description, _hr(), ''.join(sorted(links)))

    return render_template('base.html',
                           content=Markup(content),
                           title=title,
                           library_id=library_id,
                           )    

@app.route('/view/<library_id>/<item_key>')
def html(library_id, item_key):
    """View an html representation of a library item. For most items this
    will be a table showing item metadata; for a note the full content will
    be shown with the metadata listed below; and for a collection a list
    of items, sub-collections, and parent collection will be presented in 
    directory index format."""

    data = _get_item(library_id, item_key)
    if not data:
        abort(404)
    if data.get('note', None):
        content, data = _note(library_id, data)
        title = data.get('title', '[untitled]')
    elif data['itemType'] == 'collection':
        content, title = _collection(library_id, item_key, data)
    else:
        title = data.get('title', '[untitled]')
        children = _get_children(library_id)
        data['childItem'] = children.get(item_key, [])
        content = _dict2table(library_id, data)

    return render_template('base.html',
                           content=Markup(content),
                           title=title,
                           library_id=library_id
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
                           title='Library synchronization',
                           )


@app.route('/sync/<library_id>/<item_key>')
def sync_item(library_id, item_key):
    """Synchronize a single item."""
    item_type = request.args.get('item_type', 'item')
    r = _sync_item(library_id, item_key, item_type=item_type)
    return redirect(url_for('html', library_id=library_id, item_key=item_key))

@app.route('/')
def index():
    """Home page of the application."""

    # out = [markdown.markdown(app.config['DESCRIPTION'])]
    libraries = app.config['LIBRARY'].items()
    links = []
    icon = '<i class="bi bi-folder h2"></i>'

    for library, data in libraries:
        url = url_for('library_view', library_id=library)
        links.append('<tr><td><div>{}</div></td><td>{}<p class="mt-3">{}</p></td></tr>'.format(
            icon, _a(url, data['title']), data['description']))
            
    content = '<table class="table">' + ''.join(sorted(links)) + '</table>'
    
    return render_template('base.html',
                           content=Markup(content),
                           title='Home'
                           )


def _a(link, title):
    return '<a class="text-break" href="{}">{}</a>'.format(link, title)

def _link(library_id, item_key):
        item_data = _get_item(library_id, item_key)
        title = item_data.get('title', item_data.get('name', item_data.get('filename', item_data.get('itemType', 'Untitled'))))
        link = url_for('html', library_id=library_id, item_key=item_key)
        icon = '<i class="bi bi-file-earmark h2"></i>'
        if item_data.get('itemType', '') == 'collection':
            icon = '<i class="bi bi-folder h2"></i>'
        if item_data.get('itemType', '') == 'note':
            icon = '<i class="bi bi-journal-text h2"></i>'
        if item_data.get('itemType', '') == 'annotation':
            parentItem = _get_item(library_id, item_data['parentItem'])
            title = ': '.join([title, parentItem.get('title')])
            icon = '<i class="bi bi-pencil-square h2"></i>'
                                   
        description = item_data.get('abstractNote', item_data.get('annotationText', item_data.get('note', '')))
        description = BeautifulSoup(description, "html.parser").text

        return '<!--{} {} --><tr><td><div>{}</div></td><td>{}<p class="mt-3">{}</p></td></tr>'.format(
            item_data.get('itemType', 'document'), title, 
            icon, _a(link, title), description)


def _collection(library_id, collection_id, collection_data):
    data = _get_collections(library_id)
    items = data[collection_id]
    links = list()

    collection_title = collection_data['name']

    if collection_data.get('parentCollection', None):
        link = url_for('html', library_id=library_id,
                       item_key=collection_data['parentCollection'])
        parent_data = _get_item(
            library_id, collection_data['parentCollection'])
        icon = '<i class="bi bi-arrow-return-left h2"></i>'
        title = parent_data['name']
        links.append(
            '<!-- _up --><tr><td>{}</td><td>{}</td></tr>'.format(icon, _a(link, title)))

    for item in items:
        links.append(_link(library_id, item))

    content = '<table class="table">' + ''.join(sorted(links)) + '</table>'
    return content, collection_title

@app.route('/tag/<library_id>/<tag_name>')
def tag_list(library_id, tag_name):
    """View a list of resources in the library associated with `tag_name`.
    """
    all_tags = _get_tags(library_id)
    items = all_tags[tag_name]
    links = []
    for item_key in items:
        
        links.append(_link(library_id, item_key))
    
    content = '<table class="table">' + \
            ''.join(sorted(links)) + '</table>'

    return render_template('base.html', 
                           content=Markup(content), 
                           title=tag_name,
                           library_id=library_id)

@app.route('/help', methods=['GET'])
def help():
    """Print all defined routes for the application and their endpoint 
    docstrings.
    """

    out = []
    out.append("""<p>This site provides public access to digital resources managed in <a href="https://zotero.org">Zotero</a> libraries, along with experimental tools for managing those resources for qualitative data analysis purposes. The available access routes are listed below.</p>
    """)
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

    out.append('<table class="table">')
    out.append('<tr><th>Rule</th><th>Methods</th><th>Description</th></tr>')

    for rule, methods, docs in zip(rules, rule_methods, rule_docs):
        if rule.rule.startswith('/static/'):
            continue
        rulename = escape(rule.rule)
        # if '<' in rule.rule:
        #     rulename = escape(rule.rule)
        # else:
        #     rulename = '<a href="{}">{}</a>'.format(
        #         url_for(rule.endpoint), rule.rule)
        out.append(
            '<tr><td>{}</td><td>{}</td><td>{}</td></tr>'.format(rulename, methods, docs or ''))
    out.append('</table>')

    content = ' '.join(out)
    return render_template('base.html', content=Markup(content), title='Help')
