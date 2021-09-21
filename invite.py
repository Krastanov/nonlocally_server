import os
import sqlite3
import time

import smtplib
import email
import io
import socket

email_plaincontent = '''
Greetings from the Yale Society of Women Engineers,

We are running our second annual CT SEED (Students Exploring Engineering Day)
outreach event on March 23rd. We invite all middle school students to join us
in a day of scientific exploration and engineering challenges. For more information,
including a tentative schedule along with activities, and to sign up see
https://www.engineeringday.com/ctseed/?s={tracking}.

We also invite parents and educators for a program exploring resources available
for sparking kids' interest in science and engineering.

Feel free to forward to other students, educators, or parents that might be interested
in the program.
'''

email_html = '''<!DOCTYPE html>
<html>
<head></head>
<body>
<img src="cid:{cidlogo}" style="float:left;width:150pt;margin:10pt;">

Greetings from the Yale Society of Women Engineers,

<p>
We are running our second annual CT SEED (Students Exploring Engineering Day)
outreach event on March 23rd. We invite all middle school students to join us
in a day of scientific exploration and engineering challenges. For more information,
including a tentative schedule along with activities, and to sign up see
<a href="https://www.engineeringday.com/ctseed/?s={tracking}">
engineeringday.com/ctseed</a>.
</p>

<p>
We also invite parents and educators for a program exploring resources available
for sparking kids' interest in science and engineering.
</p>

<p>
Feel free to forward to other students, educators, or parents that might be interested
in the program.
</p>
</body>
</html>
'''

with open('SWElogo.png', 'rb') as f:
    swelogodata = f.read()

def send_email(tracking, address):
    msg = email.message.EmailMessage()
    msg.set_content(email_plaincontent.format(
                     tracking=tracking,
        ))
    msg['Subject'] = "Invitation to sign up for Yale SWE's middle school STEM outreach day"
    msg['From'] = email.headerregistry.Address('Yale SWE', 'engday', 'help@engineeringday.com')
    msg['To'] = address

    logocid = email.utils.make_msgid()
    msg.add_alternative(email_html.format(
                    tracking=tracking,
                    cidlogo=logocid[1:-1],
                    ),
            subtype='html'
            )
    msg.get_payload()[1].add_related(swelogodata, 'image', 'png', cid=logocid)
    
    username = 'theengineeringday@gmail.com'
    password = 'pphnlurkbfhlqwmw'
    server = smtplib.SMTP(socket.gethostbyname('smtp.gmail.com')+':587') # XXX workaround for IPv6 bugs with Digital Ocean
    server.ehlo()
    server.starttls()
    server.login(username,password)
    server.send_message(msg)
    server.quit()

import time
import csv
import sys

permitted_categories = sys.argv[1:]
print(permitted_categories)

if __name__ == '__main__':
    with open('./email_list.csv') as csvfile:
        emails = csv.reader(csvfile)
        for address, category, trackingid in emails:
            if category not in permitted_categories:
                continue
            try:
                print('sending',address,category,trackingid,flush=True)
                send_email(str(trackingid), address)
            except Exception as e:
                print(e)
            time.sleep(5)
