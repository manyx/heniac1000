
import logging
import os
import cgi
from google.appengine.ext import blobstore
from google.appengine.ext.blobstore import *
from google.appengine.ext.webapp import blobstore_handlers
from google.appengine.ext import db
from google.appengine.ext.db import *
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext.webapp import template
from google.appengine.api import *
from google.appengine.api.images import *
from django.utils import simplejson as json
from api import *
import datetime
import urllib

class MainPage(webapp.RequestHandler):
    def get(self):
        user = users.get_current_user()
        username = user.nickname()
        logoutUrl = users.create_logout_url("/")
        
        self.response.headers['Content-Type'] = 'text/html'
        path = os.path.join(os.path.dirname(__file__), 'main.html')
        self.response.out.write(template.render(path, {
            'username' : json.dumps(username),
            'logoutUrl' : json.dumps(logoutUrl)}))

class Admin(webapp.RequestHandler):
    def get(self):
        u = urlparse.urlparse(self.request.url)
        dashboard = ""
        if u.netloc.startswith("localhost"):
            dashboard = "/_ah/admin"
        else:
            appname = u.netloc[:u.netloc.find(".")]
            dashboard = "https://appengine.google.com/dashboard?&app_id=s~" + appname
            
        self.response.headers['Content-Type'] = 'text/html'
        path = os.path.join(os.path.dirname(__file__), 'admin.html')
        self.response.out.write(template.render(path, {
            "dashboard" : dashboard}))

application = webapp.WSGIApplication([
    ('/', MainPage),
    ('/admin', Admin),
], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

