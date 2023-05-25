# zqda

This is a set of experimental tools supporting the use of [Zotero](https://zotero.org) group libraries for Qualitative Data Analysis (QDA). The tools focus on facilitating the use of Zotero tags for data coding, specifically the management of tags applied to PDF annotations.

The tools are provided as an extensible set of utilities that can be accessed through a web server running Python 3.5 or higher, along with a core set of functions that retrieve updated item metadata from the Zotero API and store it locally on disk. It is possible to run this application on a public server or as a local Flask application.

Access to a specific set of Zotero group libraries must be configured manually. Tools providing read-only access to metadata from these libraries are accessible to anyone with server access, while tools that support library editing require a login password or key, and optionally the successfull completion of a ReCaptcha test as well. 

## Rationale

These tools are intended to provide functionality that is not available in the desktop Zotero client or through the web application. In a QDA workflow we would like to produce collaborative highlights and tagged (coded) annotations within PDF documents in the library, then refine and analyze the tags in ways that are either not possible, or clumsily implemented, in the Zotero client. 

The current set of tools helps us to:

  - __Generate reports extracting all annotations and their corresponding highlighted passages, from all documents across the library, that match a given tag (code).__ The Zotero client can list attachments containing annotations that match queried tags, but the attachments must be opened individually, and the matching passages located manually.

  - __Rename and merge overlapping tags efficiently.__ The Zotero client provides a small tag viewer panel with an inline list that can be difficult to scan. There is no bulk renaming mechanism.
  
  - __Group annotations matching a set of related tags into thematic categories.__ The saved search functionality of Zotero accommodates queries, but searches are not fully implemented for annotation tags.


## Installation and configuration

Install using pip:

`$ pip install -e git+https://github.com/mcdrc/zqda.git`

A configuration file `config.toml` should be created manually in the directory ".zqda" in the user home directory. 

Set the global variable `SECRET_KEY` to something secret.

To use ReCaptcha, set the variables `RECAPTCHA_SITE_KEY` and `RECAPTCHA_SECRET_KEY`.

For each Zotero group library, create a section in the configuration file corresponding to that library, using the mapping prefix `LIBRARY` followed by the Zotero ID for the library (this will bea seven-digit number). Also set an `api_key` with write access to that library, and a list of passwords allowing write access to the library.

```toml

[LIBRARY.0000000]
title = "Library title"
description = "Library description"
api_key = "22AB7C1D64D57EACDC12E367" # Use a real API key from zotero.org
keys = ['key1', 'key2']
# images embedded in notes are always allowed
allow_downloads = false

```

## Use

To run locally, use the Flask built-in web server from the install directory:

`$ flask --app zqda run`

To run on a public web server, [configure the server to run zqda as a wsgi app](https://flask.palletsprojects.com/en/2.2.x/deploying/) or place the included `zqda.cgi` file somewhere executable.

Navigate to the `/help` URL to see a list of available tools and routes.
