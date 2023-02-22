#!/usr/bin/env python

# For some shared hosts:
#!/usr/bin/scl enable rh-python35 -- python3

from wsgiref.handlers import CGIHandler
from zqda import app

CGIHandler().run(app)