import os
import sqlite3
import time

import smtplib
import email
import io
import socket

file_dir = os.path.dirname(os.path.realpath(__file__))
conn = lambda : sqlite3.connect('database.sqlite')
conf = lambda _ : eval(next(sqlite3.connect(os.path.join(file_dir,'config.sqlite')).execute('SELECT value FROM config WHERE key=?',(_,)))[0])

email_plaincontent = '''
The location of the event had to be changed due to space constraints.
Check-in will happen at Dunham Lab, 10 Hillhouse Ave.

We will need to process a large number of attendees at check-in.
Please arrive promptly at 8:50am on {event_date_string}.

Please confirm whether or not you can attend {event_name} on
{event_date_string}. After the event, both the students and participants in the
Parent and Educator Program will be able to attend a free guided tour of Yale's
campus.

Please confirm your attendance by clicking the link below (do not
reply to this email)! There are important questions for those attending the
Parent and Educator Program as well!

{message}

If you are attending, bring your ticket (printed or digital)!

Printed and signed liability and media release forms **have** to be provided
for each attending middle school student (they were included in the ticket
email sent upon registration). Blank forms will be available at check-in.

For more information, including a parking map, see
www.engineeringday.com/{event_slug}.
'''

email_html = '''<!DOCTYPE html>
<html>
<head></head>
<body>
<img src="cid:{cidlogo}" style="float:left;width:150pt;margin:10pt;">
<p>The location of the event had to be changed due to space constraints.
<strong>Check-in will happen at Dunham Lab, 10 Hillhouse Ave.</strong></p>

<p>We will need to process a large number of attendees at check-in.
<strong>Please arrive promptly at 8:50am on {event_date_string}.</strong></p>

<p><strong>
<u>Please confirm whether or not you can attend {event_name} on {event_date_string}</u>.
After the event, both the students and participants in the Parent and Educator
Program will be able to attend a free guided tour of Yale's campus.
</strong></p>
<p>
Please confirm your attendance by clicking the link below (do not reply to this email)! There are important questions for those attending the Parent and Educator Program as well!
</p>
{message_html}
<p>
If you are attending, bring your ticket (printed or digital)!
</p>
<p>
<strong>Printed and signed liability and media release forms have to be provided for each attending middle school student (they were included in the ticket email sent upon registration).</strong>
</p>
<p>
For more information, including a parking map, see <a href='https://www.engineeringday.com/{event_slug}'>www.engineeringday.com/{event_slug}</a>.
</p>
</body>
</html>
'''

with open('SWElogo.png', 'rb') as f:
    swelogodata = f.read()

def send_email(token):
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT email, pepemail, nbchildren FROM transactions WHERE token=?', (token,))
        stripeemail, pepmail, nbchildren = c.fetchone()
        emailaddr = stripeemail
        if nbchildren==0:
            emailaddr=pepmail
            message='Go to this link if you are attending: %s\nGo to this link if you are NOT attending: %s\n'
            message_html='<p><a href="%s">Click here if you are attending!</a></p><p><a href="%s">Click here if you are <strong>NOT attending</strong>!</a></p>'
        elif pepmail:
            message='Go to this link if you and your middle school students are attending: %s\nGo to this link if NEITHER you NOR the students are attending: %s\n'
            message_html='<p><a href="%s">Click here if you and your middle school students are attending!</a></p><p><a href="%s">Click here if <strong>NEITHER you NOR the students</strong> are attending!</a></p>'
        else:
            message='Go to this link if some or all of your middle school students are attending: %s\nGo to this link if NONE of the students are attending: %s\n'
            message_html='<p><a href="%s">Click here if some or all of your middle school students are attending!</a></p><p><a href="%s">Click here if <strong>NONE of the students</strong> are attending!</a></p>'
    badhref = 'https://seed.engineeringday.com/signup/emailconfirmation/%s/no'%token
    goodhref = 'https://seed.engineeringday.com/signup/emailconfirmation/%s/yes'%token
    
    msg = email.message.EmailMessage()
    msg.set_content(email_plaincontent.format(
                     message=message%(goodhref,badhref),
                     event_name=conf('event.name'),
                     event_date_string=conf('event.date_string'),
                     event_slug=conf('event.slug'),
        ))
    msg['Subject'] = '[%s] **location change** - please confirm'%conf('event.name')
    msg['From'] = email.headerregistry.Address('Yale SWE', 'engday', 'help@engineeringday.com')
    msg['To'] = emailaddr

    logocid = email.utils.make_msgid()
    msg.add_alternative(email_html.format(
                    event_name=conf('event.name'),
                    event_date_string=conf('event.date_string'),
                    event_slug=conf('event.slug'),
                    message_html=message_html%(goodhref,badhref),
                    cidlogo=logocid[1:-1],
                    ),
            subtype='html'
            )
    msg.get_payload()[1].add_related(swelogodata, 'image', 'png', cid=logocid)
    
    username = conf('email.SMTPuser')
    password = conf('email.SMTPpass')
    server = smtplib.SMTP(socket.gethostbyname(conf('email.SMTPhost'))+':'+conf('email.SMTPport')) # XXX workaround for IPv6 bugs with Digital Ocean
    server.ehlo()
    server.starttls()
    server.login(username,password)
    server.send_message(msg)
    server.quit()

import time
if __name__ == '__main__':
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT token FROM transactions')
        tokens = [_[0] for _ in c.fetchall()]
    with conn() as c:
        c = c.cursor()
        c.execute('SELECT token FROM confirmations')
        conf_tokens = [_[0] for _ in c.fetchall()]
    tokens = set(tokens) - set(conf_tokens)
    for i, token in enumerate(tokens):
        print('%d of %d '%(i+1,len(tokens)), token, flush=True)
        time.sleep(1)
        try:
            send_email(token)
        except Exception as e:
            print(e)
