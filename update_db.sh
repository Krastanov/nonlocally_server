#!/bin/sh
filename=$1
cp $filename $filename.bac
sh ./create_db.sh
mv database.sqlite $filename

sqlite3 $filename.bac << SQL
PRAGMA foreign_keys = ON;
ATTACH DATABASE '$filename' AS other;

INSERT INTO other.events SELECT
date,
speaker,
affiliation,
bio,
title,
abstract,
warmup,
email,
conf_link,
sched_link,
recording_consent,
recording_link,
previous_records,
host,
host_email,
location,
announced,
recording_processed
FROM main.events;

INSERT INTO other.invitations SELECT
uuid,
email,
dates,
warmup,
confirmed_date,
host,
host_email,
location
FROM main.invitations;

INSERT INTO other.applications SELECT
uuid,
speaker,
affiliation,
bio,
title,
abstract,
warmup,
email,
dates,
previous_records,
confirmed_date,
declined
FROM main.applications;
SQL
