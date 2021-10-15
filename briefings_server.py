import base64
import csv
import datetime
import email
import email.mime
import email.mime.base
import hashlib
import itertools
import io
import json
import logging
import os.path
import random
import smtplib
import sqlite3
import socket
import tempfile
import threading
import time
import urllib
import uuid

import cherrypy
import jinja2
import dateutil
import dateutil.parser
import rauth
import requests
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


# TODO unify admin_judge, apply_index, and invite_index / unify the invitations and applications tables

file_dir = os.path.dirname(os.path.realpath(__file__))


logfile = os.path.join(file_dir,'briefings.log')
logging.basicConfig(filename=logfile,format='%(asctime)s:%(name)s:%(levelname)s:%(message)s',level=logging.DEBUG)
log = logging.getLogger('briefings')


sqlite3.register_adapter(bool, int)
sqlite3.register_converter("BOOLEAN", lambda v: bool(int(v)))

if not os.path.exists(os.path.join(file_dir,'database.sqlite')):
    raise Exception('Please run `create_db.sh` in order to create an empty sqlite database.')
def conn():
    conn = sqlite3.connect(os.path.join(file_dir,'database.sqlite'), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = 1")
    return conn

def conf(k):
    v, vtype = next(sqlite3.connect(os.path.join(file_dir,'config.sqlite')).execute('SELECT value, valuetype FROM config WHERE key=?',(k,)))
    if vtype=='str':
        return v
    elif vtype=='str[]':
        return [_.strip() for _ in v.split(',')]
    elif vtype=='html':
        return v
    elif vtype=='int':
        return int(v)
    elif vtype=='bool':
        return v=='True'
    else:
        raise ValueError('Unknown Value Type')

def updateconf(k,v):
    conn = sqlite3.connect(os.path.join(file_dir,'config.sqlite'))
    with conn:
        c = conn.cursor()
        c.execute('UPDATE config SET value=? WHERE key=?',(v,k))


templates = jinja2.Environment(loader=jinja2.FileSystemLoader(searchpath=os.path.join(file_dir,'templates/')))
templates.globals['EVENT_NAME'] = conf('event.name')
templates.globals['DESCRIPTION'] = conf('event.description')
templates.globals['URL'] = conf('server.url')
templates.globals['KEYWORDS'] = conf('event.keywords')


def send_email(text_content, html_content, emailaddr, subject, pngbytes_cids=[], file_atts=[], cc=[]):
    log.debug('attempting to send email "%s" <%s>'%(subject, emailaddr))
    msg = email.message.EmailMessage()
    msg.set_content(text_content)
    msg['Subject'] = subject
    msg['From'] = email.headerregistry.Address(conf('email.from_display'), conf('email.from_user'), conf('email.from'))
    msg['To'] = emailaddr
    msg['Cc'] = ','.join([conf('email.cc')]+cc)

    msg.add_alternative(html_content, subtype='html')
    for pngbytes, cid in pngbytes_cids:
        msg.get_payload()[1].add_related(pngbytes, 'image', 'png', cid=cid)
    for att in file_atts:
        msg.attach(att)
    
    username = conf('email.SMTPuser')
    password = conf('email.SMTPpass')
    server = smtplib.SMTP(socket.gethostbyname(conf('email.SMTPhost'))+':'+conf('email.SMTPport')) # XXX workaround for IPv6 bugs with Digital Ocean
    server.ehlo()
    server.starttls()
    server.login(username,password)
    server.send_message(msg)
    server.quit()


ZOOM_TEMPLATE = {
                 #"topic": 'Meeting',
                 "type": 2,
                 #"start_time": "",
                 "duration": 240,
                 "timezone": 'America/New_York',
                 "settings": {
                   "host_video": False,
                   "participant_video": False,
                   "join_before_host": False,
                   "mute_upon_entry": True,
                   "waiting_room": True
                 }
               }


class Root:
    @cherrypy.expose
    def index(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link FROM events WHERE warmup=0 ORDER BY date ASC'))
        records = [t for t in all_talks if t[0]>datetime.datetime.now()]
        return templates.get_template('__index.html').render(records=records, calendarframe=conf('google.calendariframe'), banner=conf('frontpage.banner'), customfooter=conf('frontpage.footer'), ical=conf('google.calendarical'))

    @cherrypy.expose
    def iframeupcoming(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link FROM events WHERE warmup=0 ORDER BY date ASC'))
        records = [t for t in all_talks if t[0]>datetime.datetime.now()]
        return templates.get_template('__iframeupcoming.html').render(records=records)

    @cherrypy.expose
    def past(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, recording_consent, recording_link FROM events WHERE warmup=0 ORDER BY date DESC'))
        records = [t for t in all_talks if t[0]<datetime.datetime.now()]
        return templates.get_template('__past.html').render(records=records)

    @cherrypy.expose
    def event(self, date, warmup):
        try:
            with conn() as c:
                warmup = warmup and not (warmup=='False' or warmup=='0')
                parseddate = dateutil.parser.isoparse(date)
                talk = c.execute('SELECT date, warmup, speaker, affiliation, title, abstract, bio, conf_link, recording_consent, recording_link FROM events WHERE date=? AND warmup=? ORDER BY date DESC', (parseddate, warmup)).fetchone()
                if not warmup:
                    has_warmup = c.execute('SELECT COUNT(*) FROM events WHERE warmup=? AND date=?', (True, parseddate)).fetchone()[0]
            future = talk[0]>datetime.datetime.now()
        except:
            log.error('Attempted opening unknown talk %s %s'%(date, warmup))
            return templates.get_template('__blank.html').render(content='There does not exist a talk given at that time in our database!')
        return templates.get_template('__event.html').render(talk=talk, future=future, has_warmup=not warmup and has_warmup)


class Apply:
    @cherrypy.expose
    def index(self):
        slots = self.available_talks()
        if slots:
            return templates.get_template('apply_index.html').render(slots=slots)
        else:
            return templates.get_template('apply_blank.html').render(content='Currently there are no available slots for "warmup" talks.')

    @staticmethod
    def available_talks():
        with conn() as c:
            c = c.cursor()
            maintalks = list(c.execute('SELECT date, speaker, title FROM events WHERE warmup=0'))
            warmuptalks = set(d[0] for d in c.execute('SELECT date FROM events WHERE warmup!=0'))
        main_talks_dict = {d: (s,t) for d,s,t in maintalks}
        good_dates = set(main_talks_dict.keys()) - warmuptalks
        good_talks = [(d,*main_talks_dict[d]) for d in good_dates if d>datetime.datetime.now()]
        return good_talks

    @cherrypy.expose
    def do(self, **kwargs):
        uid = str(uuid.uuid4())
        args = 'speaker, affiliation, bio, title, abstract, email, previous_records'
        args_s = args.split(', ')
        data = []
        for a in args_s:
            v = kwargs.get(a)
            data.append(v)
        dates = [dateutil.parser.isoparse(v) for k,v in kwargs.items()
                 if k.startswith('date')]
        dates = '|'.join(repr(d) for d in dates) # TODO register a converter
        args = args + ', warmup, uuid, dates'
        args_s.extend(['warmup', 'uuid', 'dates'])
        data.extend([True, uid, dates])
        data_dict = dict(zip(args_s, data))
        placeholders = ("?,"*len(args_s))[:-1]
        good_talks = self.available_talks()
        if set(dates) > set([g for g,s,t in good_talks]):
            return templates.get_template('apply_blank.html').render(content='There was a problem with parsing the dates! Contact the administrator if the problem persists!')
        with conn() as c:
            c = c.cursor()
            c.execute('INSERT INTO applications (%s) VALUES (%s)'%(args, placeholders),
                      data)
        text_content = 'Submission successful! You will receive an email with a decision, depending on availability, before the talk.'
        html_content = '<p>%s</p><h2>%s</h2><strong>%s</strong><p>%s</p><p>%s</p><p>%s</p>'%(text_content, *[data_dict[k] for k in ['title','speaker','abstract','bio','previous_records']])
        subject = 'Speaker application: %s'%data_dict['title']
        send_email(text_content, html_content, data_dict['email'], subject)
        return templates.get_template('apply_blank.html').render(content=text_content)


def available_dates(uuid, table='invitations'):
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT dates, warmup, confirmed_date FROM %s WHERE uuid=?'%table, (uuid,))
        dates, warmup, confirmed_date  = c.fetchone()
    suggested_dates = [eval(d) for d in dates.split('|')] # TODO better parsing... actually better storing of array of dates too
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT date FROM events WHERE warmup=?', (warmup,))
        occupied_dates = [d[0] for d in c.fetchall()]
    good_dates = set(suggested_dates) - set(occupied_dates)
    if confirmed_date:
        good_dates = good_dates.union(set([confirmed_date]))
    today = datetime.datetime.now()
    good_dates = sorted([d for d in good_dates if d>today])
    return good_dates, confirmed_date


@cherrypy.popargs('uuid')
class Invite:
    @cherrypy.expose
    def index(self, uuid):
        try:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT email, warmup, host, host_email FROM invitations WHERE uuid=?;', (uuid,))
                email, warmup, host, host_email  = c.fetchone()
        except:
            log.error('Attempted opening unknown invite %s %s %s'%(uuid, email, warmup))
            return templates.get_template('invite_blank.html').render(content='This invation is invalid! Please contact whomever sent you the invite!')
        good_dates, confirmed_date = available_dates(uuid)
        args = 'speaker, affiliation, bio, title, abstract, recording_consent, conf_link'
        if confirmed_date:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT %s FROM events WHERE date=? AND warmup=?'%args,
                          (confirmed_date, warmup))
                data = c.fetchone()
            args_s = args.split(', ')
            old_data = dict(zip(args_s, data))
        else:
            old_data = dict()
        return templates.get_template('invite_index.html').render(dates=good_dates, confirmed_date=confirmed_date, email=email, uuid=uuid, warmup=warmup, old_data=old_data, host=host, host_email=host_email)

    @cherrypy.expose
    def do(self, **kwargs):
        uuid = kwargs['uuid']
        args = 'date, speaker, affiliation, bio, title, abstract, warmup, email, recording_consent'
        args_s = args.split(', ')
        data = []
        placeholders = ("?,"*len(args_s))[:-1]
        bools = ['warmup', 'recording_consent']
        for a in args_s:
            v = kwargs.get(a)
            if a in bools:
                v = v == 'True' or v == 'Yes'
            data.append(v)
        data[0] = dateutil.parser.isoparse(data[0])
        good_dates, confirmed_date = available_dates(uuid)
        data_dict = dict(zip(args_s, data))
        if confirmed_date and confirmed_date < datetime.datetime.now():
            return templates.get_template('invite_blank.html').render(content='Can not edit past events!')
        if data[0] not in good_dates:
            return templates.get_template('invite_blank.html').render(content='There was a problem with reserving the date! Please contact whomever sent you the invite!')
        with conn() as c:
            c = c.cursor()
            args += ', host, host_email'
            placeholders += ',?,?'
            c.execute('SELECT email, warmup, host, host_email FROM invitations WHERE uuid=?;', (uuid,))
            email, warmup, host, host_email  = c.fetchone()
            data.extend([host, host_email])
            c.execute("""INSERT INTO events (%s) VALUES (%s)
                         ON CONFLICT(date, warmup)
                         DO UPDATE SET %s"""%(
                             args, placeholders,
                             ', '.join('%s=excluded.%s'%(a,a) for a in args_s)
                         ),
                      data)
            c.execute('UPDATE invitations SET confirmed_date=? WHERE uuid=?',
                      (data[0],uuid))

        # Zoom and Calendar and Drive
        if not confirmed_date:
            # Zoom
            zoom_meet_config = {'start_time': data_dict['date'].isoformat('T'),
                                'topic': conf('event.name')+": "+data_dict['speaker'],
                                **ZOOM_TEMPLATE}
            try:
                url = Zoom.post('/users/me/meetings', data=zoom_meet_config).json()['join_url']
                with conn() as c:
                    c = c.cursor()
                    c.execute('UPDATE events SET conf_link=? WHERE date=? AND warmup=?', (url, data_dict['date'], data_dict['warmup']))
            except:
                log.error('Could not create a Zoom room for %s %s'%(data_dict['date'], data_dict['warmup']))
            # Calendar
            creds = Google.getcreds()
            with build('calendar','v3',credentials=creds) as service:
                title = data_dict["speaker"]+": "+data_dict["title"]
                j = service.events().quickAdd(calendarId=conf('google.calendarid'),text=title).execute()
                event_id = j["id"]
                date = data_dict["date"]
                j["start"]["dateTime"] = date.isoformat('T')
                j["end"]["dateTime"] = (date+datetime.timedelta(hours=1)).isoformat('T')
                nj = {
                        "start": j["start"],
                        "end": j["end"],
                        "description": data_dict["abstract"]}
                j = service.events().patch(calendarId=conf('google.calendarid'),eventId=event_id,body=nj).execute()
        # Email
        text_content = subject = '%s, you submitted your talk for %s!'%(data_dict['speaker'], data_dict['date'])
        url = 'https://'+conf('server.url')+'/invite/'+uuid
        public_url = 'https://'+conf('server.url')+'/event/'+str(data_dict['date'])+'/'+str(data_dict['warmup'])
        html_content = '<p>You can view updated information about your talk (videoconf link and private schedule) at <a href="%s">%s</a>. <strong>Keep this link private</strong>.<br>For the public announcement see <a href="%s">%s</a></p>'%(url, url, public_url, public_url) 
        send_email(text_content, html_content, data_dict['email'], subject, cc=[host_email] if host_email else [])

        return templates.get_template('invite_blank.html').render(content='Submission successful! '+html_content)


def add_default_time_to_date(date):
    if date.hour == 0 and date.minute == 0 and date.second == 0:
        hour = conf('timing.default_hour')
        minute = conf('timing.default_minute')
        d = datetime.datetime(date.year, date.month, date.day, hour, minute, 0)
    else:
        d=date
    return d


class Admin:
    @cherrypy.expose
    def index(self):
        return templates.get_template('admin_blank.html').render(content='From here you can configure the website, invite speakers, and judge applications for warmup talks.')

    @cherrypy.expose
    def config(self):
        configrecords = list(sqlite3.connect(os.path.join(file_dir,'config.sqlite')).execute('SELECT key, value, valuetype, help FROM config ORDER BY key'))
        configrecords.sort(key = lambda _:_[0].split('.')[0])
        configrecords = itertools.groupby(configrecords, key = lambda _:_[0].split('.')[0])
        return templates.get_template('admin_config.html').render(configrecords=configrecords)

    @cherrypy.expose
    def update(self, *args, **kwargs):
        key = args[0]
        value = kwargs['value']
        with sqlite3.connect(os.path.join(file_dir,'config.sqlite')) as conn:
            conn.cursor().execute('UPDATE config SET value=? WHERE key=?', (value,key))
            conn.commit()
        raise cherrypy.HTTPRedirect("/admin/config#panel-%s"%key)

    @cherrypy.expose
    def invite(self):
        return templates.get_template('admin_invite.html').render()

    @cherrypy.expose
    def invitedo(self, **kwargs):
        kwargs = {k:v.strip() for k,v in kwargs.items()}
        email = kwargs['email']
        try:
            dates = [add_default_time_to_date(dateutil.parser.isoparse(_.strip()))
                     for _ in kwargs['dates'].split(',')]
        except:
            log.error('Could not parse dates %s'%(dates))
            return templates.get_template('admin_blank.html').render(content='There was a problem with the parsing of the dates! Try again!')
        warmup = 'warmup' in kwargs
        send = 'send' in kwargs
        host = kwargs.get('hname')
        host_email = kwargs.get('hemail')
        uid = str(uuid.uuid4())
        try:
            with conn() as c:
                c.execute('INSERT INTO invitations (uuid, email, dates, warmup, host, host_email, confirmed_date) VALUES (?, ?, ?, ?, ?, ?, NULL)',
                          (uid, email, '|'.join(repr(d) for d in dates), warmup, host, host_email))
        except:
            log.error('Could not insert '%((uid, email, '|'.join(repr(d) for d in dates), warmup, host, host_email),))
            return templates.get_template('admin_blank.html').render(content='There was a problem with the database! Try again!')
        # Email
        text_content = subject = conf('invitations.email_subject_line')
        invite_link = 'https://'+conf('server.url')+'/invite/'+uid
        invite_link = '<a href="%s">%s</a>'%(invite_link, invite_link)
        dates = '<ul>%s</ul>'%''.join('<li>%s</li>'%d for d in dates)
        html_content = conf('invitations.email_message').format(dates=dates, invite_link=invite_link,host=host)
        html_panel = '<div class="panel panel-default"><div class="panel-body">%s</div></div>'%html_content
        email_link = '<a href="mailto:%s">%s</a>'%(email,email)
        if kwargs.get('send'):
            try:
                send_email(text_content, html_content, email, subject)
                mail_note = 'The following email was sent to %s:'%email_link
            except:
                log.error('Email failed to send to %s',email)
                mail_note = 'The email to %s failed to send. Contact the admin to investigate. Here is the email content if you prefer to send it manually:'%email_link
            return templates.get_template('admin_blank.html').render(content='The invite is available at <a href="/invite/%s">/invite/%s</a>. %s<div>%s</div>'%(uid,uid,mail_note,html_panel))
        else:
            return templates.get_template('admin_blank.html').render(content='The invite is available at <a href="/invite/%s">/invite/%s</a>. No emails were send, but here is a draft you can use yourself when mailing %s: <div>%s</div>'%(uid,uid, email_link, html_panel))

    @cherrypy.expose
    def invitestatus(self):
        with conn() as c:
            all_invites = list(c.execute('SELECT uuid, email, confirmed_date FROM invitations'))[::-1]
        content = ''.join('<li><a href="/invite/{uuid}">{email} | {accepted}</a>'.format(
                          uuid=uuid, email=email, accepted='accepted for %s'%confirmed_date if confirmed_date else 'not accepted yet')
                for (uuid, email, confirmed_date) in all_invites)
        content = "<h1>All Invites</h1><ul>%s</ul>"%content
        return templates.get_template('admin_blank.html').render(content=content)

    @cherrypy.expose
    def applicationsstatus(self):
        with conn() as c:
            all_apps = list(c.execute('SELECT uuid, speaker, title FROM applications WHERE declined=0 AND confirmed_date IS NULL'))[::-1]
        content = ''.join('<li><a href="/admin/judge/{uuid}">{speaker} | {title}</a>'.format(
                          uuid=uuid, speaker=speaker, title=title)
                          for (uuid, speaker, title) in all_apps)
        content = "<h1>Pending applications</h1><ul>%s</ul>"%content
        return templates.get_template('admin_blank.html').render(content=content)

    @cherrypy.expose
    def judge(self, uuid):
        args = 'speaker,affiliation,bio,title,abstract,warmup,email,dates,previous_records,confirmed_date,declined'
        try:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT %s FROM applications WHERE uuid=?;'%args, (uuid,))
                data = c.fetchone()
        except:
            log.error('Attempted opening unknown application %s %s %s'%(uuid, email, warmup))
            return templates.get_template('admin_blank.html').render(content='This application is invalid! Please contact whomever sent you the invite!')
        good_dates, confirmed_date = available_dates(uuid, table='applications')
        args_s = args.split(',')
        data_dict = dict(zip(args_s, data))
        return templates.get_template('admin_judge.html').render(dates=good_dates, confirmed_date=confirmed_date, uuid=uuid, warmup=data_dict['warmup'], data=data_dict)

    @cherrypy.expose
    def judgedo(self, **kwargs):
        uuid = kwargs['uuid']
        good_dates, confirmed_date = available_dates(uuid, table='applications')
        if confirmed_date:
            return templates.get_template('invite_blank.html').render(content='This app has already been accepted!')
        args = 'speaker,affiliation,bio,title,abstract,warmup,email,dates,previous_records,confirmed_date,declined'
        try:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT %s FROM applications WHERE uuid=?;'%args, (uuid,))
                data = c.fetchone()
        except:
            log.error('Attempted accepting unknown application %s %s %s'%(uuid, email, warmup))
            return templates.get_template('admin_blank.html').render(content='This application is invalid! Please contact whomever sent you the invite!')
        args_s = args.split(',')
        data_dict = dict(zip(args_s, data))
        if data_dict['declined']:
            return templates.get_template('admin_blank.html').render(content='This app has already been accepted!')
        confirmed_date = dateutil.parser.isoparse(kwargs['date'])
        if confirmed_date not in good_dates:
            return templates.get_template('admin_blank.html').render(content='The selected date is not available!')
        if not data_dict['warmup']:
            return templates.get_template('admin_blank.html').render(content='Only warmup talks are supported through this interface for the moment! Contact the admin for help!')

        args = 'speaker,affiliation,bio,title,abstract,warmup,email,previous_records,date,recording_consent'
        args_s = args.split(',')
        placeholders = ','.join(['?']*len(args_s))
        data_dict['date'] = confirmed_date
        data_dict['recording_consent'] = True
        with conn() as c:
            c = c.cursor()
            c.execute("INSERT INTO events (%s) VALUES (%s)"%(
                             args, placeholders,
                         ),
                      [data_dict[a] for a in args_s])
            c.execute('UPDATE applications SET confirmed_date=? WHERE uuid=?',
                      (confirmed_date,uuid))
        return templates.get_template('admin_blank.html').render(content='Application accepted!')

    @cherrypy.expose
    def authzoom(self):
        Zoom.start_auth()

    @cherrypy.expose
    def testzoom(self, test=None):
        if test=='make_meeting':
            zoom_meet_config = {'start_time':str(datetime.datetime.now()),
                                'topic': 'Test Meeting '+conf('event.name'),
                                **ZOOM_TEMPLATE}
            j = Zoom.post('/users/me/meetings', data=zoom_meet_config).json()
            zoom_meet_config = {'start_time':str(datetime.datetime.now()), **ZOOM_TEMPLATE}
            j = Zoom.post('/users/me/meetings', data=zoom_meet_config).json()
        else:
            j = Zoom.get('/users/me').json()
        content = '<pre>%s</pre>'%json.dumps(j, indent=4)
        return templates.get_template('admin_blank.html').render(content=content)

    @cherrypy.expose
    def authgoogle(self):
        Google.start_auth()

    @cherrypy.expose
    def testgoogle(self, test=None):
        creds = Google.getcreds()
        if test=='calendar':
            with build('calendar','v3',credentials=creds) as service:
                j = service.calendars().get(calendarId=conf('google.calendarid')).execute()
        elif test=='createevent':
            with build('calendar','v3',credentials=creds) as service:
                j = service.events().quickAdd(calendarId=conf('google.calendarid'),text="Internal meeting").execute()
                event_id = j["id"]
                date = datetime.datetime.now()
                j["start"]["dateTime"] = date.isoformat('T')
                j["end"]["dateTime"] = (date+datetime.timedelta(hours=1)).isoformat('T')
                nj = {
                        "start": j["start"],
                        "end": j["end"],
                        "description": "Internal meeting"}
                j = service.events().patch(calendarId=conf('google.calendarid'),eventId=event_id,body=nj).execute()
        else:
            with build('drive','v3',credentials=creds) as service:
                j = service.about().get(fields='*').execute()
        content = '<pre>%s</pre>'%json.dumps(j, indent=4)
        return templates.get_template('admin_blank.html').render(content=content)


class Dev:
    @cherrypy.expose
    def objgraph(self):
        import objgraph
        return '<br>'.join(map(str,objgraph.most_common_types(limit=300)))
    @cherrypy.expose
    def log(self):
        import subprocess
        lines = subprocess.Popen(['tail','-n',1000,logfile], stdout=subprocess.PIPE).stdout.readlines()
        return '<pre>%s</pre>'%'\n'.join(lines)


class Zoom:
    @cherrypy.expose
    def index(self):
        return "Zoom integration is controlled from the admin panel."

    @staticmethod
    def get_token(code=None):
        clientid = conf('zoom.clientid')
        clientsecret = conf('zoom.clientsecret')
        redirecturl = 'https://'+conf('server.url')+'/zoom/receive_code'
        refresh_token = conf('zoom.refreshtoken')
        access_token = conf('zoom.accesstoken')
        if code:
            grant_type = 'grant_type=authorization_code&code='+code
        else:
            grant_type = 'grant_type=refresh_token&refresh_token='+refresh_token
        url = 'https://zoom.us/oauth/token?' + grant_type + '&client_id=' + clientid + '&client_secret=' + clientsecret + '&redirect_uri=' + redirecturl
        r = requests.post(url)
        j = r.json()
        updateconf('zoom.accesstoken', j.get('access_token', access_token))
        updateconf('zoom.refreshtoken', j.get('refresh_token',refresh_token))
        return j

    @staticmethod
    def get_session():
        Zoom.get_token() # TODO refresh only on errors
        clientid = conf('zoom.clientid')
        clientsecret = conf('zoom.clientsecret')
        access_token = conf('zoom.accesstoken')
        session = rauth.OAuth2Session(
           client_id=clientid,
           client_secret=clientsecret,
           access_token=access_token)
        return session

    @staticmethod 
    def get(r, params={}):
        base_url='https://api.zoom.us/v2'
        s = Zoom.get_session()
        return s.get(base_url+r, params=params)

    @staticmethod 
    def post(r, data={}):
        base_url='https://api.zoom.us/v2'
        s = Zoom.get_session()
        return s.post(base_url+r, json=data)

    @staticmethod # TODO not tested
    def patch(r, data={}):
        base_url='https://api.zoom.us/v2'
        s = Zoom.get_session()
        return s.patch(base_url+r, json=data)

    @staticmethod
    def start_auth():
        clientid = conf('zoom.clientid')
        clientsecret = conf('zoom.clientsecret')
        redirecturl = 'https://'+conf('server.url')+'/zoom/receive_code'
        raise cherrypy.HTTPRedirect('https://zoom.us/oauth/authorize?response_type=code&client_id=' + clientid + '&redirect_uri=' + redirecturl)

    @cherrypy.expose
    def receive_code(self, code):
        clientid = conf('zoom.clientid')
        clientsecret = conf('zoom.clientsecret')
        redirecturl = 'https://'+conf('server.url')+'/zoom/receive_code'
        j = self.get_token(code=code)
        content = 'Success!'
        return templates.get_template('admin_blank.html').render(content=content)


class Google:
    scopes=['openid', 'https://www.googleapis.com/auth/userinfo.profile', 'https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive']
    @staticmethod
    def getflow():
        redirecturl = 'https://'+conf('server.url')+'/google/receive_code'
        client_config = json.loads(conf('google.client_secrets'))
        flow = Flow.from_client_config(client_config, scopes=Google.scopes, redirect_uri=redirecturl)
        return flow

    @cherrypy.expose
    def index(self):
        return "Google integration is controlled from the admin panel."

    @staticmethod
    def start_auth():
        flow = Google.getflow()
        passthrough_val = hashlib.sha256(os.urandom(1024)).hexdigest()
        auth_url, state = flow.authorization_url(access_type='offline',state=passthrough_val,include_granted_scopes='true')
        raise cherrypy.HTTPRedirect(auth_url)
        
    @cherrypy.expose
    def receive_code(self, **kwargs):
        flow = self.getflow()
        flow.fetch_token(code=kwargs['code']) # TODO you should check passthrough_val...
        j = flow.credentials.to_json()
        updateconf('google.credential_tokens',j)
        content = 'Success!'
        return templates.get_template('admin_blank.html').render(content=content)

    @staticmethod
    def getcreds():
        tokens = json.loads(conf('google.credential_tokens'))
        creds = Credentials.from_authorized_user_info(tokens, scopes=Google.scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            j = creds.to_json()
            tokens.update(json.loads(j))
            updateconf('google.credential_tokens',json.dumps(tokens))
        return creds


def auth(realm,u,p):
    log.info('attempting to access protected area %s'%((realm,u,p),))
    return p==conf('admin.pass') and u==conf('admin.user')


if __name__ == '__main__':
    log.info('server starting')
    cherrypy.config.update({'server.socket_host'     : '127.0.0.1',
                            'server.socket_port'     : conf('server.port'),
                            'tools.encode.on'        : True,
                            #'environment'            : 'production',
                            'tools.sessions.on'      : True,
                            'tools.sessions.timeout' : 60,
                            'tools.caching.on'       : False,
                           })

    static_conf = {'/static':{
                              'tools.staticdir.on'   : True,
                              'tools.staticdir.dir'  : '',
                              'tools.staticdir.root' : os.path.join(os.path.dirname(os.path.realpath(__file__)),'static'),
                             }}
    customfiles_conf = {'/customfiles':{# Almost certainly this should be overwritten by your nginx config.
                              'tools.staticdir.on'   : True,
                              'tools.staticdir.dir'  : '',
                              'tools.staticdir.root' : os.path.join(os.path.dirname(os.path.realpath(__file__)),'customfiles'),
                             }}
    video_conf = {'/video':{# Almost certainly this should be overwritten by your nginx config.
                              'tools.staticdir.on'   : True,
                              'tools.staticdir.dir'  : '',
                              'tools.staticdir.root' : conf('zoom.recdownloads'),
                             }}
    password_conf = {'/':{
                          'tools.auth_basic.on': True,
                          'tools.auth_basic.realm': 'engday',
                          'tools.auth_basic.checkpassword': auth,
                         }}
    cherrypy.tree.mount(Root(), '/', {**static_conf,**video_conf,**customfiles_conf})
    cherrypy.tree.mount(Invite(), '/invite', {})
    cherrypy.tree.mount(Apply(), '/apply', {})
    cherrypy.tree.mount(Admin(), '/admin', password_conf)
    cherrypy.tree.mount(Dev(), '/dev', password_conf)
    cherrypy.tree.mount(Zoom(), '/zoom', {})
    cherrypy.tree.mount(Google(), '/google', {})
    cherrypy.engine.start()
    cherrypy.engine.block()
    log.info('server stopping')
