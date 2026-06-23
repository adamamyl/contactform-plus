# EMF Infra VM Setup

This document covers bare-metal / VM setup for an EMF Conduct server running Docker rootless under the `conduct` service user. It supplements [sysadmin-setup.md](sysadmin-setup.md), which covers application deployment.

Tested on: **Debian 13 (trixie) / x86_64**.

---

## Table of contents

1. [Docker installation](#1-docker-installation)
2. [conduct user and groups](#2-conduct-user-and-groups)
3. [Docker rootless setup](#3-docker-rootless-setup)
4. [Traefik proxy gotchas](#4-traefik-proxy-gotchas)
5. [Kernel settings](#5-kernel-settings)
6. [Shell quality-of-life](#6-shell-quality-of-life)

---

## 1. Docker installation

Docker's official APT repository lags behind new Debian stable releases. On trixie, use the `bookworm` repo — the packages are compatible.

```bash
apt install -y ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Use bookworm repo until Docker publishes trixie packages
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
  > /etc/apt/sources.list.d/docker.list

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin docker-ce-rootless-extras
```

> **arch=amd64**: Docker's repo uses `amd64`, not `x86_64`. Using `x86_64` in the sources line causes a silent "doesn't support architecture" skip and no packages are found.

---

## 2. conduct user and groups

```bash
# Create service user
useradd -m -s /bin/bash -c "conduct repo code whazz,,," conduct

# Group for humans who need to operate as conduct
groupadd conduct-admins
usermod -aG conduct-admins <your-username>
```

### sudo rule

Create `/etc/sudoers.d/conduct` (mode `0440`, owned `root:root`):

```
%conduct-admins ALL=(conduct) ALL
```

This allows `conduct-admins` members to `sudo -u conduct` but not to become root or any other user.

Validate before saving:

```bash
visudo -c -f /etc/sudoers.d/conduct
chmod 0440 /etc/sudoers.d/conduct
```

### Convenience helper

`/usr/local/sbin/become-conduct` — drops into a login shell as `conduct`. Must be run as root or via sudo.

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
echo "error: must be run as root or via sudo" >&2
exit 1
fi

exec su - conduct
```

```bash
chmod 0755 /usr/local/sbin/become-conduct
```

> Always use `su - conduct` (with the dash) or `become-conduct`. Plain `su conduct` does not load `.bashrc` and `DOCKER_HOST` / `XDG_RUNTIME_DIR` will not be set.

---

## 3. Docker rootless setup

Order of operations matters. Skipping steps or using the wrong session type causes silent failures.

### 3.1 Enable linger (as root)

```bash
loginctl enable-linger conduct
apt install -y systemd-container   # provides machinectl
```

Linger keeps the `conduct` user's systemd manager alive without an active login session.

### 3.2 Open a proper session (as root)

```bash
machinectl shell conduct@
```

`machinectl shell` gives a full login session with `$XDG_RUNTIME_DIR` and `$DBUS_SESSION_BUS_ADDRESS` set correctly. Plain `su conduct` or `sudo -u conduct` do not.

If `machinectl` isn't available yet, set the vars manually:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=${XDG_RUNTIME_DIR}/bus
```

### 3.3 Install rootless Docker (as conduct, inside the session)

```bash
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker
```

> **Do not run this via `sudo -u conduct`** — it writes to `~/.config/systemd/user/` and registers with the user's D-Bus. Run it as `conduct` in a real session.

### 3.4 Persistent environment

Add to `~/.bashrc`:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DOCKER_HOST=unix://${XDG_RUNTIME_DIR}/docker.sock
```

### 3.5 DNS inside containers

Rootless Docker may not inherit the host's DNS resolver (common when the host uses `systemd-resolved` on `127.0.0.53`). Fix:

```bash
mkdir -p ~/.config/docker
echo '{"dns": ["1.1.1.1", "8.8.8.8"]}' > ~/.config/docker/daemon.json
systemctl --user restart docker
```

### 3.6 Verify

```bash
docker run --rm hello-world
```

---

## 4. Traefik proxy gotchas

### Socket access

Traefik needs access to the Docker socket to discover containers. In rootless mode the socket is at `/run/user/1003/docker.sock`, not `/var/run/docker.sock`.

Mount it via a variable to avoid hardcoding the UID. In `.env` (generated at deploy time as `conduct`):

```bash
echo "DOCKER_SOCK=/run/user/$(id -u)/docker.sock" >> .env
```

In `docker-compose.yml`:

```yaml
volumes:
  - ${DOCKER_SOCK}:/var/run/docker.sock:ro
```

Do **not** set `DOCKER_HOST` on the Traefik container — it overrides the socket mount and points inside the container's namespace where the host path doesn't exist.

### Privileged ports

By default, rootless processes cannot bind ports below 1024. Set this once on the host:

```bash
echo 'net.ipv4.ip_unprivileged_port_start=80' >> /etc/sysctl.conf
sysctl -p
```

Check for duplicates if `sysctl -p` shows conflicting values:

```bash
grep unprivileged_port /etc/sysctl.conf
```

---

## 5. Kernel settings

All kernel settings live in `/etc/sysctl.conf`. Applied with `sysctl -p` (no reboot needed).

| Setting | Value | Why |
|---|---|---|
| `net.ipv4.ip_unprivileged_port_start` | `80` | Traefik binds 80/443 in rootless Docker |

---

## 6. Shell quality-of-life

Recommended additions to `~/.bashrc` for the `conduct` user:

```bash
# Rootless Docker
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DOCKER_HOST=unix://${XDG_RUNTIME_DIR}/docker.sock

# History — append continuously, merge sessions, unlimited size, ISO-ish timestamps
HISTSIZE=-1
HISTFILESIZE=1000000
HISTCONTROL=ignoredups
HISTTIMEFORMAT="%Y-%m-%dT%H:%M:%SZ "
shopt -s histappend
PROMPT_COMMAND="history -a; history -n${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
```

> `HISTTIMEFORMAT` timestamps are stored in local time. The `Z` suffix is accurate only when the system timezone is UTC — which it should be on a server.
