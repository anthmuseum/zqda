import urllib.parse
import toml
import os
import dbm
import operator
import re
import json

from flask import render_template, redirect, url_for, abort, request, make_response, escape, flash, json, send_file
from markupsafe import Markup
from pyzotero import zotero, zotero_errors
import json2table
from werkzeug.utils import import_string
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from bs4 import BeautifulSoup
from flask_caching import Cache

from zqda import app

cache = Cache(app)

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
    """Provide access credentials (passkey) for a library. Logged-in users
    will have access to (1) tools that modify the library content, and (2) 
    binary attachment files if public downloads are disabled for that
    library."""
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

    
# e.g., to resync from a specific version
def _sync_items(library_id):
    """Synchronize all items in a single group library. Store item data
    for updated items in the file "items_LIBRARY-ID.db" within the application
    data directory. The latest local version number for each library is stored 
    in the file "versions.json" in the application data directory.
    """
    local_ver = 0
    api_key = app.config['LIBRARY'][library_id]['api_key']
    zot = zotero.Zotero(library_id, 'group', api_key)

    remote_ver = zot.last_modified_version()
    _sync_library_data(library_id, api_key)

    jsn = os.path.join(app.data_path, 'versions.json')
    data = {}
    if os.path.exists(jsn):
        with open(jsn, 'r') as f:
            data = json.load(f)
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
    with open(jsn, 'w') as f:
        json.dump(data, f, ensure_ascii=False)

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


@cache.memoize()
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
            if len(item_collections) == 0 and not i['data'].get('parentItem', None) and i['data']['itemType'] == 'collection':
                item_collections.append('top')

            for c in item_collections:
                if not c in collections:
                    collections[c] = list()
                collections[c].append(key)

    return collections


def _load_attachment(zot, item):
    if item['data'].get('linkMode') in ('linked_file', 'linked_url'):
        return
    key = item['data']['key']
    dir = os.path.join(app.data_path, key)
    # This will actually be a zip file if it ends with .html
    filename = item['data']['filename']
    mimetype = item['data']['contentType']
    if mimetype == 'text/html':
        mimetype = 'application/zip'
        filename = key + '.zip'
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


@cache.memoize()
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
            # ignore unfiled items
            if len(i['data'].get('collections', [])) == 0 and not i['data'].get('parentItem', None):
                continue
            item_tags = i['data'].get('tags', None)
            if not item_tags:
                continue
            for tag in item_tags:
                tag = tag['tag']
                if not tag in tags:
                    tags[tag] = list()
                tags[tag].append(i['data']['key'])

    return tags


@cache.memoize()
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
        library_id = m.group(1)
        item_key = m.group(2)
        return url_for('html', library_id=library_id, item_key=item_key)
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

    filename = item['filename']
    mimetype = item['contentType']
    if mimetype == 'text/html':
        mimetype = 'application/zip'
        filename = item_key + '.zip'

    dir = os.path.join(app.data_path, item_key)
    filepath = os.path.join(dir, filename)
    if not os.path.exists(filepath):
        abort(404)

    if not app.config['LIBRARY'][library_id].get('allow_downloads', False):
        # always allow images embedded in notes
        if item['linkMode'] != 'embedded_image' and _check_key(library_id) is False:
            abort(401)

    return send_file(filepath)


def _dict2table(library_id, data):
    """Convert a dictionary to tabular form."""

    data = {k:v for k,v in data.items() if v != '' and v != []}
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

    table_class = "table mt-4"
    if data['itemType'] in ('note', 'attachment'):
        table_class = "table mt-4 table-sm"
    table_attributes = {"style": "width:100%", "class": table_class}


    j = json2table.convert(data, table_attributes=table_attributes)
    j = j.replace('<ul>', '<ul class="mb-0 ms-0 ps-0" style="list-style-type:none">')
    return j

def _hr():
    return '<hr class="my-5 border border-primary border-3 opacity-75"">'


def _embed_pdf(library_id, item_key, data):
    content = '<div class="ratio ratio-4x3"><object data="{pdf}" type="application/pdf"><p><a href="{pdf}">Download PDF</a></p></object></div>'.format(
        pdf=url_for('blob', library_id=library_id, item_key=item_key)
    )
    children = _get_children(library_id)
    data['childItem'] = children.get(item_key, [])
    metadata = _dict2table(library_id, data)
    return content + _hr() + metadata


def _embed_img(library_id, item_key, data):
    content = '<img src="{}" class="img-fluid">'.format(
        url_for('blob', library_id=library_id, item_key=item_key)
    )
    metadata = _dict2table(library_id, data)
    return content + _hr() + metadata


def _embed_video(library_id, item_key, data):
    content = '<div class="ratio ratio-16x9"><video src="{}" class="object-fit-contain" controls></video></div>'.format(
        url_for('blob', library_id=library_id, item_key=item_key)
    )
    metadata = _dict2table(library_id, data)
    return content + _hr() + metadata


def _embed_audio(library_id, item_key, data):
    content = '<audio controls><source src="{}"></audio>'.format(
        url_for('blob', library_id=library_id, item_key=item_key)
    )
    metadata = _dict2table(library_id, data)
    return content + _hr() + metadata


def _embed_note(library_id, data):
    content = data['note']
    m = re.search(r'<h1>(.*?)</h1>', data['note'])
    if m: 
        title = BeautifulSoup(m.group(1), "html.parser").text
        content = re.sub(r'<h[1-3]>(.*?)</h[1-3]>', '', content, count=1)
    else:
        title = 'Note'
    del data['note']  # don't show in the metadata table

    content = re.sub(r'data-attachment-key="(.*?)"',
                     'src="{}\g<1>" class="img-fluid"'.format(
                        url_for('blob', library_id=library_id, item_key='')), content)
    content = _process_citations(content)

    metadata = _dict2table(library_id, data)
    return content + _hr() + metadata, title


@app.route('/view/<library_id>')
def library_view(library_id):
    """View an html representation of the library. The list includes top-level
    collections but NOT top-level items, as the latter may include items
    that have been trashed or deleted. (Such items are still accessible in
    other views.)"""

    description = app.config['LIBRARY'][library_id]['description']
    title = app.config['LIBRARY'][library_id]['title']
    collections = _get_collections(library_id)
    items = collections['top']
    links = []

    # icon = '<i class="bi bi-arrow-return-left h2 text-primary"></i>'
    # links.append(
    #     '<!-- _up --><tr><td>{}</td><td>{}</td></tr>'.format(icon, _a(url_for('index'), 'Top')))

    for item in items:
        links.append(_link(library_id, item))

    content = '<p>{}</p>{}<table class="table">{}</table>'.format(
        description, _hr(), ''.join(sorted(links)))

    return render_template('base.html',
                           content=Markup(content),
                           title=title,
                           library_id=library_id,
                           )    


def _download_authorized(library_id, data):
    if app.config['LIBRARY'][library_id].get('allow_downloads', False):
        return True
    if data.get('linkMode', '') == 'embedded_image':
        return True
    if _check_key(library_id) is True:
        return True
    return False

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
    title = data.get('title', '[untitled]')
    if data.get('note', None):
        content, title = _embed_note(library_id, data)
    elif data['itemType'] == 'collection':
        content, title = _collection(library_id, item_key, data)
    
    # embeds
    elif data.get('contentType', '') == 'application/pdf' and _download_authorized(library_id, data):
        content = _embed_pdf(library_id, item_key, data)      
    elif data.get('contentType', '').startswith('image') and _download_authorized(library_id, data):
        content = _embed_img(library_id, item_key, data)
    elif data.get('contentType', '').startswith('video') and _download_authorized(library_id, data):
        content = _embed_video(library_id, item_key, data)
    elif data.get('contentType', '').startswith('audio') and _download_authorized(library_id, data):
        content = _embed_audio(library_id, item_key, data)

    else:
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
    cache.clear()
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
    """Home page of the application. Depending on how the application 
    is configured, this will either show a browsable list of projects or
    redirect to the application help page."""
    if not app.config.get('EXPORT', True):
        return redirect(url_for('help'))

    libraries = app.config['LIBRARY'].items()
    links = []
    icon = '<i class="bi bi-folder h2 text-primary"></i>'

    for library, data in libraries:
        url = url_for('library_view', library_id=library)
        links.append('<tr><td style="width:2em"><div>{}</div></td><td>{}<p class="mt-3">{}</p></td></tr>'.format(
            icon, _a(url, data['title']), data['description']))
            
    content = (markdown.markdown(app.config.get('DESCRIPTION', ' '))) + 
                '<table class="table">' + 
                ''.join(sorted(links)) +
                '</table>')

    return render_template('base.html',
                           content=Markup(content),
                           title='Home'
                           )


def _a(link, title):
    return '<a class="text-break" href="{}">{}</a>'.format(link, title)

def _link(library_id, item_key):
        item_data = _get_item(library_id, item_key)
        if not item_data:
            return ''
        title = item_data.get('title', item_data.get('name', item_data.get('filename', item_data.get('itemType', 'Untitled'))))
        link = url_for('html', library_id=library_id, item_key=item_key)
        icon = '<i class="bi bi-file-earmark h2 text-primary"></i>'
        if item_data.get('itemType', '') == 'collection':
            icon = '<i class="bi bi-folder h2 text-primary"></i>'
        if item_data.get('itemType', '') == 'note':
            icon = '<i class="bi bi-journal-text h2 text-primary"></i>'
        if item_data.get('itemType', '') == 'annotation':
            parentItem = _get_item(library_id, item_data['parentItem'])
            title = ': '.join([title, parentItem.get('title')])
            icon = '<i class="bi bi-pencil-square h2 text-primary"></i>'
                                   
        description = item_data.get('abstractNote', item_data.get('annotationText', item_data.get('note', '')))
        description = BeautifulSoup(description, "html.parser").text
        description_trunc = ' '.join(description.split(" ")[:200])
        if description_trunc != description:
            description = description_trunc + '...'

        # Add the itemType and title in a comment for sorting
        return '<!-- {} {} --><tr><td style="width:2em"><div>{}</div></td><td>{}<p class="mt-3">{}</p></td></tr>'.format(
            item_data.get('itemType', 'document'), 
            title.replace('-', ' '), # avoid misformed comment tags
            icon, _a(link, title), description)


def _collection(library_id, collection_id, collection_data):
    data = _get_collections(library_id)
    items = data[collection_id]
    links = list()

    collection_title = collection_data['name']

    icon = '<i class="bi bi-arrow-return-left h2 text-primary"></i>'
    if collection_data.get('parentCollection', None):
        link = url_for('html', library_id=library_id,
                       item_key=collection_data['parentCollection'])
        parent_data = _get_item(
            library_id, collection_data['parentCollection'])

        title = parent_data['name']
    else:
        link = url_for('library_view', library_id=library_id)
        title = app.config['LIBRARY'][library_id]['title']

    links.append('<!-- _up --><tr><td style="width:2em">{}</td><td>{}</td></tr>'.format(
        icon, _a(link, title)))

    for item in items:
        links.append(_link(library_id, item))

    content = '<table class="table">' + ''.join(sorted(links)) + '</table>'
    return content, collection_title


@app.route('/tags/<library_id>')
def show_tags(library_id):
    """Show a list of tags in the selected group library."""
    title = 'Tags: {}'.format(app.config['LIBRARY'][library_id]['title'])
    links = []
    tags = _get_tags(library_id)
    icon = '<i class="bi bi-tag h2 text-primary"></i>'
    for tag in sorted(tags):
        link = url_for('tag_list', library_id=library_id,
                       tag_name=tag)
        links.append(
            '<tr><td style="width:2em"><div>{}</div></td><td>{}</td></tr>'.format(
                icon, _a(link, tag))
        )

    content = '<table class="table">' + \
        ''.join(sorted(links)) + '</table>'
    
    return render_template('base.html',
                           content=Markup(content),
                           title=title,
                           library_id=library_id,
                           )


@app.route('/tags/<library_id>/<tag_name>')
def tag_list(library_id, tag_name):
    """View a list of resources in the library associated with `tag_name`.
    """
    all_tags = _get_tags(library_id)
    items = all_tags.get(tag_name, None)
    if not items:
        abort(404)
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
