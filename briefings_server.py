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

templates = jinja2.Environment(loader=jinja2.FileSystemLoader(searchpath=os.path.join(file_dir,'templates/')))
templates.globals['EVENT_NAME'] = conf('event.name')

def send_email(text_content, html_content, emailaddr, subject, pngbytes_cids=[], file_atts=[]):
    log.debug('attempting to send email "%s" <%s>'%(subject, emailaddr))
    msg = email.message.EmailMessage()
    msg.set_content(text_content)
    msg['Subject'] = subject
    msg['From'] = email.headerregistry.Address(conf('email.from_display'), conf('email.from_user'), conf('email.from'))
    msg['To'] = emailaddr
    msg['Cc'] = conf('email.cc')

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

class Root:
    @cherrypy.expose
    def index(self):
        with conn() as c:
            all_talks = list(c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link FROM events WHERE warmup=0 ORDER BY date ASC'))
        records = [t for t in all_talks if t[0]>datetime.datetime.now()]
        return templates.get_template('__index.html').render(records=records)

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
                talk = c.execute('SELECT date, speaker, affiliation, title, abstract, bio, conf_link, recording_consent, recording_link FROM events WHERE date=? AND warmup=? ORDER BY date DESC', (dateutil.parser.isoparse(date), warmup)).fetchone()
            future = talk[0]>datetime.datetime.now()
        except:
            return templates.get_template('__blank.html').render(content='There does not exist a talk given at that time in our database!')
        return templates.get_template('__event.html').render(talk=talk, future=future)


class Apply:
    @cherrypy.expose
    def index(self):
        slots = self.available_talks()
        return templates.get_template('apply_index.html').render(slots=slots)

    @staticmethod
    def available_talks():
        with conn() as c:
            c = c.cursor()
            maintalks = list(c.execute('SELECT date, speaker, title FROM events WHERE warmup=0'))
            warmuptalks = set(d[0] for d in c.execute('SELECT date FROM events WHERE warmup!=0'))
        main_talks_dict = {d: (s,t) for d,s,t in maintalks}
        good_dates = set(main_talks_dict.keys()) - warmuptalks
        good_talks = [(d,*main_talks_dict[d]) for d in good_dates]
        return good_talks

    @cherrypy.expose
    def do(self, **kwargs):
        uid = str(uuid.uuid4())
        args = 'speaker, affiliation, bio, title, abstract, email'
        args_s = args.split(', ')
        data = []
        for a in args_s:
            v = kwargs.get(a)
            data.append(v)
        dates = [dateutil.parser.isoparse(v) for k,v in kwargs.items()
                 if k.startswith('date')]
        dates = '|'.join(str(d) for d in dates) # TODO register a converter
        args = args + ', warmup, uuid, dates'
        args_s.extend(['warmup', 'uuid', 'dates'])
        data.extend([True, uid, dates])
        placeholders = ("?,"*len(args_s))[:-1]
        good_talks = self.available_talks()
        if set(dates) > set([g for g,s,t in good_talks]):
            return templates.get_template('apply_blank.html').render(content='There was a problem with parsing the dates! Contact the administrator if the problem persists!')
        with conn() as c:
            c = c.cursor()
            c.execute('INSERT INTO applications (%s) VALUES (%s)'%(args, placeholders),
                      data)
        return templates.get_template('apply_blank.html').render(content='Submission successful! You will receive an email with a decision, depending on availability, before the talk.')



@cherrypy.popargs('uuid')
class Invite:
    @cherrypy.expose
    def index(self, uuid):
        try:
            with conn() as c:
                c = c.cursor()
                c.execute('SELECT email, warmup FROM invitations WHERE uuid=?;', (uuid,))
                email, warmup  = c.fetchone()
        except:
            return templates.get_template('invite_blank.html').render(content='This invation is invalid! Please contact whomever sent you the invite!')
        good_dates, confirmed_date = self.available_dates(uuid)
        args = 'speaker, affiliation, bio, title, abstract, recording_consent'
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
        return templates.get_template('invite_index.html').render(dates=good_dates, confirmed_date=confirmed_date, email=email, uuid=uuid, warmup=warmup, old_data=old_data)

    @staticmethod
    def available_dates(uuid):
        with conn() as c:
            c = c.cursor()
            c.execute('SELECT dates, warmup, confirmed_date FROM invitations WHERE uuid=?', (uuid,))
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
        good_dates, confirmed_date = self.available_dates(uuid)
        data_dict = dict(zip(args_s, data))
        if confirmed_date and confirmed_date < datetime.datetime.now():
            return templates.get_template('invite_blank.html').render(content='Can not edit past events!')
        if data[0] not in good_dates:
            return templates.get_template('invite_blank.html').render(content='There was a problem with reserving the date! Please contact whomever sent you the invite!')
        with conn() as c:
            c = c.cursor()
            c.execute("""INSERT INTO events (%s) VALUES (%s)
                         ON CONFLICT(date, warmup)
                         DO UPDATE SET %s"""%(
                             args, placeholders,
                             ', '.join('%s=excluded.%s'%(a,a) for a in args_s)
                         ),
                      data)
            c.execute('UPDATE invitations SET confirmed_date=? WHERE uuid=?',
                      (data[0],uuid))
        # Email
        text_content = subject = '%s, you submitted your talk for %s!'%(data_dict['speaker'], data_dict['date'])
        url = 'https://'+conf('server.url')+'/invite/'+uuid
        html_content = 'You can view updated information about your talk at <a href="%s">%s</a>. <strong>Keep this link private</strong>.'%(url, url) 
        send_email(text_content, html_content, data_dict['email'], subject)
        return templates.get_template('invite_blank.html').render(content='Submission successful! You can edit the talk details until the date of the talk at <a href="/invite/%s">the same link</a>. <strong>Keep this link private.</strong>'%uuid)


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
        except ValueError:
            return templates.get_template('admin_blank.html').render(content='There was a problem with the parsing of the dates! Try again!')
        warmup = 'warmup' in kwargs
        send = 'send' in kwargs
        uid = str(uuid.uuid4())
        try:
            with conn() as c:
                c.execute('INSERT INTO invitations (uuid, email, dates, warmup, confirmed_date) VALUES (?, ?, ?, ?, NULL)',
                          (uid, email, '|'.join(repr(d) for d in dates), warmup))
        except ValueError:
            return templates.get_template('admin_blank.html').render(content='There was a problem with the database! Try again!')
        # Email
        text_content = subject = conf('invitations.email_subject_line')
        invite_link = 'https://'+conf('server.url')+'/invite/'+uid
        invite_link = '<a href="%s">%s</a>'%(invite_link, invite_link)
        dates = '<ul>%s</ul>'%''.join('<li>%s</li>'%d for d in dates)
        html_content = conf('invitations.email_message').format(dates=dates, invite_link=invite_link)
        html_panel = '<div class="panel panel-default"><div class="panel-body">%s</div></div>'%html_content
        email_link = '<a href="mailto:%s">%s</a>'%(email,email)
        if kwargs.get('send'):
            try:
                send_email(text_content, html_content, email, subject)
                mail_note = 'The following email was sent to %s:'%email_link
            except:
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
    def judgle(self, uuid):
        return templates.get_template('admin_blank.html').render(content='Judging applications is not implemented yet!')


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


def auth(realm,u,p):
    log.info('attempting to access protected area %s'%((realm,u,p),))
    return p==conf('admin.pass') and u==conf('admin.user')


if __name__ == '__main__':
    log.info('server starting')
    cherrypy.config.update({'server.socket_host'     : '127.0.0.1',
                            'server.socket_port'     : 12347,
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
    password_conf = {'/':{
                          'tools.auth_basic.on': True,
                          'tools.auth_basic.realm': 'engday',
                          'tools.auth_basic.checkpassword': auth,
                         }}
    cherrypy.tree.mount(Root(), '/', static_conf)
    cherrypy.tree.mount(Invite(), '/invite', {})
    cherrypy.tree.mount(Apply(), '/apply', {})
    cherrypy.tree.mount(Admin(), '/admin', password_conf)
    cherrypy.tree.mount(Dev(), '/dev', password_conf)
    cherrypy.engine.start()
    cherrypy.engine.block()
    log.info('server stopping')
