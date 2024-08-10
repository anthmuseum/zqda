import os
import json
from flask import render_template, url_for, request, redirect, Markup
from pyzotero import zotero

from zqda import app
import zqda.core


HELP = """
    <p>Apply thematic tags to annotations in a Zotero group library.</p>
    <p>Existing tags associated with PDF annotations in the selected Zotero
    library will be listed. If a tag is only present in annotations that
    already have an associated thematic tag, it will be hidden from the list.</p>
    <p>Enter the name of the thematic tag to apply, then use the checkboxes
    next to tag names corresponding to annotations that should also be tagged
    with the thematic tag.</p>
    <p>The thematic tag will be converted to UPPERCASE and prefixed with the
    <code>@</code> symbol.
"""


def _apply_category_tag(library_id, tags_group, target):
    """Apply a tag to all library items matching any of the tags in the list
    `tags_group`, then synchronize changes to the local database."""

    zot = zotero.Zotero(library_id, 'group',
                        app.config['LIBRARY'][library_id]['api_key'])
    prefix = app.config['LIBRARY'][library_id].get('cluster_tag_prefix', '@')
    target = prefix + target.lstrip(prefix).upper()

    for tag in tags_group:
        items = zot.everything(zot.items(tag=tag))
        for item in items:
            # skip if the item already has this tag; otherwise update
            if not any(t['tag'] == target for t in item['data']['tags']):
                zot.add_tags(item, target)
    zqda.core._sync_items(library_id)


def _get_filtered_tags(library_id, purge=False, remove=None):
    """Retrieve a list of tags that have not yet been applied to annotations
    that also have a thematic (cluster) tag associated with them."""

    tags = []
    tagfile = 'group_tags_{}.json'.format(library_id)
    jsn = os.path.join(app.data_path, tagfile)
    prefix = app.config['LIBRARY'][library_id].get('group_tag_prefix', '@')

    if os.path.exists(jsn) and not purge:
        with open(jsn, 'r') as f:
            try:
                tags = json.load(f)
            except:
                purge = True

        if remove and isinstance(remove, list) and not purge:
            for tag in remove:
                try:
                    tags.remove(tag)
                except ValueError:
                    continue
            with open(jsn, 'w') as f:
                json.dump(tags, f)
            return tags

    # Filter to ONLY annotations
    # getting this from Zotero server works but seems to have a hard limit of 900 for keys
    items = zqda.core._get_items(library_id)

    for item in items:
        if not 'annotation' in item['data']['itemType']:
            continue

        if not any(t['tag'].startswith(prefix) for t in item['data']['tags']):
            new = [e['tag']
                   for e in item['data']['tags'] if not e['tag'] in tags]
            tags.extend(new)

    with open(jsn, 'w', encoding='utf-8') as f:
        json.dump(tags, f, ensure_ascii=False)
    return tags


@app.route('/cluster_tags/<library_id>', methods=['GET', 'POST'])
def tag_grouper_form(library_id, purge=False, remove=None):
    """Present or process a web form allowing the user to cluster tags in
    a Zotero group library. A list of tags is presented, which includes only
    tags that meet all the following critera: (1) the tag is applied to an
    annotation, (2) the tag is not associated only with annotations that
    already have cluster tags applied to them, and (3) the tag is not itself
    a cluster tag. Thematically related tags can be selected using checkboxes.
    The name of a higher-level "cluster" tag can be entered at the top of the
    form; this tag will be converted to uppercase and prefixed with the `@`
    symbol or other character defined in the application settings under
    `LIBRARY.xxxxxxx.group_tag_prefix`. """

    if zqda.core._check_key(library_id) is False:
        return redirect(url_for('set_key', library_id=library_id, target='tag_grouper_form'))
    out = []
    args = request.values
    purge = args.get('purge', False)

    if request.method == 'POST':
        tags_group = [arg for arg in args if args[arg] == 'on']
        target = args.get('target')
        _apply_category_tag(library_id, tags_group, target)
        remove = tags_group
    else:
        remove = None
    tags = _get_filtered_tags(library_id, remove=remove, purge=purge)

    out.append('<form action="{}" method="post">'.format(
        url_for('tag_grouper_form', library_id=library_id)))
    out.append('<div class="form-group mb-4">')
    out.append(
        '<p><input type="text" placeholder="Thematic tag name to apply" name="target" required="required" class="form-control" ></p>')
    out.append(
        '<input type="submit" value="submit" class="btn btn-primary mb-2" />')
    for tag in sorted(tags):
        out.append('<div class="form-check">')
        out.append(
            '<input type="checkbox" name="{tag}" class="form-check-input" >'.format(tag=tag))
        out.append(
            ' <label for="{tag}" class="form-check-label">{tag}</label>'.format(tag=tag))
        out.append('</div>')
    out.append(
        '<input type="hidden" name="library_id" value="{}">'.format(library_id))
    out.append('</div>')
    out.append('</form>')

    return render_template('base.html',
                           content=Markup(' '.join(out)),
                           help=Markup(HELP),
                           title='Cluster tags'
                           )
