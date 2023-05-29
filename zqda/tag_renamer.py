from flask import render_template, url_for, request, redirect
from markupsafe import Markup
from pyzotero import zotero


from zqda import app
import zqda.core


def _rename(library_id, src_tag, target_tag):
    """Update all items in the Zotero library that have been tagged with 
    `src_tag`, replacing the value of `src_tag` with the value of `target_tag`.
    """
    
    zot = zotero.Zotero(library_id, 'group', app.config['LIBRARY'][library_id]['api_key'])
    items = zot.everything(zot.items(tag=src_tag))
    if len(items) == 0:
        return
    for item in items:
        item['data']['tags'] = [{'tag': tag['tag'].replace(
            src_tag, target_tag)} for tag in item['data']['tags']]
        zot.update_item(item['data'])
    return


@app.route('/rename_tags/<library_id>/', methods=['GET', 'POST'])
def tag_rename_form(library_id):
    """Present a form allowing for the bulk renaming of tags in a Zotero
    group library. Each tag is presented in an editable field; submitting the
    form will update the names of all modified tags.
    """
    if zqda.core._check_key(library_id) is False:
        return redirect(url_for('set_key', library_id=library_id, 
                        target='tag_rename_form'))
    out = []
    args = request.values
    if request.method == 'POST':
        for arg in args:
            if arg == args[arg]:
                continue
            if arg in ('library_id'):
                continue
            _rename(library_id, arg, args[arg])
        # resync from server to make sure our local database is up-to-date
        zqda.core._sync_items(library_id)

    tags = zqda.core._get_tags(library_id).keys()

    out.append('<form action="{}" id="zform" method="post">'.format(
        url_for('tag_rename_form', library_id=library_id)))
    out.append('<div class="form-group mb-4">')

    for tag in sorted(tags):
        out.append(
            '<p><input type="text" value="{tag}" class="form-control" name="{tag}"></p>'.format(tag=tag))
    out.append(
        '<input type="hidden" name="library_id" value="{}">'.format(library_id))
    out.append(
        '<input type="submit" value="submit" class="btn btn-primary mb-2" />')
    out.append('</div>')
    out.append('</form>')
    out.append('<script>$("#zform").dirty({preventLeaving: true})</script>')

    return render_template('base.html',
                           content=Markup(' '.join(out)),
                           title='Rename tags'
                           )
