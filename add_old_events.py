"""Add events to database from a csv file."""

import csv
import datetime
import itertools
import os.path
import sys
import sqlite3

import dateutil
import dateutil.parser

file_dir = os.path.dirname(os.path.realpath(__file__))


sqlite3.register_adapter(bool, int)
sqlite3.register_converter("BOOLEAN", lambda v: bool(int(v)))

if not os.path.exists(os.path.join(file_dir,'database.sqlite')):
    raise Exception('Please run `create_db.sh` in order to create an empty sqlite database.')
def conn():
    conn = sqlite3.connect(os.path.join(file_dir,'database.sqlite'), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = 1")
    return conn

db_columns_str = 'date, speaker, affiliation, bio, title, abstract, recording_link, email, warmup, recording_consent'
db_columns = [c.strip() for c in db_columns_str.split(',')]
print('DB columns supported:', db_columns)

_, filename, dateformat, *csv_columns = sys.argv

print('File: ', filename)
print('Date format: ', dateformat) # %m/%d/%Y is the stupid American format
print('CSV columns: ', csv_columns)

saved_column_names = []
saved_column_indices = []
for name in db_columns:
    try:
        index = csv_columns.index(name)
        saved_column_names.append(name)
        saved_column_indices.append(index)
    except:
        pass

print('Saved columns: ', list(zip(saved_column_names, saved_column_indices)))

# XXX warmup, email, and others can be skipped
default_key = []
default_val = []
if 'email' not in saved_column_names:
    default_key.append('email')
    default_val.append('""')
if 'warmup' not in saved_column_names:
    default_key.append('warmup')
    default_val.append('0')
if 'recording_consent' not in saved_column_names:
    default_key.append('recording_consent')
    default_val.append('0')
if default_key:
    default_key = ','+','.join(default_key)
    default_val = ','+','.join(default_val)
else:
    default_key = ''
    default_val = ''

records = []
with open(filename) as csv_file:
    csv_reader = csv.reader(csv_file, delimiter=',')
    line_count = 0
    for row in csv_reader:
        record = []
        for i in saved_column_indices:
            v = row[i].strip()
            if saved_column_names[i] == 'date':
                v = datetime.datetime.strptime(v, dateformat)
            record.append(v)
        if all(record):
            records.append(record)
        line_count += 1

print(f'Processed {line_count} lines, containing {len(records)} good records.')

namestr = ','.join(saved_column_names)
placeholderstr = ','.join(['?']*len(saved_column_names))
sqlstr = 'INSERT INTO events (%s %s) VALUES (%s %s)'%(namestr,default_key,placeholderstr,default_val)
print('Executing ', sqlstr)
with conn() as c:
    c = c.cursor()
    c.executemany(sqlstr, records)

print('Done')
