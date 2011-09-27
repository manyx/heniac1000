
import logging
import os
from urlparse import urlparse

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
from google.appengine.api.channel import *
from google.appengine.api import images
from google.appengine.api import taskqueue

from django.utils import simplejson as json
from myutil import *

###############################################
# boiler plate

now = 0

api_list = {}
def registerAPI(name):
    def temp(func):
        if name.startswith("_"):
            api_list["/_api/" + name] = func
        else:
            api_list["/api/" + name] = func
        return func
    return temp

def verifyUser(me):
    if users.is_current_user_admin():
        username = me
    else:
        username = users.get_current_user().nickname()
        if me != username:
            raise BaseException("must be admin to set user")
    return getUser(username)

class Meta(webapp.RequestHandler):
    def get(self):
        self.go()
    def post(self):
        self.go()
    def go(self):
        global now
        now = mytime()
        func = api_list[self.request.path]
        args = {}
        for key in self.request.arguments():
            args[str(key)] = self.request.get(key)
        if "me" in args:
            args["user"] = verifyUser(args["me"])
            del args["me"]
        jso = func(**args)
        ret = json.dumps(jso if jso != None else "ok", indent=4)
        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write(ret)

application = webapp.WSGIApplication([
    ('/api/.*', Meta),
    ('/_api/.*', Meta),
], debug = True)

def main():
    run_wsgi_app(application)
    
@registerAPI("_eval")
def func(code):
    ret = []
    def output(a):
        ret.append(a)
    exec(code) in globals(), locals()
    return ret

###############################################
# Session

class Session(db.Model):
    title = db.StringProperty()
    createTime = db.IntegerProperty()

class Message(db.Model):
    createTime = db.IntegerProperty()
    user = db.ReferenceProperty(collection_name="a")
    session = db.ReferenceProperty(collection_name="b")
    text = db.TextProperty()

def getDefaultSession():
    return Session.get_or_insert("defaultSession",
        createTime = mytime(),
        title = "lobby")

def getSessionTouchKeyName(u, s):
    return sha1(str(u.key()) + "," + str(s.key()))

def enterSession(u, s):
    keyName = getSessionTouchKeyName(u, s)
    def func():
        st = SessionTouch.get_by_key_name(keyName)
        if not st:
            st = SessionTouch(key_name=keyName)
            st.user = u
            st.session = s
        st.time = now
        st.put()
    db.run_in_transaction(func)
    
    sl = SessionLog(createTime = now, user = u, session = s)
    sl.put()
    
    def func(uKey):
        u = User.get(uKey)
        u.session = s
        u.put()
        return u
    return db.run_in_transaction(func, u.key())

def createSession(u, title):
    s = Session()
    s.createTime = now
    s.title = title
    s.put()
    return enterSession(u, s)

def sendMessage(u, s, message):
    m = Message()
    m.createTime = now
    m.user = u
    m.session = s
    m.text = message
    m.put()
    
    for u2 in User.gql("where session = :session", session = s):
        jso = getMessageJso(m)
        jso["sessionKey"] = str(s.key())
        channelMessage(u2, {
            "type" : "message",
            "data" : jso
        })

def getMessageJso(m):
    return {
        "createTime" : m.createTime,
        "user" : getUserJso(m.user),
        "text" : m.text
    }

def getMessages(s):
    return [getMessageJso(m) for m in Message.gql("where session = :session order by createTime", session = s)]

def getSessionJso(u, s):
    jso = {
        "key" : str(s.key()),
        "createTime" : s.createTime,
        "title" : s.title
    }
    if u.session and u.session.key() == s.key():
        jso["current"] = True
        jso["messages"] = getMessages(s)
    return jso

def getSessionsJso(u):
    return [getSessionJso(u, st.session) for st in SessionTouch.gql("where user = :user order by time desc limit 10", user = u)]

@registerAPI("enterSession")
def func(user, session):
    user = enterSession(user, Session.get(Key(session)))
    return getSessionsJso(user)

@registerAPI("createSession")
def func(user, title):
    user = createSession(user, title)
    return getSessionsJso(user)

@registerAPI("getSessions")
def func(user):
    return getSessionsJso(user)

@registerAPI("sendMessage")
def func(user, message, session=""):
    sendMessage(user, Session.get(Key(session)) if session else user.session, message)

###############################################
# User

class User(db.Model):
    createTime = db.IntegerProperty()
    username = db.StringProperty()
    status = db.StringProperty(default="gone")
    statusTime = db.IntegerProperty(default=0)
    lastAnswerSummonsTime = db.IntegerProperty(default=0)
    session = db.ReferenceProperty()

class SessionLog(db.Model):
    createTime = db.IntegerProperty()
    user = db.ReferenceProperty(collection_name="c")
    session = db.ReferenceProperty(collection_name="d")

class SessionTouch(db.Model):
    time = db.IntegerProperty()
    user = db.ReferenceProperty(collection_name="e")
    session = db.ReferenceProperty(collection_name="f")

class StatusLog(db.Model):
    createTime = db.IntegerProperty()
    user = db.ReferenceProperty()
    status = db.StringProperty()

def getUser(username):
    u = User.get_by_key_name(username)
    if not u:
        u = User.get_or_insert(username,
            createTime = now,
            username = username)
        u = enterSession(u, getDefaultSession())
    return u

def getAvailable():
    return User.gql("where status in ('available', 'availableReserved') order by status desc, statusTime")

def getWorking():
    return User.gql("where status = 'working' order by statusTime")

def setStatus(u, status):
    if status not in set(["working", "available", "gone"]):
        raise BaseException("invalid status: " + status)
        
    if status == "available" and getReservation(u, getHour(now)):
        status = "availableReserved"
    
    sl = StatusLog()
    sl.createTime = now
    sl.user = u
    sl.status = status
    sl.put()
        
    def func(uKey):
        u = User.get(uKey)
        u.status = status
        u.statusTime = now
        u.put()
        return u
    return db.run_in_transaction(func, u.key())

def getUserJso(u, includePrivateInfo=False):
    jso = {
        "key" : str(u.key()),
        "createTime" : u.createTime,
        "username" : u.username,
        "status" : u.status,
        "sessionKey" : str(u.session.key() if u.session else None)
    }
    if includePrivateInfo:
        jso["lastAnswerSummonsTime"] = u.lastAnswerSummonsTime
    return jso

def getUsersJso(u):
    return {
        "me" : getUserJso(u, True),
        "working" : [getUserJso(u2) for u2 in getWorking()],
        "available" : [getUserJso(u2) for u2 in getAvailable()]
    }

@registerAPI("setStatus")
def func(user, status):
    user = setStatus(user, status)
    return getUsersJso(user)
    
@registerAPI("getUsers")
def func(user):
    return getUsersJso(user)

###############################################
# Summoning

summonTime = 30 * 1000
summonQueueInterval = 5 * 1000

class Portal(db.Model):
    createTime = db.IntegerProperty()
    user = db.ReferenceProperty(collection_name="nud")
    session = db.ReferenceProperty(collection_name="owd")
    deadline = db.IntegerProperty()
    maxUses = db.IntegerProperty()
    usesLeft = db.IntegerProperty()
    hasUsesLeft = db.BooleanProperty()

def getOpenPortal():
    return Portal.gql("where hasUsesLeft = True and deadline >= :now order by deadline", now = now).get()

def channelSummons(u):
    taskqueue.add(url='/_api/_eval', params={'code' : """
u = User.get(Key(%s))
if u.lastAnswerSummonsTime < %d:
    setStatus(u, "gone")
    channelMessage(u, {"type" : "summonsFail"})
""" % (json.dumps(str(u.key())), now)}, countdown = summonTime // 1000)
    channelMessage(u, {"type" : "summons"})

def summon(u, s, n=1):
    p = Portal()
    p.createTime = now
    p.user = u
    p.session = s
    p.deadline = now + (2 * summonTime)
    p.maxUses = n
    p.usesLeft = n
    p.hasUsesLeft = True
    p.put()
    
    i = 1 - n
    for u2 in getAvailable():
        taskqueue.add(url='/_api/_eval', params={'code' : """
if getOpenPortal():
    channelSummons(User.get(Key(%s)))
""" % json.dumps(str(u2.key()))}, countdown = ((i if i > 0 else 0) * summonQueueInterval) // 1000)
        i += 1

def answerSummons(u):
    def func(uKey):
        u = User.get(uKey)
        u.lastAnswerSummonsTime = now
        u.put()
    db.run_in_transaction(func, u.key())
    
    p = getOpenPortal()
    if p:
        def func(pKey):
            p = Portal.get(pKey)
            if p.usesLeft > 0:
                p.usesLeft -= 1
                p.hasUsesLeft = (p.usesLeft > 0)
                p.put()
                return True
        if db.run_in_transaction(func, p.key()):
            u = setStatus(u, "working")
            u = enterSession(u, p.session)
            return u

@registerAPI("summon")
def func(user, session=""):
    summon(user, Session.get(Key(session)) if session else user.session)

@registerAPI("answerSummons")
def func(user):
    u = answerSummons(user)
    return not not u

###############################################
# Reservation

maxReservedHours = 12
reservationWindowSize = 3 * 24

class Reservation(db.Model):
    createTime = db.IntegerProperty()
    hour = db.IntegerProperty()
    user = db.ReferenceProperty()
    
class ReservationAgg(db.Model):
    hour = db.IntegerProperty()
    count = db.IntegerProperty(default=0)

def getHour(t):
    return t // (1000 * 60 * 60)

def getReservationKeyName(u, hour):
    return sha1(str(u.key()) + "," + str(hour))
    
def getReservationAggKeyName(hour):
    return str(hour)

def getReservation(u, hour):
    return Reservation.get_by_key_name(getReservationKeyName(u, hour))

def deleteReservation(u, hour):
    r = getReservation(u, hour)
    if r:
        r.delete()

def getReservationWindow():
    startHour = getHour(now)
    endHour = startHour + reservationWindowSize
    return (startHour, endHour)

def getMyReservations(u):
    win = getReservationWindow()
    return Reservation.gql("where user = :user and hour >= :startHour and hour < :endHour", user = u, startHour = win[0], endHour = win[1])

def reserve(u, hour):
    win = getReservationWindow()
    if hour < win[0] or hour >= win[1]:
        raise BaseException("can't make reservations outside the reservation window from now until " + str(reservationWindowSize) + " hours from now")
    
    r_key_name = getReservationKeyName(u, hour)
    ra_key_name = getReservationAggKeyName(hour)
    def func():
        r = getReservation(u, hour)
        if not r:
            r = Reservation(key_name = r_key_name)
            r.createTime = now
            r.hour = hour
            r.user = u
            r.put()
            return True
    if db.run_in_transaction(func):
        if getMyReservations(u).count() > maxReservedHours:
            deleteReservation(u, hour)
            raise BaseException("too many hours reserved. your max is " + str(maxReservedHours))
        
        def func():
            ra = ReservationAgg.get_by_key_name(ra_key_name)
            if not ra:
                ra = ReservationAgg(key_name = ra_key_name)
                ra.hour = hour
            if ra.count < 2:
                ra.count += 1
                ra.put()
                return True
        if db.run_in_transaction(func):
            return True
        deleteReservation(u, hour)
        raise BaseException("too many reservations for this hour")
    raise BaseException("reservation already exists")

def unreserve(u, hour):
    deleteReservation(u, hour)
    
    def func():
        ra = ReservationAgg.get_by_key_name(getReservationAggKeyName(hour))
        ra.count -= 1
        ra.put()
    db.run_in_transaction(func)
    
def getReservationsJso(u):
    win = getReservationWindow()
    return {
        "all" : [{"hour" : r.hour, "count" : r.count} for r in ReservationAgg.gql("where hour >= :startHour and hour < :endHour", startHour = win[0], endHour = win[1])],
        "myHours" : [r.hour for r in getMyReservations(u)],
        "maxHours" : maxReservedHours,
        "windowSize" : reservationWindowSize
    }

@registerAPI("reserve")
def func(user, hour):
    reserve(user, int(hour))
    return getReservationsJso(user)

@registerAPI("unreserve")
def func(user, hour):
    unreserve(user, int(hour))
    return getReservationsJso(user)

@registerAPI("getReservations")
def func(user):
    return getReservationsJso(user)

###############################################
# Channel

def channelMessage(u, jso):
    send_message(u.username, json.dumps(jso))

@registerAPI("getChannel")
def func(user):
    return {
        "token" : create_channel(user.username)
    }

###############################################
# Misc

def getInfoJso(u):
    jso = {}
    jso["users"] = [getUserJso(u2) for u2 in User.all()]
    jso["sessions"] = getSessionsJso(u)
    jso["me"] = getUserJso(u, True)
    return jso

@registerAPI("getInfo")
def func(user):
    return getInfoJso(user)

@registerAPI("_clear")
def func(user):
    for i in Session.all():
        i.delete()
    for i in Message.all():
        i.delete()
        
    for i in User.all():
        i.delete()
    for i in SessionLog.all():
        i.delete()
    for i in SessionTouch.all():
        i.delete()
    for i in StatusLog.all():
        i.delete()
        
    for i in Portal.all():
        i.delete()
        
    for i in Reservation.all():
        i.delete()
    for i in ReservationAgg.all():
        i.delete()

###############################################
# main

if __name__ == "__main__":
    main()

