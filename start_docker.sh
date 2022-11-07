#!/bin/bash

# This script expects that:
# 1. The working directory is the git cloned server directory.
# 2. database.sqlite, config.sqlite, and the etherpad directory are in the parent directory, just outside the git cloned server directory
# (thus recreating the enviornment that is on the production server)
# 3. It may need to be run with sudo for docker to work.
# e.g. "ls .." should show "config.sqlite database.sqlite etherpad server"

docker build -t nonlocally .
echo ""
echo ""
echo "Now running the container. Use ctrl+c to stop it."
echo "The website should be visible at localhost:12347"
docker run -it --network host -v $(pwd)/../:/workdir nonlocally python3 /workdir/server/briefings_server.py oqe


# This script will launch an interactive docker shell running the website
# The website will then be accessible at localhost:12347


# A couple notes:
# - this is very much a work in progress and not everything may work
# - The -v part of the script mounts the parent directory of the working directory into the docker container. This means any changes the briefings_server.py makes will be reflected locally, outside of the docker container, (in particular, changes to the sqlite databases).
