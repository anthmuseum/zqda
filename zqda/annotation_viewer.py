from zqda import app
from flask import render_template, url_for
from markupsafe import Markup
from pyzotero import zotero
import zqda.core



def _get_parent_title(library_id, parentItem):
    """Retrieve the bibliographic citation for the parent item of a PDF
    attachment containing an annotation."""
    parent_item = zqda.core._get_item(library_id, parentItem) # the PDF attachment
    if not parent_item:
        return 'No title'
    grandparent_item = zqda.core._get_item(library_id,
        parent_item['parentItem'], data='bib')  # main item
    title = grandparent_item
    return title


@app.route('/annotations/<library_id>')
def show_annotations_tag_select(library_id):
    """Show a list of tags associated with annotations in the selected
    group library."""
    title = 'Annotation viewer'
    help = 'Please select a tag.'
    out = []
    tags = zqda.core._get_tags(library_id)
    out.append('<ul>')

    for tag in sorted(tags):
        # escaped = urllib.parse.quote(tag)
        link = url_for('show_annotations', library_id=library_id,
                       tag=tag)
        out.append('<li><a href="{}">{}</a></li>'.format(link, tag))
    out.append('</ul>')

    return render_template('base.html',
                           content=Markup(' '.join(out)),
                           help=help,
                           title=title
                           )

@app.route('/annotations/<library_id>/<tag>')
def show_annotations(library_id, tag):
    """Show the annotations associated with a single tag in a Zotero group
    library. Each annotation is presented as applicable with the highlighted 
    text passage from the PDF, editor comments, and a list of tags applied
    to the annotation."""
    out = []
    zot = zotero.Zotero(library_id, 'group')
    items = zot.items(tag=tag,
                        itemType='annotation',
                        format='keys')
    items = items.decode('utf-8').splitlines()

    # We still get an empty value.
    # if len(items) == 0:
    #     return render_template('base.html', content="No results!")
    
    out.append('<ol>')

    for item_key in items:
        if item_key == '':
            continue
        i = zqda.core._get_item(library_id, item_key)
        if not i:
            continue
        zotero_link = 'zotero://open-pdf/groups/{}/items/{}?page={}&annotation={}'.format(
            library_id,
            i['parentItem'],
            i['annotationPageLabel'],
            i['key']
        )
        title = _get_parent_title(library_id, i['parentItem'])
        zotero_link = '<a href="{z}">{title}</a>'.format(
            z=zotero_link, title=title)
        out.append('<li>')
        out.append('<p class="b">{}</p>'.format(zotero_link))
        out.append('<p>{}</p>'.format(i.get('annotationText', 'No text')))
        out.append('<p><em>{}</em></p>'.format(i.get('annotationComment', '')))
        item_tags = ['<a class="btn btn-primary btn-sm" href="{}">{}</a>'.format(url_for(
            'show_annotations', library_id=library_id, tag=v['tag']), v['tag']) for v in i['tags']]
        out.append(
            '<p>{}</p>'.format(' '.join(item_tags)))
        out.append('</li>')
    out.append('</ol>')
    return render_template('base.html',
                           content=Markup(' '.join(out)),
                           title='Annotations - {}'.format(tag)
                           )


