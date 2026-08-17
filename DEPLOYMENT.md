# Ship Log — Production Deployment Plan

Target: fresh Ubuntu 26 server, single-user, HTTPS + basic auth, Docker Compose behind nginx.

**Decisions taken:** server is primary for data (one-way pull to laptop for dev); Strava OAuth repointed to the server domain; app delivered by `git pull` from `github.com/jirivrany/ship-log`.

---

## 1. Stack

```
Internet ──443/tcp──> nginx (host)  ──proxy──> 127.0.0.1:8000  ──> app container
             │            │                                          │
             │            ├─ TLS termination (certbot / Let's Encrypt)│
             │            └─ HTTP basic auth                          └─ /app/data (bind mount)
             └──22/tcp──> sshd (key-only)
```

**nginx, not Apache.** The app is a single uvicorn upstream with no `.htaccess`-style needs; nginx's proxy config is shorter, certbot's nginx plugin is the best-maintained path, and `auth_basic` gives exactly the protection you asked for. Apache would work but buys nothing here.

**nginx on the host, not in a container.** Certbot's `--nginx` plugin edits the host config and reloads it automatically on renew. Containerising nginx means hand-rolling the ACME webroot and renewal reload. For a single-user app, host nginx is materially simpler.

The app container binds to `127.0.0.1:8000` only — never `0.0.0.0`. That is what makes the firewall rule meaningful (see §6 for the Docker/UFW trap).

---

## 2. Server hardening (do this first, before the app)

**Ubuntu 26 has no root account to log into.** The installer locks root's password and creates one regular user with `sudo` membership; SSH ships with root login disabled. So there is no root shell to start from and nothing in `/root/.ssh` — every command below runs as your existing sudo user, and privileged ones are prefixed with `sudo`. Do not `sudo passwd root` to "fix" this; the locked root account is the desired state.

### 2.1 Which user runs the app

You already have a sudo user from the install (call it `<youruser>`). You have two reasonable options:

- **Use that account for everything.** Fewer moving parts. Fine for a single-user app.
- **Add a dedicated unprivileged `shiplog` account** that owns the code and data but has no sudo. Slightly better isolation: a compromise of the app account can't escalate.

The rest of this document assumes a dedicated `shiplog` account, because it makes the trust boundary explicit. If you prefer the simpler route, substitute `<youruser>` for `shiplog` throughout and skip this subsection.

**`shiplog` has no inbound SSH access at all** — no password, no `authorized_keys`, and it is not listed in `AllowUsers` (§2.2). You reach it only by logging in as `<youruser>` and switching:

```bash
sudo su - shiplog
```

This is deliberately stricter than giving it its own key. It means the server exposes exactly **one** account to the network, and `shiplog` becomes a privilege boundary rather than a second entry point: reaching it requires already holding your key *and* sudo. A stolen laptop key alone gets an attacker to `<youruser>`, not straight to the data.

```bash
sudo adduser --disabled-password --gecos "" shiplog
# no sudo group, no SSH key, no password — reachable only via `sudo su - shiplog`
```

`--disabled-password` locks the password; combined with the sshd config below, the account cannot authenticate over the network by any method.

**Keep its login shell as `/bin/bash`.** Setting `/usr/sbin/nologin` looks tempting for a service account, but it breaks `sudo su - shiplog` as well — `su -` invokes the target's login shell and `nologin` refuses. You would be pushed to `sudo -u shiplog bash`, which starts a non-login shell that skips `~/.profile` and does not pick up the `docker` group, and it would also break the `shiplog` crontab in §7.3. The account is unreachable because sshd rejects it, not because its shell is broken locally.

### 2.2 SSH hardening

`/etc/ssh/sshd_config.d/99-hardening.conf` (create with `sudo nano`):

```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
AllowUsers <youruser>
```

`AllowUsers` lists **only** your admin account. `shiplog` is deliberately absent (§2.1), so sshd refuses it before authentication is even attempted — belt and braces alongside its locked password and empty `authorized_keys`.

Make sure `<youruser>` is spelled correctly here: this single line is now the only account allowed in, so a typo locks you out of the server entirely.

```bash
sudo sshd -t && sudo systemctl restart ssh
```

`sshd -t` validates the config; if it errors, fix it *before* restarting, while you still have a working session.

Then, **keeping this session open**, confirm `ssh <youruser>@<ip>` still works from a second terminal. `sshd -t` catches syntax errors but not a misspelled username in `AllowUsers` — and with only one account permitted, that mistake means a console/rescue trip to the provider.

### 2.3 Updates and fail2ban

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y unattended-upgrades fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades
```

`/etc/fail2ban/jail.local` (`sudo nano /etc/fail2ban/jail.local`):

```ini
[sshd]
enabled = true
maxretry = 3
bantime  = 1h

[nginx-http-auth]
enabled = true
```

`nginx-http-auth` matters more than usual here: basic auth is your only app-level authentication, so brute-force protection on it is not optional.

```bash
sudo systemctl enable --now fail2ban
```

---

## 3. Firewall — SSH, HTTPS, and HTTP

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp  comment 'ssh'
sudo ufw allow 80/tcp  comment 'http: ACME challenge + redirect to https'
sudo ufw allow 443/tcp comment 'https'
sudo ufw enable
```

Port 80 stays open permanently: Let's Encrypt's HTTP-01 challenge can only be validated there, so gating it behind certbot renewal hooks risks a *silent* renewal failure and an expired cert while you're at sea. What listens on it is nginx serving a redirect to HTTPS (§6), so the exposure is small and a bare `<domain>` typed on a phone still lands on TLS.

Docker's firewall interaction is handled in §4.4, once Docker is actually installed.

---

## 4. Application deployment

### 4.1 Code and Docker

Install Docker from **Docker's own apt repository**, not from Ubuntu's `docker.io` package. Ubuntu's package is an unofficial build that lags upstream, and Docker's docs list it as a conflicting package that must be removed before the official one will install. It also does not ship `docker compose` v2 or buildx as plugins in the layout the rest of this document assumes.

Remove any conflicting packages first (apt may report none are installed — that is fine):

```bash
sudo apt remove $(dpkg --get-selections docker.io docker-compose docker-compose-v2 \
  docker-doc docker-buildx podman-docker containerd runc | cut -f1)
```

Add Docker's repository and GPG key:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
```

Then install the engine, CLI and plugins:

```bash
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker.service containerd.service
sudo docker run hello-world          # verify
sudo usermod -aG docker shiplog      # takes effect on shiplog's next login
```

`docker-compose-plugin` is what provides `docker compose` (space, not hyphen) — the form used throughout §7 and §8.

Ubuntu Resolute 26.04 LTS is a supported target in Docker's OS requirements, so the `Suites:` line resolving to its codename gets a real package set.

Note that `docker` group membership is equivalent to root access (a container can mount the host filesystem) — Docker's own post-install docs state the group "grants root-level privileges to the user". This is why `shiplog` is *not* also in the `sudo` group — it would add nothing but would widen what a stolen SSH key reaches.

**Daemon settings.** Do this now, before any container starts. Create `/etc/docker/daemon.json`:

```json
{ "ip6tables": true, "userland-proxy": false }
```

```bash
sudo systemctl restart docker
```

`"iptables": false` is *not* an option here — it breaks container networking. The real defence against the UFW bypass is the publish spec in §4.2, verified in §4.4.

Then switch to `shiplog` (`sudo su - shiplog` — it has no inbound SSH of its own, §2.1), generate a key with `ssh-keygen -t ed25519`, add `~/.ssh/id_ed25519.pub` as a **read-only deploy key** on the GitHub repo, and clone:

```bash
git clone git@github.com:jirivrany/ship-log.git /home/shiplog/ship-log
cd /home/shiplog/ship-log/ship_log
```

### 4.2 The production compose file

`docker-compose.prod.yml` is **already in the repo** — it arrives with the clone, no editing on the server. The dev `docker-compose.yml` bind-mounts the whole source over `/app` and runs uvicorn with `--reload`; do not use it in production.

Two deliberate differences from the dev file: no `.:/app` mount (the image's `COPY . .` is the code, so deploys are reproducible and a stray file on the server can't change behaviour), and no `--reload` (halves idle memory and removes the file-watcher).

`--host 0.0.0.0` inside the container is correct and safe — the `127.0.0.1:8000:8000` publish spec confines it to loopback on the host.

Everything host-specific — `UID_GID`, the Strava credentials and `STRAVA_REDIRECT_URI` — comes from `.env`, which is gitignored. That is what lets the same tracked file work on both machines; see §4.3 and §6.1.

### 4.3 Run the container as a non-root user

Your `data/` is currently root-owned because the container runs as root. That is why the `Makefile` verify target shells into Alpine to copy files. On the server this would make backups and the sync in §7 need `sudo` — which `shiplog` doesn't have.

**Do not bake a uid into the image.** Your laptop account is uid 1000, but on the server uid 1000 is the sudo account created by the Ubuntu installer, and `shiplog` — added afterwards — gets 1001 or higher. A `useradd -u 1000` in the Dockerfile would therefore write files owned by the *wrong* account on the server: the one you were trying to keep the app away from.

Instead the image is uid-agnostic and the uid is set per host at run time. **Both tracked files already carry this** — nothing to edit on the server:

- `Dockerfile` ends with `RUN useradd -m app && chown -R app:app /app` + `USER app` — note **no `-u 1000`**.
- `docker-compose.prod.yml` sets `user: "${UID_GID}"` on the `app` service.

`user:` overrides the image's `USER` with the numeric ids of whoever owns `data/` on that machine — so the same image and the same compose file work on laptop and server with no rebuild and no local edits. The `useradd` still matters: it stops the image *defaulting* to root if `UID_GID` is ever unset.

The only per-host step is the `.env` entry, and `.env` is gitignored — so this never conflicts with a `git pull`:

```bash
# on the server, as shiplog, in /home/shiplog/ship-log/ship_log
echo "UID_GID=$(id -u):$(id -g)" >> .env
```

Then, **as `<youruser>`** (`shiplog` has no sudo), hand it the data directory:

```bash
sudo chown -R shiplog:shiplog /home/shiplog/ship-log/ship_log/data
```

Locally you can ignore all of this — the dev `docker-compose.yml` is unchanged and has no `user:` key. If you want the laptop consistent too, add the same `.env` line and run `sudo chown -R $(id -u):$(id -g) data/` once to clear the existing root-owned files.

After this, backups, rsync and the §7.2 pull all work **without `sudo`**, and a container escape lands as an unprivileged account with no sudo rights.

> **Do this before the first `docker compose up`.** Starting the stack first lets the container create root-owned files under `data/`, which you then have to chown anyway.

### 4.4 Verify the container is not exposed (Docker/UFW trap)

Docker's docs are blunt: "Docker and ufw use firewall rules in ways that make them incompatible with each other." Container traffic is routed in the `nat` table, so packets are diverted before reaching the `INPUT` chain UFW filters on — "effectively ignoring your firewall configuration." A container published to `0.0.0.0` is reachable from the internet even with UFW denying it, and Docker offers no workaround of its own. The `127.0.0.1:8000:8000` publish spec in §4.2 is therefore the actual defence, not a belt-and-braces extra — which is why it gets verified rather than assumed.

Once the stack is up:

```bash
ss -tlnp | grep 8000      # must show 127.0.0.1:8000, never 0.0.0.0:8000
```

From your laptop:

```bash
nmap -Pn -p 22,80,443,8000 <server-ip>   # expect 22/80/443 open, 8000 CLOSED
```

Port 8000 showing as open means the bypass has bitten you — fix the publish spec before going further. Re-run both checks after any change to the compose file's `ports:` block; a dropped `127.0.0.1:` prefix exposes the app with no other visible symptom.

**Do not hand-write `nft` rules on this box.** Docker is only compatible with `iptables-nft` and `iptables-legacy`; rules created with `nft` are not supported on a host running Docker. UFW goes through the iptables compatibility layer, so it is fine — but any custom container filtering belongs in the `DOCKER-USER` chain.

---

## 5. TLS with certbot

You need a DNS A record pointing at the server first. Verify it resolves before running certbot — a wrong or unpropagated record is the most common issuance failure:

```bash
dig +short <your-domain>     # must print your server's IP
```

Then:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <your-domain>
```

With port 80 permanently open (§3) there are **no renewal hooks and no firewall juggling**. Certbot installs its own systemd timer; confirm both the timer and a real renewal dry-run:

```bash
systemctl list-timers | grep certbot     # snap/deb installs a twice-daily timer
sudo certbot renew --dry-run             # must pass
```

The dry-run exercises the full challenge against Let's Encrypt's staging endpoint. If it passes, renewal will work unattended — which is the whole point of not gating it behind hook scripts.

---

## 6. nginx: basic auth + proxy

```bash
sudo apt install -y nginx apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd jiri      # strong, unique password
sudo chmod 640 /etc/nginx/.htpasswd
sudo chown root:www-data /etc/nginx/.htpasswd
```

`/etc/nginx/sites-available/shiplog` (certbot will have inserted the `ssl_*` and `listen 443` lines; merge rather than overwrite):

```nginx
# Port 80: ACME challenge + redirect everything else to HTTPS.
server {
    listen 80;
    server_name <your-domain>;

    # Let's Encrypt HTTP-01 must reach this unauthenticated — no auth_basic here.
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    http2 on;
    server_name <your-domain>;

    # --- certbot-managed ---
    ssl_certificate     /etc/letsencrypt/live/<your-domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<your-domain>/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    client_max_body_size 64M;          # FIT/GPX uploads; default 1M would reject them

    auth_basic           "Ship Log";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;       # PDF export / FIT processing can be slow
    }

    # Strava redirects the browser here with ?code=... — Strava itself never
    # authenticates, but the *browser* does, and it already holds the basic-auth
    # credentials from the session that started the OAuth flow. Kept behind auth
    # deliberately: exempting it would expose a token-exchange endpoint publicly.
    location /strava/callback {
        proxy_pass       http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/shiplog /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

The `acme-challenge` location above is deliberately outside `auth_basic`. Let's Encrypt's validator is anonymous — if the challenge path sat behind basic auth it would get a 401 and renewal would fail. Everything else on port 80 is a redirect, so nothing sensitive is served unauthenticated.

Two settings above are easy to miss and both would bite you: `client_max_body_size` (uploads fail with 413 at the default 1 MB) and `proxy_read_timeout` (WeasyPrint PDF export can exceed the 60 s default).

**On basic auth as your only auth:** it is acceptable here because TLS protects the credential in transit, it's one user, and fail2ban covers brute force. Its real weakness is that the browser caches the credential with no logout and no session expiry — so if the phone is lost, the app is open to whoever holds it. Mitigation: a device passcode, and rotate the htpasswd entry if a device goes missing.

### 6.1 Strava app settings

In the Strava API settings, set **Authorization Callback Domain** to `<your-domain>` (no scheme, no path). The container env `STRAVA_REDIRECT_URI` in §4.2 must match `https://<your-domain>/strava/callback`. The code already reads both from env (`app/strava_api.py:95`), so no code change is needed.

Copy `.env` to the server manually (it is gitignored, correctly) with `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET`, mode `600`.

---

## 7. Data migration and one-way sync

Current local state is small — **217 KB DB + 12 MB uploads** — so migration is a single copy, no dump/restore machinery needed.

### 7.1 Initial migration (local → server, once)

Stop the local container first so SQLite is not mid-write. A live copy of a SQLite file can be torn; this is the one step where that matters.

```bash
# laptop
cd ~/work/sailing/ship_log
docker compose down
sudo tar czf /tmp/shiplog-migrate.tgz -C data .
scp /tmp/shiplog-migrate.tgz <youruser>@<server>:/tmp/     # shiplog has no inbound SSH
```

```bash
# server: land it as <youruser>, then switch accounts to unpack.
sudo su - shiplog

# The laptop tarball holds root-owned files (written before the §4.3 fix), so
# --no-same-owner lands them as shiplog — the uid the container now runs as.
cd /home/shiplog/ship-log/ship_log
mkdir -p data
tar xzf /tmp/shiplog-migrate.tgz --no-same-owner -C data
docker compose -f docker-compose.prod.yml up -d --build
```

`/tmp` is world-readable, so `shiplog` can read the tarball your admin account dropped there — no `chown` needed in between.

Check `https://<your-domain>` shows your voyages, then `rm /tmp/shiplog-migrate.tgz` on both machines.

### 7.2 Server → laptop pull (one-way, repeatable)

Server is authoritative. This **overwrites your local data** — that is the intended semantics of the choice you made, but it means never editing locally and expecting it to survive.

`scripts/pull-from-server.sh`:

Because `shiplog` has no inbound SSH (§2.1), this connects as your **admin** account and uses `sudo -u shiplog` for anything touching the app's files:

```bash
#!/usr/bin/env bash
set -euo pipefail
SERVER=<youruser>@<your-domain>          # NOT shiplog — it has no inbound SSH
REMOTE=/home/shiplog/ship-log/ship_log/data
LOCAL="$(cd "$(dirname "$0")/.." && pwd)/data"

echo "This OVERWRITES $LOCAL with the server copy. Ctrl-C to abort."
read -r -p "Continue? [y/N] " a; [ "$a" = y ] || exit 1

# Consistent snapshot: SQLite's own backup API, safe while the app is running.
ssh "$SERVER" 'sudo -u shiplog -H bash -c "cd /home/shiplog/ship-log/ship_log && \
  docker compose -f docker-compose.prod.yml exec -T app \
  python -c \"import sqlite3;s=sqlite3.connect(\\\"/app/data/ship_log.db\\\");d=sqlite3.connect(\\\"/app/data/_snapshot.db\\\");s.backup(d);d.close();s.close()\""'

# rsync reads as <youruser>, so make the tree readable to it; --rsync-path lets
# the remote side run as shiplog instead of relying on group permissions.
rsync -av --delete --rsync-path="sudo -u shiplog rsync" "$SERVER:$REMOTE/" "$LOCAL/"
mv "$LOCAL/_snapshot.db" "$LOCAL/ship_log.db"

ssh "$SERVER" 'sudo -u shiplog rm -f /home/shiplog/ship-log/ship_log/data/_snapshot.db'
echo "Done."
```

The `--rsync-path` trick is what keeps this working without loosening permissions on `data/`: rsync's remote half runs as `shiplog`, so it reads files that account owns, while the SSH session itself stays on your admin account.

**This needs passwordless sudo for the specific commands**, or every run prompts for your password (and the `rsync` one cannot prompt at all — it would hang). Add `/etc/sudoers.d/shiplog-sync` with `sudo visudo -f /etc/sudoers.d/shiplog-sync`:

```
<youruser> ALL=(shiplog) NOPASSWD: /usr/bin/rsync, /usr/bin/docker, /usr/bin/rm, /usr/bin/bash
```

That is a real widening of `<youruser>`'s powers — but only to *become `shiplog`*, an account it could already reach interactively via `sudo su -`. It grants nothing new in kind, just removes the prompt. Use `visudo` rather than an editor: a malformed sudoers file can lock you out of sudo entirely.

Using `sqlite3.backup()` rather than `cp` is what makes this safe to run while you're logging from the boat — a plain copy of a live SQLite file can land mid-transaction and produce a corrupt local DB.

### 7.3 Server backups

The server now holds the only authoritative copy, so this is the most important section, not an afterthought.

`/home/shiplog/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/shiplog/ship-log/ship_log
STAMP=$(date +%Y%m%d-%H%M%S)
DEST=/home/shiplog/backups
mkdir -p "$DEST"
docker compose -f docker-compose.prod.yml exec -T app \
  python -c "import sqlite3;s=sqlite3.connect('/app/data/ship_log.db');d=sqlite3.connect('/app/data/_bk.db');s.backup(d);d.close();s.close()"
tar czf "$DEST/shiplog-$STAMP.tgz" -C data _bk.db uploads strava_tokens.json
docker compose -f docker-compose.prod.yml exec -T app rm -f /app/data/_bk.db
find "$DEST" -name 'shiplog-*.tgz' -mtime +30 -delete
```

With §4.3 applied the container writes as `shiplog`, so the `tar` reads files it actually owns and the whole job needs no privileges. (Without that fix it would be reading root-owned files, working only by the accident of their being world-readable.)

Install it as `shiplog`'s **own** crontab (no `sudo`, since the job needs no privileges):

```bash
sudo su - shiplog
chmod +x /home/shiplog/backup.sh
crontab -e     # now editing shiplog's own crontab
# 0 3 * * *  /home/shiplog/backup.sh >> /home/shiplog/backup.log 2>&1
```

Cron runs the job as `shiplog` regardless of inbound SSH being disabled — the account not being reachable from the network has no bearing on local scheduled jobs. This is exactly why §2.1 keeps the login shell as `/bin/bash`: with `nologin`, cron could not start the job either.

Local backups protect against app bugs, not against losing the VM. Add an off-box copy — either your provider's snapshots, or append to the pull script in §7.2 (running it after a trip is itself a backup). **Test a restore once**, before you rely on it at sea.

---

## 8. Deploy / update procedure

```bash
ssh <youruser>@<your-domain>
sudo su - shiplog
cd /home/shiplog/ship-log/ship_log
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app   # confirm clean start
```

`init_db()` runs on startup (`app/main.py:13`), which creates missing tables but does **not** migrate changed columns. Any schema change beyond "new table" needs a hand-written migration — run `/home/shiplog/backup.sh` before deploying one.

---

## 9. Order of operations

Each step notes which account to run it from — `<youruser>` is your sudo account from the Ubuntu install, `shiplog` is the unprivileged app account from §2.1. **All SSH is to `<youruser>`**; every `shiplog` step below is reached with `sudo su - shiplog` once you're on the box.

1. **`<youruser>`** — §2 hardening (create `shiplog`, SSH config, fail2ban) + §3 firewall, before anything is listening
2. DNS A record → server IP; confirm with `dig +short <your-domain>`
3. **`<youruser>`** — install Docker from Docker's apt repo (§4.1 — *not* `docker.io`), write `/etc/docker/daemon.json`, then nginx/certbot packages (§5, §6)
4. **`shiplog`** — deploy key, clone, prod compose (§4.1–4.2); §4.3 non-root fix — `UID_GID` in `.env` + `chown data/` — **before** the first `docker compose up`
5. **`shiplog`** (+ laptop) — §4.4 exposure check: `ss -tlnp` shows `127.0.0.1:8000`, `nmap` shows 8000 closed. **Do this before certbot** — no point publishing a hostname to a box that's leaking port 8000.
6. **`<youruser>`** — §5 certbot issuance + `certbot renew --dry-run`
7. **`<youruser>`** — §6 nginx basic auth, proxy and port-80 redirect; §6.1 Strava callback domain
8. **`shiplog`** (+ laptop) — §7.1 data migration
9. **`shiplog`** — §7.3 backups; then **verify a restore** before relying on it

---

## 10. Follow-ups worth doing after it's live

- **Real auth** if you ever add a second user; basic auth has no logout or session expiry.
- **A healthcheck** in the prod compose so Docker restarts a hung app rather than serving 502s from the boat.
