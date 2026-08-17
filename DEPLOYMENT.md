# Ship Log — Production Deployment

Fresh Ubuntu 26.04 LTS server. HTTPS + basic auth, Docker Compose behind host nginx.

- Server is authoritative for data; the laptop pulls one-way from it.
- App is delivered by `git pull` from `github.com/jirivrany/ship-log`.
- Two accounts: `<youruser>` (sudo, the only account with SSH access) and `shiplog` (unprivileged, runs the app, reached with `sudo su - shiplog`).

```
Internet ──443──> nginx (host) ──proxy──> 127.0.0.1:8000 ──> app container
             │       ├─ TLS (certbot)                          └─ /app/data (bind mount)
             │       └─ HTTP basic auth
             └──22──> sshd (key-only, <youruser> only)
```

---

## 1. Accounts

Ubuntu 26 locks the root account and disables root SSH — that is the desired state. Everything below runs as your installer-created sudo user.

```bash
sudo adduser --disabled-password --gecos "" shiplog
```

No sudo group, no password, no SSH key. `shiplog` is reachable only via `sudo su - shiplog`, so the server exposes exactly one account to the network.

Keep its shell as `/bin/bash`. `nologin` would break both `sudo su - shiplog` and the backup crontab (§7.3).

## 2. SSH hardening

`/etc/ssh/sshd_config.d/99-hardening.conf`:

```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
AllowUsers <youruser>
```

```bash
sudo sshd -t && sudo systemctl restart ssh
```

⚠️ `sshd -t` checks syntax, not whether `<youruser>` is spelled right. With one permitted account, a typo locks you out — confirm `ssh <youruser>@<ip>` from a second terminal **before** closing this session.

## 3. Updates, fail2ban, firewall

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y unattended-upgrades fail2ban ufw
sudo dpkg-reconfigure -plow unattended-upgrades
```

`/etc/fail2ban/jail.local` — basic auth is the app's only authentication, so `nginx-http-auth` matters here:

```ini
[sshd]
enabled = true
maxretry = 3
bantime  = 1h

[nginx-http-auth]
enabled = true
```

```bash
sudo systemctl enable --now fail2ban

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp  comment 'ssh'
sudo ufw allow 80/tcp  comment 'acme + https redirect'
sudo ufw allow 443/tcp comment 'https'
sudo ufw enable
```

Port 80 stays open permanently: HTTP-01 validates only there, and gating it behind renewal hooks risks a silent renewal failure and an expired cert while you're at sea. Only an HTTPS redirect is served on it (§6).

---

## 4. Docker

Use Docker's own apt repository. Ubuntu's `docker.io` is an unofficial build that Docker lists as a conflicting package, and it lacks the compose/buildx plugins.

```bash
sudo apt remove $(dpkg --get-selections docker.io docker-compose docker-compose-v2 \
  docker-doc docker-buildx podman-docker containerd runc | cut -f1)

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
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker.service containerd.service
sudo docker run hello-world
sudo usermod -aG docker shiplog
```

`/etc/docker/daemon.json`, before any container starts:

```json
{ "ip6tables": true, "userland-proxy": false }
```

```bash
sudo systemctl restart docker
```

`"iptables": false` is not an option — it breaks container networking.

The `docker` group is equivalent to root ("grants root-level privileges to the user" — Docker's docs). That is why `shiplog` is not also in `sudo`.

Don't hand-write `nft` rules on this box: Docker supports only `iptables-nft`/`iptables-legacy`. UFW uses the iptables compat layer, so it's fine. Custom container filtering goes in the `DOCKER-USER` chain.

---

## 5. Deploy the app

All of §5 runs as `shiplog`:

```bash
sudo su - shiplog
```

Generate a key, add `~/.ssh/id_ed25519.pub` as a **read-only deploy key** on the GitHub repo, then clone:

```bash
ssh-keygen -t ed25519
git clone git@github.com:jirivrany/ship-log.git /home/shiplog/ship-log
cd /home/shiplog/ship-log
```

`docker-compose.prod.yml` is already in the repo — no editing on the server. It differs from the dev file in three ways: publishes to `127.0.0.1:8000` only, mounts `data/` but not the source tree, and drops `--reload`.

Create the data directory and the `.env` **before the first start** — `data/` is gitignored, so the clone doesn't include it, and starting the stack first would create it as root:

```bash
mkdir -p data
cp .env.example .env
chmod 600 .env
```

Edit `.env`: set `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REDIRECT_URI=https://<your-domain>/strava/callback`, then append the host ids:

```bash
echo "UID_GID=$(id -u):$(id -g)" >> .env
```

`UID_GID` is why the image carries no fixed uid: uid 1000 is your laptop account but the sudo admin account on the server, so the uid is supplied per host at run time. `.env` is gitignored, so this never conflicts with a `git pull`.

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

If `data/` was ever created by a root container, repair it as `<youruser>`:

```bash
sudo chown -R shiplog:shiplog /home/shiplog/ship-log/data
```

### Verify it isn't exposed

Docker's NAT rules bypass UFW, so a container published to `0.0.0.0` is reachable from the internet even with UFW denying it. The loopback publish spec is the actual defence — verify it rather than assume it.

```bash
ss -tlnp | grep 8000                        # must be 127.0.0.1:8000
nmap -Pn -p 22,80,443,8000 <server-ip>      # from the laptop: 8000 CLOSED
```

Re-run both after any change to `ports:`. A dropped `127.0.0.1:` prefix exposes the app with no other symptom.

---

## 6. TLS and nginx

```bash
dig +short <your-domain>     # must print the server IP before continuing
sudo apt install -y nginx apache2-utils certbot python3-certbot-nginx
```

Order matters here: write the site file first, **then** run certbot. Certbot edits whatever server block already matches the domain, so without the file it would modify the `default` site instead.

### 6.1 Basic auth password file

```bash
sudo htpasswd -c /etc/nginx/.htpasswd jiri    # prompts twice for the password
sudo chmod 640 /etc/nginx/.htpasswd
sudo chown root:www-data /etc/nginx/.htpasswd
```

`-c` **creates and truncates**. Use it only this once — to change the password or add a user later, run `sudo htpasswd /etc/nginx/.htpasswd <user>` without it, or you will wipe the existing entries. nginx re-reads the file per request, so changes take effect with no reload.

### 6.2 Site config (HTTP only, for now)

**Create** `/etc/nginx/sites-available/shiplog` with exactly this — no TLS lines yet, certbot adds those in §6.3:

```nginx
server {
    listen 80;
    server_name <your-domain>;

    client_max_body_size 64M;      # FIT/GPX uploads; default 1M rejects them
    proxy_read_timeout   120s;     # PDF export can exceed the 60s default

    auth_basic           "Ship Log";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # Only needed for the webroot authenticator; certbot --nginx (§6.3) serves
    # the challenge itself. Kept as a fallback — safe to delete once the
    # renewal dry-run passes.
    location /.well-known/acme-challenge/ {
        auth_basic off;
        root /var/www/html;
    }

    location / {
        proxy_pass       http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it and drop the default site:

```bash
sudo ln -sf /etc/nginx/sites-available/shiplog /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Check `http://<your-domain>` prompts for the password and then shows the app. Fixing problems now is easier than after TLS is layered on.

### 6.3 Let certbot add TLS

```bash
sudo certbot --nginx -d <your-domain>
```

Choose **redirect** when it asks. Certbot rewrites the file in place: your original block becomes the `listen 443 ssl` one, and it appends a new port-80 block that redirects to HTTPS. It will also move the `acme-challenge` location into the 443 block — that is fine, see below.

Confirm renewal works unattended:

```bash
sudo certbot renew --dry-run
systemctl list-timers | grep certbot
```

The dry-run runs the real challenge against Let's Encrypt's staging endpoint. If it passes, renewal is unattended from here.

The `--nginx` authenticator does **not** serve the challenge from `/var/www/html`. It injects a temporary location block into your config, reloads, validates, then removes it and reloads again. So the `acme-challenge` location from §6.2 is only needed if you ever switch to the `webroot` authenticator — once the dry-run passes you can delete it, along with its now-pointless `auth_basic off;`.

What *would* break renewal is closing port 80 at the firewall (§3), since HTTP-01 validates only there.

### 6.4 Finishing touches

Reopen `/etc/nginx/sites-available/shiplog` and add these inside the **443** block, next to certbot's `listen 443 ssl;`:

```nginx
    http2 on;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
```

Certbot does not enable HTTP/2. Keep the headers at server level: `add_header` inside a `location` block *replaces* all inherited ones rather than adding to them.

Verify certbot kept `client_max_body_size`, `proxy_read_timeout` and both `auth_basic` lines in the 443 block — it normally copies them, but a missing `client_max_body_size` shows up much later as a 413 on your first upload.

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`/strava/callback` needs no special block: it sits behind basic auth like everything else. Strava never authenticates, but the browser arriving with `?code=...` already holds the credentials.

In Strava's API settings, set **Authorization Callback Domain** to `<your-domain>` (no scheme, no path).

Basic auth has no logout and no session expiry, so a lost phone means a lost session — use a device passcode and rotate the htpasswd entry if one goes missing.

---

## 7. Data

### 7.1 Initial migration (once)

Stop the local container first — a live copy of a SQLite file can be torn.

```bash
# laptop
cd ~/work/sailing/ship_log
docker compose down
sudo tar czf /tmp/shiplog-migrate.tgz -C data .
scp /tmp/shiplog-migrate.tgz <youruser>@<server>:/tmp/
```

```bash
# server, as shiplog. --no-same-owner because the tarball holds root-owned files.
sudo su - shiplog
cd /home/shiplog/ship-log
tar xzf /tmp/shiplog-migrate.tgz --no-same-owner -C data
docker compose -f docker-compose.prod.yml restart
```

Check the site shows your voyages, then delete the tarball on both machines.

### 7.2 Server → laptop pull

Server is authoritative; this **overwrites local data**. Connects as `<youruser>` because `shiplog` has no inbound SSH.

`scripts/pull-from-server.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SERVER=<youruser>@<your-domain>
REMOTE=/home/shiplog/ship-log/data
LOCAL="$(cd "$(dirname "$0")/.." && pwd)/data"

echo "This OVERWRITES $LOCAL with the server copy. Ctrl-C to abort."
read -r -p "Continue? [y/N] " a; [ "$a" = y ] || exit 1

# SQLite's backup API — safe to run while the app is live, unlike cp.
ssh "$SERVER" 'sudo -u shiplog -H bash -c "cd /home/shiplog/ship-log && \
  docker compose -f docker-compose.prod.yml exec -T app \
  python -c \"import sqlite3;s=sqlite3.connect(\\\"/app/data/ship_log.db\\\");d=sqlite3.connect(\\\"/app/data/_snapshot.db\\\");s.backup(d);d.close();s.close()\""'

rsync -av --delete --rsync-path="sudo -u shiplog rsync" "$SERVER:$REMOTE/" "$LOCAL/"
mv "$LOCAL/_snapshot.db" "$LOCAL/ship_log.db"

ssh "$SERVER" 'sudo -u shiplog rm -f /home/shiplog/ship-log/data/_snapshot.db'
echo "Done."
```

Needs passwordless sudo, or the `rsync` call hangs with no way to prompt. `sudo visudo -f /etc/sudoers.d/shiplog-sync`:

```
<youruser> ALL=(shiplog) NOPASSWD: /usr/bin/rsync, /usr/bin/docker, /usr/bin/rm, /usr/bin/bash
```

This only lets `<youruser>` become `shiplog` — an account it can already reach via `sudo su -`.

### 7.3 Backups

`/home/shiplog/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/shiplog/ship-log
STAMP=$(date +%Y%m%d-%H%M%S)
DEST=/home/shiplog/backups
mkdir -p "$DEST"
docker compose -f docker-compose.prod.yml exec -T app \
  python -c "import sqlite3;s=sqlite3.connect('/app/data/ship_log.db');d=sqlite3.connect('/app/data/_bk.db');s.backup(d);d.close();s.close()"
tar czf "$DEST/shiplog-$STAMP.tgz" -C data _bk.db uploads strava_tokens.json
rm -f data/_bk.db
find "$DEST" -name 'shiplog-*.tgz' -mtime +30 -delete
```

Minimal Ubuntu images ship without cron, so install it first:

```bash
sudo apt install -y cron
sudo systemctl enable --now cron
```

```bash
sudo su - shiplog
chmod +x /home/shiplog/backup.sh
crontab -e
```

Add this line, **uncommented**:

```
0 3 * * *  /home/shiplog/backup.sh >> /home/shiplog/backup.log 2>&1
```

Run `/home/shiplog/backup.sh` by hand once and check `ls /home/shiplog/backups` — cron reports failures only by mail to a local account nobody reads, so a broken job stays invisible until you need a restore.

These protect against app bugs, not against losing the VM — add provider snapshots or run §7.2 after each trip. **Test a restore before relying on it.**

---

## 8. Deploy an update

```bash
ssh <youruser>@<your-domain>
sudo su - shiplog
cd /home/shiplog/ship-log
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app
```

`init_db()` creates missing tables but does **not** migrate changed columns. Any schema change beyond "new table" needs a hand-written migration — run `backup.sh` first.

---

## 9. Order of operations

1. §1–2 accounts + SSH hardening — verify second-terminal login before closing the session
2. §3 updates, fail2ban, firewall
3. DNS A record → server IP
4. §4 Docker
5. §5 clone, `.env`, `mkdir data`, first start
6. §5 exposure check — **before** certbot; don't publish a hostname to a leaking box
7. §6 nginx on port 80 first, then certbot adds TLS; Strava callback domain
8. §7.1 data migration
9. §7.3 backups, then verify a restore

---

## 10. Later

- **Real auth** if a second user ever needs access — basic auth has no logout or expiry.
- **Healthcheck** in the prod compose, so Docker restarts a hung app instead of serving 502s from the boat.
- **DNS-01 certs** if you'd rather close port 80 — needs a DNS provider API and credentials on the server.
