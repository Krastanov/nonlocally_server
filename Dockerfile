#start with debian:10 (what the nonlocally server runs)
FROM debian:10

# make a directory to work from
RUN mkdir /workdir
WORKDIR /workdir

# install packages from apt that we will need
# currently its just 'python3', 'python3-pip', and 'sqlite3'
# if more programs are needed they should be added here
RUN apt update
RUN DEBIAN_FRONTEND=noninteractive apt install -y --no-install-recommends python3 python3-pip sqlite3 && apt clean
RUN rm -rf /var/lib/apt/lists/*

# copy the requirements.txt file into the container and install the python packages listed on it via pip
# 'pip install --upgrade pip' and 'pip install wheel' just ensure that the later installs run smoothly
COPY requirements.txt /workdir/
RUN pip install --upgrade pip
RUN pip install wheel
RUN pip install -r requirements.txt
