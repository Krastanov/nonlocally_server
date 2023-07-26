###############################################################################################
# INSTRUCTIONS:                                                                               #
# 1. cd to the root directory of the git repo (.../nonlocally_server/) if you haven't already #
# 2. docker build -t nonlocally .                                                             #
# 3. docker run --net host -it -v $(pwd):/workdir nonlocally                                  #
###############################################################################################

#start with debian:10
FROM debian

# make a directory to work from
RUN mkdir /workdir
WORKDIR /workdir

# install packages from apt that we will need
# currently its just 'python3', 'python3-pip', 'sqlite3', and 'ffmpeg'
# if more programs are needed they should be added here
RUN apt update
RUN DEBIAN_FRONTEND=noninteractive apt install -y --no-install-recommends python3 python3-venv sqlite3 wget && apt clean
RUN DEBIAN_FRONTEND=noninteractive apt install -y ffmpeg libx264-dev libx265-dev && apt clean

RUN rm -rf /var/lib/apt/lists/*

# copy all files, including the requirements.txt file into the container and install the python packages listed on it via pip
# 'pip install --upgrade pip' and 'pip install wheel' just ensure that the later installs run smoothly
RUN python3 -m venv /venv
RUN /venv/bin/pip3 install --upgrade pip
RUN /venv/bin/pip3 install wheel setuptools
COPY requirements.txt /workdir/requirements.txt
RUN /venv/bin/pip3 install -r /workdir/requirements.txt

ENTRYPOINT ["/venv/bin/python3", "briefings_server.py", "oqe", "/workdir/example-container-compose/nonlocally/oqe/var"]
