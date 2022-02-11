#!/bin/sh
sqlite3 database.sqlite << SQL
PRAGMA foreign_keys = ON;

CREATE TABLE events
(date TIMESTAMP,
 speaker TEXT NOT NULL,
 affiliation TEXT NOT NULL,
 bio TEXT NOT NULL,
 title TEXT NOT NULL,
 abstract TEXT NOT NULL,
 warmup BOOLEAN NOT NULL,
 email TEXT NOT NULL,
 conf_link TEXT,
 sched_link TEXT,
 recording_consent BOOLEAN NOT NULL,
 recording_link TEXT,
 previous_records TEXT,
 host TEXT,
 host_email TEXT,
 location TEXT,
 PRIMARY KEY (date, warmup)
);

CREATE TABLE invitations
(uuid TEXT PRIMARY KEY,
 email NOT NULL,
 dates TEXT NOT NULL, -- TODO implement an array converter in python
 warmup BOOLEAN NOT NULL,
 confirmed_date TIMESTAMP,
 host TEXT,
 host_email TEXT,
 location TEXT,
 FOREIGN KEY(confirmed_date, warmup) REFERENCES events(date, warmup)
 -- TODO maybe CHECK that dates has the correct format
);

CREATE TABLE applications
(uuid TEXT PRIMARY KEY,
 speaker TEXT NOT NULL,
 affiliation TEXT NOT NULL,
 bio TEXT NOT NULL,
 title TEXT NOT NULL,
 abstract TEXT NOT NULL,
 warmup BOOLEAN NOT NULL,
 email TEXT,
 dates TEXT NOT NULL, -- TODO implement an array converter in python
 previous_records TEXT,
 confirmed_date TIMESTAMP,
 declined BOOLEAN DEFAULT 0, -- TODO CHECK that declined and confirmed_date are not on at the same time
 FOREIGN KEY(confirmed_date, warmup) REFERENCES events(date, warmup)
 -- TODO maybe CHECK that dates has the correct format and refers to events that exist
)
SQL
