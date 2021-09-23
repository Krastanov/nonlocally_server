"""Download all zoom videos of given meeting ids."""

import datetime
import itertools
import json
import os
import os.path
import sys
import sqlite3

import dateutil
import dateutil.parser
import rauth
import requests

file_dir = os.path.dirname(os.path.realpath(__file__))


sqlite3.register_adapter(bool, int)
sqlite3.register_converter("BOOLEAN", lambda v: bool(int(v)))

if not os.path.exists(os.path.join(file_dir,'database.sqlite')):
    raise Exception('Please run `create_db.sh` in order to create an empty sqlite database.')
def conn():
    conn = sqlite3.connect(os.path.join(file_dir,'database.sqlite'), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = 1")
    return conn

_, dl_folder, *all_meetings = sys.argv

from briefings_server import Zoom

s = Zoom.get_session()
for m in all_meetings:
    print(m)
    config = {"recording_authentication": False}
    Zoom.patch('/meetings/%s/recordings/settings'%m, data=config)
    rec = Zoom.get('/meetings/%s/recordings'%m).json()
    print(json.dumps(rec,indent=4))
    try:
        rec = [r for r in rec['recording_files'] if r['recording_type']=='shared_screen_with_speaker_view']
        for r in rec:
            url = r['download_url']
            time = r['recording_start']
            print('Downloading ', time)
            os.system('wget "%s" -O "%s/%s"'%(url,dl_folder,time))
    except:
        print('No download')
        pass



