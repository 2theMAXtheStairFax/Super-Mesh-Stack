"""Drive web.py in-process against a scratch app directory."""
import io
import os
import sys
import tempfile
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
APP_DIR = tempfile.mkdtemp()
os.environ["SMS_DIR"] = APP_DIR

import web  # noqa: E402


def call(path, form=None):
    """GET, or POST when `form` is a dict. Returns (status, headers dict, body text)."""
    out = {}
    body = urlencode(form or {}).encode()
    env = {"REQUEST_METHOD": "POST" if form is not None else "GET", "PATH_INFO": path,
           "SERVER_NAME": "t", "SERVER_PORT": "80", "wsgi.input": io.BytesIO(body),
           "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/x-www-form-urlencoded",
           "wsgi.errors": sys.stderr, "wsgi.url_scheme": "http", "wsgi.version": (1, 0),
           "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": True}

    def start(status, headers, exc=None):
        out.update(status=status, headers=dict(headers))

    text = b"".join(web.app(env, start)).decode()
    return out["status"], out["headers"], text
