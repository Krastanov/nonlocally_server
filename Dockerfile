#start with debian:10
FROM debian:10

# make a directory to work from
RUN mkdir /workdir
WORKDIR /workdir

# install packages from apt that we will need
# currently its just 'python3', 'python3-pip', 'sqlite3', and 'ffmpeg'
# if more programs are needed they should be added here
RUN apt update
RUN DEBIAN_FRONTEND=noninteractive apt install -y --no-install-recommends python3 python3-pip sqlite3 && apt clean
RUN DEBIAN_FRONTEND=noninteractive apt install -y ffmpeg libx264-155 libx265-165 && apt clean

RUN rm -rf /var/lib/apt/lists/*

# copy all files, including the requirements.txt file into the container and install the python packages listed on it via pip
# 'pip install --upgrade pip' and 'pip install wheel' just ensure that the later installs run smoothly
COPY ./requirements.txt ./requirements.txt
RUN pip3 install --upgrade pip
RUN pip3 install wheel
RUN pip3 install -r requirements.txt
COPY ./ ./

ENTRYPOINT ["python3", "briefings_server.py"]
