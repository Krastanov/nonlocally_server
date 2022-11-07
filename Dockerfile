FROM ubuntu:20.04

RUN mkdir /workdir
WORKDIR /workdir

RUN apt update
RUN DEBIAN_FRONTEND=noninteractive apt install -y --no-install-recommends python3 sqlite3 python3-pip python3-venv git vim && apt clean
RUN rm -rf /var/lib/apt/lists/*
COPY requirements.txt /workdir/
RUN pip install --upgrade pip
RUN pip install wheel
RUN pip install -r requirements.txt
