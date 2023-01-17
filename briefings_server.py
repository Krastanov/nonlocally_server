import base64
import csv
import datetime
import email
import email.mime
import email.mime.base
import hashlib
import html
import itertools
import io
import json
import logging
import os.path
import random
import smtplib
import sqlite3
import socket
import sys
import tempfile
import threading
import time
import urllib
import urllib.parse
import uuid

import cherrypy
from cherrypy.process.plugins import Monitor
import jinja2
import dateutil
import dateutil.parser
import py_etherpad
import rauth
import requests


# TODO unify admin_judge, apply_index, and invite_index / unify the invitations and applications tables

file_dir = os.path.dirname(os.path.realpath(__file__))

if len(sys.argv)!=3:
    println("call as `python server.py SEMINAR_SERIES FOLDER_LOCATION`")
    raise Exception('You need to specify the seminar series and folder location')
SEMINAR_SERIES = sys.argv[1]
FOLDER_LOCATION = sys.argv[2]
DB_FILENAME = FOLDER_LOCATION+ '/%s_database.sqlite' % SEMINAR_SERIES
CONF_FILENAME = FOLDER_LOCATION+ '/%s_config.sqlite' % SEMINAR_SERIES
LOG_FILENAME = FOLDER_LOCATION+ '/%s.log' % SEMINAR_SERIES

if not os.path.exists(os.path.join(file_dir,DB_FILENAME)):
    raise Exception('Please run `create_db.sh` in order to create an empty sqlite database.')
if not os.path.exists(os.path.join(file_dir,CONF_FILENAME)):
    raise Exception('You need a configuration settings database file (maybe copy one of the already available and then edit it from the /admin page).')


logfile = os.path.join(file_dir,LOG_FILENAME)
logging.basicConfig(filename=logfile,format='%(asctime)s:%(name)s:%(levelname)s:%(message)s',level=logging.DEBUG)
log = logging.getLogger('briefings')


sqlite3.register_adapter(bool, int)
sqlite3.register_converter("BOOLEAN", lambda v: bool(int(v)))

def conn(d=False): # TODO make d=True default
    conn = sqlite3.connect(os.path.join(file_dir,DB_FILENAME), detect_types=sqlite3.PARSE_DECLTYPES)
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
    v, vtype = next(sqlite3.connect(os.path.join(file_dir,CONF_FILENAME)).execute('SELECT value, valuetype FROM config WHERE key=?',(k,)))
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
    conn = sqlite3.connect(os.path.join(file_dir,CONF_FILENAME))
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
templates.globals['TZ'] = conf('server.tzlong')


def send_email(text_content, html_content, emailaddr, subject, pngbytes_cids=[], file_atts=[], cc=[]):
    log.debug('attempting to send email "%s" <%s>'%(subject, emailaddr))
    try:
        msg = email.message.EmailMessage()
        msg.set_content(text_content)
        msg['Subject'] = subject
        msg['From'] = email.headerregistry.Address(conf('email.from_display'), conf('email.from_user'), conf('email.from'))
        msg['To'] = emailaddr
        msg['Bcc'] = ','.join(conf('email.cc')+cc+[conf('sysadmin.email')])

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
    except Exception as e:
        log.error('failed to send email "%s" <%s> due to %s'%(subject, emailaddr, e))


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

# Etherpad

etherpad = py_etherpad.EtherpadLiteClient(apiKey=conf("etherpad.apikey"),baseUrl=conf("etherpad.url")+'/api')

# Scheduled Events

def check_upcoming_talks_and_email():
    try:
        log.debug('Checking whether we need to send an email announcement for talks')
        for prev_announcements, days_in_advance_min, days_in_advance_max in [(1, 1, 2), (0, 1, 6)]:
            with conn(d=True) as c:
                upcoming_talks = c.execute(f"SELECT * FROM events WHERE announced<={prev_announcements} AND date>date('now','+{days_in_advance_min} day') AND date<date('now','+{days_in_advance_max} day')").fetchall()
                all_upcoming_talks = list(c.execute("SELECT * FROM events WHERE announced=0 AND date>date('now') AND date<date('now','+60 day')"))
            for r in upcoming_talks:
                event = conf('event.name')
                datestr = r['date'].strftime('%b %-d %-I:%M%p')
                subject = f"Upcoming talk {datestr} - {r['title']} by {r['speaker']}"
                priv_subject = f"Private Schedule - {r['title']} by {r['speaker']}"
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
                priv_signup_html = f"<div><h2>Private schedule</h2><a href=\"{r['sched_link']}\">{r['sched_link']}</a></div><div><strong>{conf('event.emailfooter')}</strong></div>"
                priv_signup_plain = f"\nPrivate meeting signup: {r['sched_link']}\n{conf('event.emailfooter')}"
                html = f"""
                <strong>{event} - {datestr}</strong>
                <h2>{r['title']}</h2>
                <h3>{r['speaker']} - {r['affiliation']}</h3>
                <div><p>Abstract: </p><p style=\"white-space:pre-wrap;\">{r['abstract']}</p></div>
                <div><p>Bio:</p><p style=\"white-space:pre-wrap;\">{r['bio']}</p></div><div></div>
                <div>
                <p><strong>Location and Video Conference link</strong>: <a href=\"{public_url}\">{public_url}</a></p>
                <p>Timezone: {conf('server.tzlong')}</p>
                </div>"""
                plain = f"{event} - {datestr}\n{r['title']}\n{r['speaker']} - {r['affiliation']}\n\nAbstract: {r['abstract']}\n\nBio: {r['bio']}\n\nLocation & Video Conference link: {public_url}\n\nTimezone: {conf('server.tzlong')}"
                speaker_email = r['email']
                host_email = r['host_email']
                mailing_list_email = conf("email.mailing_list")
                priv_mailing_list_email = conf("email.priv_mailing_list")
                send_email(plain+future_talks_plain, html+future_talks_html, mailing_list_email, subject, cc=[speaker_email, host_email])
                send_email(plain+priv_signup_plain+future_talks_plain, html+priv_signup_html+future_talks_html, priv_mailing_list_email, priv_subject, cc=[speaker_email, host_email])
                with conn() as c: # TODO do not send this if the email failed to send
                    c.execute(f'UPDATE events SET announced={prev_announcements+1} WHERE date=? AND warmup=?',
                              (r['date'],r['warmup']))
    except Exception as e:
        log.error('Failure in the email annoucements scheduled job due to %s'%e)

def check_recordings_and_download():
    try:
        log.debug('Checking whether we have talks to download recordings for')
        with conn(d=True) as c:
            recorded_talks = c.execute("SELECT * FROM events WHERE recording_processed=0 AND recording_consent=1 AND date<date('now','-1 day') ORDER BY date DESC").fetchall()
        for r in recorded_talks: # TODO this should be a function that can also be called from the admin panel
            event = conf('event.name')
            config = {"recording_authentication": False}
            if r['conf_link']:
                meetingid = r['conf_link'].split('/')[-1]
            else:
                log.error("the conf_link for %s is missing and we can not download anything"%r['date'])
            log.debug(f"looking up zoom recording for {r['date']} {r['warmup']}")
            Zoom.patch('/meetings/%s/recordings/settings'%meetingid, data=config)
            rec = Zoom.get('/meetings/%s/recordings'%meetingid).json()
            log.debug("zoom recording json: %s"%(json.dumps(rec,indent=4)))
            rec = [r for r in rec['recording_files'] if r['recording_type'].startswith('shared_screen')]
            rec.sort(key=lambda _:int(_['file_size']),reverse=True)
            rec = rec[0]
            url = rec['download_url']
            recording_folder = FOLDER_LOCATION+"/recordings/"+SEMINAR_SERIES # TODO this should be in config and the trailing / should be normalized conf("zoom.recdownloads")
            recording_name = str(r["date"]).replace(" ","_").replace(":","_") + '-' + str(int(r["warmup"]))
            hls_cmd = f"ffmpeg -i {recording_folder}/{recording_name}.mp4 -profile:v baseline -level 3.0 -start_number 0 -hls_time 10 -hls_list_size 0 -f hls {recording_folder}/hls/{recording_name}.m3u8"
            log.debug("started downloading %s into %s/%s"%(url,recording_folder,recording_name))
            os.system('wget "%s" -O "%s/%s.mp4"'%(url,recording_folder,recording_name)) # TODO raise error if wget is not installed
            log.debug("finished downloading and now converting %s"%recording_name)
            os.system(f'{hls_cmd} &') # TODO raise error if ffmpeg is not installed
            log.debug("converting is now running in the background %s"%recording_name)
            with conn() as c:
                c.execute('UPDATE events SET recording_processed=1 WHERE date=? AND warmup=?',
                          (r['date'],r['warmup']))
            break # TODO add some delay so we do not download multiple files at the same time and remove this break statement
    except Exception as e:
        log.error('Failure in downloading recording due to %s'%e)

scheduled_events = [
    (check_upcoming_talks_and_email, 3600*2),
    (check_recordings_and_download, 3600*2),
        ]

# CherryPy server

class Root:
    @cherrypy.expose
    def index(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link, location FROM events WHERE warmup=0 ORDER BY date ASC'))
        now = datetime.datetime.now() - datetime.timedelta(days=2)
        records = [t for t in all_talks if t[0]>now]
        return templates.get_template('__index.html').render(records=records, customfooter=conf('frontpage.footer'))

    @cherrypy.expose
    def iframeupcoming(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link, location FROM events WHERE warmup=0 ORDER BY date ASC'))
        now = datetime.datetime.now() - datetime.timedelta(days=2)
        records = [t for t in all_talks if t[0]>now]
        return templates.get_template('__iframeupcoming.html').render(records=records)

    @cherrypy.expose
    def past(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, recording_consent, recording_link, location, recording_processed FROM events WHERE warmup=0 ORDER BY date DESC'))
        now = datetime.datetime.now()
        records = [t for t in all_talks if t[0]<now]
        return templates.get_template('__past.html').render(records=records)

    @cherrypy.expose
    def event(self, date, warmup):
        try:
            with conn() as c:
                warmup = warmup and not (warmup=='False' or warmup=='0') # TODO this should not be such a mess to parse
                parseddate = dateutil.parser.isoparse(date)
                talk = c.execute('SELECT date, warmup, speaker, affiliation, title, abstract, bio, conf_link, recording_consent, recording_link, location, recording_processed FROM events WHERE date=? AND warmup=? ORDER BY date DESC', (parseddate, warmup)).fetchone()
                if not warmup:
                    has_warmup = c.execute('SELECT COUNT(*) FROM events WHERE warmup=? AND date=?', (True, parseddate)).fetchone()[0]
        except:
            log.error('Attempted opening unknown talk %s %s'%(date, warmup))
            return templates.get_template('__blank.html').render(content='There does not exist a talk given at that time in our database!')
        return templates.get_template('__event.html').render(talk=talk, has_warmup=not warmup and has_warmup)

    @cherrypy.expose
    def about(self):
        return templates.get_template('__about.html').render(seminar=conf('event.name'),description=conf('event.description'),longdescription=conf('event.longdescription'),aboutnonlocally='')


class Apply:
    @cherrypy.expose
    def index(self):
        slots = []#self.available_talks()
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
        text_content = html_content = 'You or someone on your behalf applied to give a warmup talk for our seminar series. The submission was successful. You will receive an email with a decision, depending on availability, before the talk.'
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
    if url:
        if url.startswith('https://'):
            return '<a href="%s">%s</a>'%(url,url[8:])
        else:
            return '<a href="https://%s">%s</a>'%(url,url)
    else:
        return "<span>no link available yet</span>"

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
        euuid = kwargs['uuid']
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
        good_dates, confirmed_date = available_dates(euuid)
        data_dict = dict(zip(args_s, data))
        if confirmed_date and confirmed_date < datetime.datetime.now():
            return templates.get_template('invite_blank.html').render(content='Can not edit past events!')
        if data[0] not in good_dates:
            return templates.get_template('invite_blank.html').render(content='There was a problem with reserving the date! Please contact whomever sent you the invite!')
        with conn() as c:
            c = c.cursor()
            args += ', host, host_email'
            placeholders += ',?,?'
            c.execute('SELECT email, warmup, host, host_email FROM invitations WHERE uuid=?;', (euuid,))
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
                      (data[0],euuid))

        # Zoom and Calendar and Schedule
        if not confirmed_date:
            # Zoom
            Invite.makezoom(data_dict)
            # Calendar
            Invite.makecalevent(data_dict)
            # Sched
            Invite.makesched(data_dict)
        # Email
        text_content = subject = '%s, schedule and updates for your talk on %s!'%(data_dict['speaker'], data_dict['date'])
        url = 'https://'+conf('server.url')+'/invite/'+euuid
        public_url = 'https://'+conf('server.url')+'/event/'+str(data_dict['date'])+'/'+str(data_dict['warmup'])
        html_content = '<p>Your schedule and a videoconf link are now available at <a href="%s">%s</a>. <strong>Keep this link private</strong>.<br>For the public announcement see <a href="%s">%s</a></p>'%(url, url, public_url, public_url) 
        send_email(text_content, html_content, data_dict['email'], subject, cc=[host_email] if host_email else [])
        return templates.get_template('invite_blank.html').render(content='Submission successful! '+html_content)

    @staticmethod
    def makezoom(data_dict):
        zoom_meet_config = {'start_time': data_dict['date'].isoformat('T'),
                            'topic': conf('event.name')+": "+data_dict['speaker'],
                            **ZOOM_TEMPLATE()}
        try:
            log.info(f"Attempting the creation of a zoom room for {data_dict['date']}")
            response = Zoom.post('/users/me/meetings', data=zoom_meet_config).json()
            log.info(f"Zoom responds with {response}")
            url = response['join_url']
            data_dict['conf_link'] = url
            with conn() as c:
                c = c.cursor()
                c.execute('UPDATE events SET conf_link=? WHERE date=? AND warmup=?', (url, data_dict['date'], data_dict['warmup']))
        except Exception as e:
            log.error(f'Could not create a Zoom room for {data_dict["date"]} {data_dict["warmup"]} due to {e}')
            data_dict['conf_link'] = ''

    @staticmethod
    def makecalevent(data_dict):
        return

    @staticmethod        
    def makesched(data_dict):
        try:
            prefix = data_dict['date'].strftime('%Y%m%d')
            padid = prefix+str(uuid.uuid4()).replace('-','')
            #etherpad.copyPad(conf('etherpad.scheduletemplate'), padid)
            etherpad.createPad(padid)
            templatehtml = etherpad.getHtml(conf('etherpad.scheduletemplate'))['html']
            public_url = 'https://'+conf('server.url')+'/event/'+urllib.parse.quote(str(data_dict['date']))+'/'+str(data_dict['warmup'])
            schedhtml = templatehtml.format(
                    details_url=public_url,
                    speaker=html.escape(data_dict['speaker']),
                    affiliation=html.escape(data_dict['affiliation']),
                    date=html.escape(str(data_dict['date']))+f"  {conf('server.tzlong')}",
                    )
            sched_url = conf('etherpad.url')+'/p/'+padid 
            etherpad.setHtml(padid, schedhtml)
        except Exception as e:
            log.error('Etherpad problems for %s %s: %s'%(data_dict['date'], data_dict['warmup'], e))
            sched_url = None
        with conn() as c:
            c.execute('UPDATE events SET sched_link=? WHERE date=? AND warmup=?', (sched_url, data_dict['date'], data_dict['warmup']))



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
        return templates.get_template('admin_sop.html').render()

    @staticmethod
    def get_configrecords(access_levels=[]):
        configrecords = list(sqlite3.connect(os.path.join(file_dir,CONF_FILENAME)).execute('SELECT key, value, valuetype, help, access_level FROM config ORDER BY key'))
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
        config_access = list(sqlite3.connect(os.path.join(file_dir,CONF_FILENAME)).execute('SELECT access_level FROM config WHERE key==?', (key,)))[0]
        if config_access not in [None]+self.access_levels:
            with sqlite3.connect(os.path.join(file_dir,CONF_FILENAME)) as conn:
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
        except Exception as e:
            log.error('Could not insert %s due to %s'%((uid, email, '|'.join(repr(d) for d in dates), warmup, host, host_email),e))
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
    def eventstatus(self):
        with conn() as c:
            all_events = list(c.execute("""
            SELECT
                date, speaker,
                events.email, events.host, events.location,
                announced, recording_consent, recording_processed,
                invitations.uuid
            FROM events
            LEFT JOIN invitations
            ON
                invitations.confirmed_date = events.date
                AND invitations.warmup = events.warmup
            WHERE events.warmup=0
            ORDER BY date DESC
                """))
        return templates.get_template('admin_eventstatus.html').render(all_events=all_events)

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
        if action == 'zoom':
            Invite.makezoom(data_dict)
        elif action == 'sched':
            Invite.makesched(data_dict)
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


def auth(realm,u,p):
    log.info('attempting to access protected area %s'%((realm,u,p),))
    return p==conf('admin.pass') and u==conf('admin.user')

def sysauth(realm,u,p):
    log.info('attempting to access protected area %s'%((realm,u,p),))
    return p==conf('sysadmin.pass') and u==conf('sysadmin.user')

def allauth(realm,u,p):
    log.info('attempting to access protected area %s'%((realm,u,p),))
    return p==conf('server.allpass') and u==conf('server.alluser')

if __name__ == '__main__':
    log.info('server starting')
    cherrypy.config.update({'server.socket_host'     : '0.0.0.0',
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
                              'tools.auth_basic.on': False
                             }}
    customfiles_conf = {'/customfiles':{# Almost certainly this should be overwritten by your reverse proxy config.
                              'tools.staticdir.on'   : True,
                              'tools.staticdir.dir'  : '',
                              'tools.staticdir.root' : os.path.join(os.path.dirname(os.path.realpath(__file__)),FOLDER_LOCATION+'/customfiles'),
                              'tools.auth_basic.on': False
                             }}
    video_conf = {'/video':{# Almost certainly this should be overwritten by your reverse proxy config.
                              'tools.staticdir.on'   : True,
                              'tools.staticdir.dir'  : '',
                              'tools.staticdir.root' : conf('zoom.recdownloads')+'/'+SEMINAR_SERIES,
                              'tools.auth_basic.on': False
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
    root_conf = {**static_conf,**video_conf,**customfiles_conf}
    if conf('server.alluser'):
        root_conf = {**root_conf,
                     '/':{
                          'tools.auth_basic.on': True,
                          'tools.auth_basic.realm': 'all',
                          'tools.auth_basic.checkpassword': allauth,
                         }}
    cherrypy.tree.mount(Root(), '/', root_conf)
    cherrypy.tree.mount(Invite(), '/invite', {})
    cherrypy.tree.mount(Apply(), '/apply', {})
    cherrypy.tree.mount(Admin(), '/admin', password_conf)
    cherrypy.tree.mount(SysAdmin(), '/sysadmin', sys_password_conf)
    cherrypy.tree.mount(Dev(), '/dev', sys_password_conf)
    cherrypy.tree.mount(Zoom(), '/zoom', {})
    for (f,t) in scheduled_events:
        threading.Thread(target=f).start() # run it once at the start
        Monitor(cherrypy.engine, f, frequency=t).subscribe() # schedule future runs
    cherrypy.engine.start()
    cherrypy.engine.block()
    log.info('server stoped')
