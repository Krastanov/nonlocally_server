FROM debian:12

WORKDIR /workdir

# Keep package indexes and installation in the same layer on security rebuilds.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-venv sqlite3 wget ffmpeg libx264-164 libx265-199 && \
    rm -rf /var/lib/apt/lists/*

# Debian manages the system Python; install application packages in a venv.
RUN python3 -m venv /workdir/env
ENV PATH="/workdir/env/bin:$PATH"
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt
COPY . ./

ENTRYPOINT ["python", "briefings_server.py"]
