# Nonlocally server

Seminar scheduling, briefings, and recording workflows.

## Container

Build on Debian 12 with current base-image and package updates:

```sh
podman build --pull=always --no-cache -t nonlocally .
```

Python dependencies are installed in `/workdir/env`. The entrypoint uses that
virtual environment; the system Python remains managed by Debian.

Pass the seminar series and data directory as arguments, and mount the existing
databases, configuration, and recordings at that directory. See
[`example-container-compose/docker-compose.yaml`](example-container-compose/docker-compose.yaml)
for the volume layout. Back up application data before replacing a deployment.
