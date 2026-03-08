FROM ubuntu:24.04

ARG MM_VERSION=9.11.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl libexpat1 \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://releases.mattermost.com/${MM_VERSION}/mattermost-team-${MM_VERSION}-linux-${ARCH}.tar.gz" \
    | tar -xz -C /opt && \
    mkdir -p /opt/mattermost/data /opt/mattermost/logs /opt/mattermost/plugins /opt/mattermost/client/plugins && \
    useradd -r -d /opt/mattermost mattermost && \
    chown -R mattermost:mattermost /opt/mattermost

USER mattermost
WORKDIR /opt/mattermost

EXPOSE 8065

CMD ["./bin/mattermost"]
