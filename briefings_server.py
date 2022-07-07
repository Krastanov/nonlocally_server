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
from cherrypy.process.plugins import Monitor
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
def conn(d=False):
    conn = sqlite3.connect(os.path.join(file_dir,'database.sqlite'), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = 1")
    if d:
        conn.row_factory = dict_factory
    return conn

def dict_factory(cursor, row): # TODO use this everywhere
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

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

def parsedates(dates): # TODO this should be automatically done as a registered converter
    return [eval(d) for d in dates.split('|')] # TODO better parsing... actually better storing of array of dates too

templates = jinja2.Environment(loader=jinja2.FileSystemLoader(searchpath=os.path.join(file_dir,'templates/')))
templates.globals['EVENT_NAME'] = conf('event.name')
templates.globals['DESCRIPTION'] = conf('event.description')
templates.globals['URL'] = conf('server.url')
templates.globals['KEYWORDS'] = conf('event.keywords')


def send_email(text_content, html_content, emailaddr, subject, pngbytes_cids=[], file_atts=[], cc=[]):
    log.debug('attempting to send email "%s" <%s>'%(subject, emailaddr))
    try:
        msg = email.message.EmailMessage()
        msg.set_content(text_content)
        msg['Subject'] = subject
        msg['From'] = email.headerregistry.Address(conf('email.from_display'), conf('email.from_user'), conf('email.from'))
        msg['To'] = emailaddr
        msg['Cc'] = ','.join(conf('email.cc')+cc+[conf('sysadmin.email')])

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
    except:
        log.error('failed to send email "%s" <%s>'%(subject, emailaddr))


def ZOOM_TEMPLATE():
    return {
    #"topic": 'Meeting',
    "type": 2,
    #"start_time": "",
    "duration": 240,
    "timezone": 'America/New_York',
    "settings": {
      "alternative_hosts": ';'.join(conf('zoom.alternative_hosts')),
      "host_video": False,
      "participant_video": False,
      "join_before_host": False,
      "mute_upon_entry": True,
      "waiting_room": True
    }
    }


# Scheduled Events

def check_upcoming_talks_and_email():
    try:
        log.debug('Checking whether we need to send an email announcement for talks')
        with conn(d=True) as c:
            upcoming_talks = c.execute("SELECT * FROM events WHERE announced=0 AND date>date('now','+2 day') AND date<date('now','+10 day')").fetchall()
            all_upcoming_talks = list(c.execute("SELECT * FROM events WHERE announced=0 AND date>date('now') AND date<date('now','+60 day')"))
        for r in upcoming_talks:
            event = conf('event.name')
            datestr = r['date'].strftime('%b %-d %-I:%M%p')
            subject = f"Upcoming talk {datestr} - {r['title']} by {r['speaker']}"
            priv_subject = f"Meet the speaker - {r['title']} by {r['speaker']}"
            public_url = 'https://'+conf('server.url')+'/event/'+str(r['date'])+'/'+str(r['warmup'])
            upcoming_url = 'https://'+conf('server.url')
            if all_upcoming_talks:
                _html = "".join([f"<p>{t['date']} | {t['title']} - {t['speaker']}</p>" for t in all_upcoming_talks if t!=r])
                _plain = "\n".join([f"{t['date']} | {t['title']} - {t['speaker']}" for t in all_upcoming_talks if t!=r])
                future_talks_html = f"<div><h2>Future talks (<a href=\"{upcoming_url}\">listed at {upcoming_url}</a>)</h2>{_html}</div>"
                future_talks_plain = f"\n\nFuture talks listed at {upcoming_url}\n{_plain}"
            else:
                future_talks_html = f"<div><a href=\"{upcoming_url}\">{upcoming_url}</div>"
                future_talks_plain = f"\n\n{upcoming_url}"
            priv_signup_html = f"<div><h2>Private meeting signup</h2><a href=\"{r['sched_link']}\">{r['sched_link']}</a></div>"
            priv_signup_plain = f"\nPrivate meeting signup: {r['sched_link']}"
            html = f"""
            <strong>{event} - {datestr}</strong>
            <h2>{r['title']}</h2>
            <h3>{r['speaker']} - {r['affiliation']}</h3>
            <div><p>Abstract: </p><p style=\"white-space:pre-wrap;\">{r['abstract']}</p></div>
            <div><p>Bio:</p><p style=\"white-space:pre-wrap;\">{r['bio']}</p></div><div></div>
            <div>
            <p><strong>Video Conference link</strong>: <a href=\"{r['conf_link']}\">{r['conf_link']}</a></p>
            <p><strong>More details</strong>: <a href=\"{public_url}\">{public_url}</a></p>
            <p><strong>Location</strong>: {r['location']}</p>
            </div>"""
            plain = f"{event} - {datestr}\n{r['title']}\n{r['speaker']} - {r['affiliation']}\n\nAbstract: {r['abstract']}\n\nBio: {r['bio']}\n\nVideo Conference link: {r['conf_link']}\nMore details: {public_url}\nLocation: {r['location']}"
            speaker_email = r['email']
            host_email = r['host_email']
            mailing_list_email = conf("email.mailing_list")
            priv_mailing_list_email = conf("email.priv_mailing_list")
            send_email(plain+future_talks_plain, html+future_talks_html, mailing_list_email, subject, cc=[speaker_email, host_email])
            send_email(plain+priv_signup_plain+future_talks_plain, html+priv_signup_html+future_talks_html, priv_mailing_list_email, priv_subject, cc=[speaker_email, host_email])
            with conn() as c:
                c.execute('UPDATE events SET announced=1 WHERE date=? AND warmup=?',
                          (r['date'],r['warmup']))
    except Exception as e:
        log.error('Failure in the email annoucements scheduled job due to %s'%e)

scheduled_events = [
    (check_upcoming_talks_and_email, 3600*24),
        ]

# CherryPy server

class Root:
    @cherrypy.expose
    def index(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link, location FROM events WHERE warmup=0 ORDER BY date ASC'))
        now = datetime.datetime.now() - datetime.timedelta(days=1)
        records = [t for t in all_talks if t[0]>now]
        return templates.get_template('__index.html').render(records=records, calendarframe=conf('google.calendariframe'), banner=conf('frontpage.banner'), customfooter=conf('frontpage.footer'), ical=conf('google.calendarical'))

    @cherrypy.expose
    def iframeupcoming(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link, location FROM events WHERE warmup=0 ORDER BY date ASC'))
        now = datetime.datetime.now() - datetime.timedelta(days=1)
        records = [t for t in all_talks if t[0]>now]
        return templates.get_template('__iframeupcoming.html').render(records=records)

    @cherrypy.expose
    def past(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, recording_consent, recording_link, location FROM events WHERE warmup=0 ORDER BY date DESC'))
        now = datetime.datetime.now() 
        yesterday = datetime.datetime.now()-datetime.timedelta(days=1)
        records = [(*t,t[0]>yesterday) for t in all_talks if t[0]<now]
        return templates.get_template('__past.html').render(records=records)

    @cherrypy.expose
    def event(self, date, warmup):
        try:
            with conn() as c:
                warmup = warmup and not (warmup=='False' or warmup=='0') # TODO this should not be such a mess to parse
                parseddate = dateutil.parser.isoparse(date)
                talk = c.execute('SELECT date, warmup, speaker, affiliation, title, abstract, bio, conf_link, recording_consent, recording_link, location FROM events WHERE date=? AND warmup=? ORDER BY date DESC', (parseddate, warmup)).fetchone()
                if not warmup:
                    has_warmup = c.execute('SELECT COUNT(*) FROM events WHERE warmup=? AND date=?', (True, parseddate)).fetchone()[0]
            future = talk[0]>datetime.datetime.now()+datetime.timedelta(days=1)
        except:
            log.error('Attempted opening unknown talk %s %s'%(date, warmup))
            return templates.get_template('__blank.html').render(content='There does not exist a talk given at that time in our database!')
        return templates.get_template('__event.html').render(talk=talk, future=future, has_warmup=not warmup and has_warmup)

    @cherrypy.expose
    def about(self):
        return templates.get_template('__about.html').render(seminar=conf('event.name'),description=conf('event.description'),longdescription=conf('event.longdescription'),aboutnonlocally='')


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


def available_dates(uuid, table='invitations', daysoffset=0):
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT dates, warmup, confirmed_date FROM %s WHERE uuid=?'%table, (uuid,))
        dates, warmup, confirmed_date  = c.fetchone()
    suggested_dates = parsedates(dates) # TODO register a converter
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT date FROM events WHERE warmup=?', (warmup,))
        occupied_dates = [d[0] for d in c.fetchall()]
    good_dates = set(suggested_dates) - set(occupied_dates)
    if confirmed_date:
        good_dates = good_dates.union(set([confirmed_date]))
    today = datetime.datetime.now() + datetime.timedelta(days=daysoffset)
    good_dates = sorted([d for d in good_dates if d>today])
    return good_dates, confirmed_date

def linkify(url):
    if url.startswith('https://'):
        return '<a href="%s">%s</a>'%(url,url[8:])
    else:
        return '<a href="https://%s">%s</a>'%(url,url)

@cherrypy.popargs('uuid')
class Invite:
    @cherrypy.expose
    def index(self, uuid):
        try:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT email, warmup, host, host_email, location FROM invitations WHERE uuid=?;', (uuid,))
                email, warmup, host, host_email, invite_location  = c.fetchone()
                if invite_location is None: # TODO this is ridiculous and it should not be needed
                    invite_location = ''
        except:
            log.error('Attempted opening unknown invite %s '%(uuid,))
            return templates.get_template('invite_blank.html').render(content='This invation is invalid! Please contact whomever sent you the invite!')
        good_dates, confirmed_date = available_dates(uuid, daysoffset=conf('invitations.neededdays'))
        args = 'speaker, affiliation, bio, title, abstract, recording_consent, conf_link, sched_link, location'
        if confirmed_date:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT %s FROM events WHERE date=? AND warmup=?'%args,
                          (confirmed_date, warmup))
                data = c.fetchone()
            args_s = args.split(', ')
            old_data = dict(zip(args_s, data))
            preevent_message = Invite.preevent_message(uuid,confirmed_date,warmup,old_data,host)
        else:
            old_data = dict()
            preevent_message = ''
        return templates.get_template('invite_index.html').render(dates=good_dates, confirmed_date=confirmed_date, email=email, uuid=uuid, warmup=warmup, old_data=old_data, host=host, host_email=host_email, invite_location=invite_location, preevent_message=preevent_message)

    @staticmethod
    def preevent_message(uuid,confirmed_date,warmup,data,host):
        preevent_message = conf('invitations.preevent_message').format(
            host=host,
            videoconf=linkify(data['conf_link']),
            private_details=linkify(conf('server.url')+'/invite/'+uuid),
            schedule=linkify(data['sched_link']) if data['sched_link'] else 'not yet available',
            public_details=linkify(conf('server.url')+'/event/%s/%s'%(confirmed_date,warmup)),
            warmup_talk='schedule not finalized', # TODO
            will_record='Thank you for permitting us to record the talk' if data['recording_consent'] else 'The talk will not be recorded, as you have instructed us'
            )
        return preevent_message

    @cherrypy.expose
    def do(self, **kwargs):
        uuid = kwargs['uuid']
        args = 'date, speaker, affiliation, bio, title, abstract, warmup, email, recording_consent, location'
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
                                **ZOOM_TEMPLATE()}
            try:
                url = Zoom.post('/users/me/meetings', data=zoom_meet_config).json()['join_url']
                data_dict['conf_link'] = url
                with conn() as c:
                    c = c.cursor()
                    c.execute('UPDATE events SET conf_link=? WHERE date=? AND warmup=?', (url, data_dict['date'], data_dict['warmup']))
            except:
                log.error('Could not create a Zoom room for %s %s'%(data_dict['date'], data_dict['warmup']))
                data_dict['conf_link'] = ''
            # Calendar
            Invite.makecalevent(data_dict)
        # Email
        text_content = subject = '%s, updates about your talk for %s!'%(data_dict['speaker'], data_dict['date'])
        url = 'https://'+conf('server.url')+'/invite/'+uuid
        public_url = 'https://'+conf('server.url')+'/event/'+str(data_dict['date'])+'/'+str(data_dict['warmup'])
        html_content = '<p>You can view updated information about your talk (videoconf link and private schedule) at <a href="%s">%s</a>. <strong>Keep this link private</strong>.<br>For the public announcement see <a href="%s">%s</a></p>'%(url, url, public_url, public_url) 
        send_email(text_content, html_content, data_dict['email'], subject, cc=[host_email] if host_email else [])

        return templates.get_template('invite_blank.html').render(content='Submission successful! '+html_content)

    @staticmethod
    def makecalevent(data_dict):
        title = data_dict["speaker"]+": "+data_dict["title"]
        date = data_dict["date"]
        description = conf('event.name')+'\n\n'
        description += data_dict["abstract"]+'\n\n'
        if data_dict["conf_link"]:
            description += 'Video conf at: '+data_dict["conf_link"]+'\n\n'
        if data_dict["location"]:
            description += 'In-person at: '+data_dict["location"]+'\n\n'
        try:
            creds = Google.getcreds()
            with build('calendar','v3',credentials=creds) as service:
                for calid in conf('google.calendarid'):
                    j = service.events().quickAdd(calendarId=calid,text=title).execute()
                    event_id = j["id"]
                    j["start"]["dateTime"] = date.isoformat('T')
                    j["end"]["dateTime"] = (date+datetime.timedelta(hours=1)).isoformat('T')
                    nj = {
                            "start": j["start"],
                            "end": j["end"],
                    "description": description}
                    j = service.events().patch(calendarId=calid,eventId=event_id,body=nj).execute()
        except:
            log.error('Could not create a calendar event for %s %s'%(title, date))



def add_default_time_to_date(date):
    if date.hour == 0 and date.minute == 0 and date.second == 0:
        hour = conf('timing.default_hour')
        minute = conf('timing.default_minute')
        d = datetime.datetime(date.year, date.month, date.day, hour, minute, 0)
    else:
        d=date
    return d


class Admin:
    access_levels = []

    @cherrypy.expose
    def index(self):
        return templates.get_template('admin_blank.html').render(content='From here you can configure the website, invite speakers, and judge applications for warmup talks.')

    @staticmethod
    def get_configrecords(access_levels=[]):
        configrecords = list(sqlite3.connect(os.path.join(file_dir,'config.sqlite')).execute('SELECT key, value, valuetype, help, access_level FROM config ORDER BY key'))
        configrecords = [r[:-1] for r in configrecords if r[-1] in [None]+access_levels]
        configrecords.sort(key = lambda _:_[0].split('.')[0])
        configrecords = itertools.groupby(configrecords, key = lambda _:_[0].split('.')[0])
        return configrecords

    @cherrypy.expose
    def config(self):
        return templates.get_template('admin_config.html').render(configrecords=self.get_configrecords(self.access_levels))

    @cherrypy.expose
    def update(self, *args, **kwargs):
        key = args[0]
        value = kwargs['value']
        config_access = list(sqlite3.connect(os.path.join(file_dir,'config.sqlite')).execute('SELECT access_level FROM config WHERE key==?', (key,)))[0]
        if config_access not in [None]+self.access_levels:
            with sqlite3.connect(os.path.join(file_dir,'config.sqlite')) as conn:
                conn.cursor().execute('UPDATE config SET value=? WHERE key=?', (value,key))
                conn.commit()
            raise cherrypy.HTTPRedirect("../config#panel-%s"%key)
        raise cherrypy.HTTPError(403)

    @cherrypy.expose
    def invite(self):
        today = datetime.datetime.now()
        with conn() as c:
            takendates = [d for (d,) in c.execute('SELECT date FROM events WHERE warmup=0 ORDER BY date ASC') if d>today]
        start_of_month = datetime.datetime(today.year, today.month, 1)
        removedates = [start_of_month]
        day = datetime.timedelta(days=1)
        for i in range(today.day+conf('invitations.neededdays')):
            removedates.append(removedates[-1]+day)
        takendates += removedates
        takendates = ','.join("'%s'"%d.strftime('%Y-%m-%d') for d in takendates)
        return templates.get_template('admin_invite.html').render(takendates=takendates,location=conf('event.defaultlocation'))

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
        location = kwargs.get('location')
        uid = str(uuid.uuid4())
        try:
            with conn() as c:
                c.execute('INSERT INTO invitations (uuid, email, dates, warmup, host, host_email, confirmed_date, location) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)',
                          (uid, email, '|'.join(repr(d) for d in dates), warmup, host, host_email, location))
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
            all_invites = list(c.execute('SELECT uuid, email, confirmed_date, dates FROM invitations'))[::-1]
        lim = datetime.datetime.now() + datetime.timedelta(days=conf('invitations.neededdays'))
        all_invites = [(uuid, email, confirmed_date, dates, 
                              'accepted for %s'%confirmed_date if confirmed_date
                              else 'not accepted yet' if any(d>lim for d in parsedates(dates)) else 'expired')
                for (uuid, email, confirmed_date, dates) in all_invites]
        return templates.get_template('admin_invitestatus.html').render(all_invites=all_invites)

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
    def modevent(self, date, warmup, action):
        try:
            with conn() as c:
                warmup = warmup and not (warmup=='False' or warmup=='0') # TODO this should not be such a mess to parse
                parseddate = dateutil.parser.isoparse(date)
                talk = c.execute('SELECT date, warmup, speaker, affiliation, title, abstract, bio, conf_link, recording_consent, recording_link, location FROM events WHERE date=? AND warmup=? ORDER BY date DESC', (parseddate, warmup)).fetchone()
                if not warmup:
                    has_warmup = c.execute('SELECT COUNT(*) FROM events WHERE warmup=? AND date=?', (True, parseddate)).fetchone()[0]
                has_warmup=not warmup and has_warmup
            future = talk[0]>datetime.datetime.now()
            # TODO this dictionary interface is used often... there should be a more official way to get a dictionary... if not make your own helper function
            args = 'date, warmup, speaker, affiliation, title, abstract, bio, conf_link, recording_consent, recording_link, location'.split(', ')
            data_dict = {k:v for (k,v) in zip(args,talk)}
            data_dict['has_warmup'] = has_warmup
        except:
            log.error('Attempted modifying unknown talk %s %s'%(date, warmup))
            return templates.get_template('admin_blank.html').render(content='Failed attempt, check logs!')
        if action == 'cal':
            Invite.makecalevent(data_dict)
        else:
            return templates.get_template('admin_blank.html').render(content='Unknown operation attempted!')
        return templates.get_template('admin_blank.html').render(content='Modification successful!')

    @cherrypy.expose
    def authzoom(self):
        Zoom.start_auth()

    @cherrypy.expose
    def testzoom(self, test=None):
        if test=='make_meeting':
            zoom_meet_config = {'start_time':str(datetime.datetime.now()),
                                'topic': 'Test Meeting '+conf('event.name'),
                                **ZOOM_TEMPLATE()}
            j = Zoom.post('/users/me/meetings', data=zoom_meet_config).json()
            zoom_meet_config = {'start_time':str(datetime.datetime.now()), **ZOOM_TEMPLATE()}
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


class SysAdmin(Admin):
    access_levels = ['sysadmin']


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

def sysauth(realm,u,p):
    log.info('attempting to access protected area %s'%((realm,u,p),))
    return p==conf('sysadmin.pass') and u==conf('sysadmin.user')

if __name__ == '__main__':
    log.info('server starting')
    cherrypy.config.update({'server.socket_host'     : '127.0.0.1',
                            'server.socket_port'     : conf('server.port'),
                            'tools.encode.on'        : True,
                            'environment'            : 'production',
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
                          'tools.auth_basic.realm': 'admin',
                          'tools.auth_basic.checkpassword': auth,
                         }}
    sys_password_conf = {'/':{
                              'tools.auth_basic.on': True,
                              'tools.auth_basic.realm': 'sysadmin',
                              'tools.auth_basic.checkpassword': sysauth,
                             }}
    cherrypy.tree.mount(Root(), '/', {**static_conf,**video_conf,**customfiles_conf})
    cherrypy.tree.mount(Invite(), '/invite', {})
    cherrypy.tree.mount(Apply(), '/apply', {})
    cherrypy.tree.mount(Admin(), '/admin', password_conf)
    cherrypy.tree.mount(SysAdmin(), '/sysadmin', sys_password_conf)
    cherrypy.tree.mount(Dev(), '/dev', sys_password_conf)
    cherrypy.tree.mount(Zoom(), '/zoom', {})
    cherrypy.tree.mount(Google(), '/google', {})
    for (f,t) in scheduled_events:
        Monitor(cherrypy.engine, f, frequency=t).subscribe()
    cherrypy.engine.start()
    cherrypy.engine.block()
    log.info('server stoped')
