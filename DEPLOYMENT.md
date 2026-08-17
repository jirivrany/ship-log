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

```bash
sudo adduser --disabled-password --gecos "" shiplog
# deliberately NOT in the sudo group

# give it your SSH key by copying from your own account (not from /root, which is empty)
sudo mkdir -p /home/shiplog/.ssh
sudo cp ~/.ssh/authorized_keys /home/shiplog/.ssh/
sudo chown -R shiplog:shiplog /home/shiplog/.ssh
sudo chmod 700 /home/shiplog/.ssh
sudo chmod 600 /home/shiplog/.ssh/authorized_keys
```

Verify `ssh shiplog@<ip>` works **in a second terminal** before changing any SSH settings. Keep your first session open until it does — a broken sshd config with no open session means a console/rescue trip to the provider.

### 2.2 SSH hardening

`/etc/ssh/sshd_config.d/99-hardening.conf` (create with `sudo nano`):

```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
AllowUsers shiplog <youruser>
```

`AllowUsers` must list **both** accounts — `shiplog` for deploys and `<youruser>` for admin. Omitting your sudo account locks you out of all privileged work.

```bash
sudo sshd -t && sudo systemctl restart ssh
```

`sshd -t` validates the config; if it errors, fix it *before* restarting, while you still have a working session.

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

### Why port 80 is open (revised decision)

The original draft of this plan closed port 80 and opened it briefly via certbot renewal hooks. **That was the wrong call — keep it open.** The reasoning:

- **HTTP-01 can only be validated on port 80.** Let's Encrypt refuses to validate on any other port by design, and it cannot be done over 443. Port 80 is therefore not optional for this challenge type; the only question is continuous vs. bursts.
- **The hook approach fails exactly when you can't fix it.** A non-executable hook, a `ufw delete` that doesn't match the rule, or a UFW behaviour change makes renewal fail *silently*. The certificate then expires ~60 days later, plausibly while you're at sea with only a phone. A silently expired cert is a worse outcome than an open port 80.
- **Exposure is small.** What listens on 80 is nginx serving a redirect — the same nginx already exposed on 443, on a well-audited code path.
- **You get the HTTPS redirect back**, so a bare `<domain>` typed on the phone lands on HTTPS instead of failing to connect.

Note on a common misconception: modern browsers do **not** block port 80. Chrome and Firefox *prefer* HTTPS — they upgrade typed navigations and warn before falling back — but plain HTTP still works when something is listening. So browser behaviour is not an argument for closing it; the redirect in §6 is what actually guarantees you end up on TLS.

**The stronger alternative, if you want it:** DNS-01 validation needs no inbound port at all, so port 80 could stay closed. The cost is that your DNS provider must have an API and you must store DNS-write credentials on the server. If your domain is on Cloudflare/Route53/similar, this is a genuine upgrade — install `python3-certbot-dns-<provider>` and issue with `--dns-<provider>` instead of `--nginx`. For a single-user app, either choice is defensible.

**Docker/UFW trap:** Docker writes its own iptables rules that bypass UFW, so a container published to `0.0.0.0` is reachable from the internet even with UFW denying it. Two defences, apply both:

1. Publish to loopback only in the compose file: `127.0.0.1:8000:8000` (§4).
2. Set `"iptables": false` is *not* recommended (breaks container networking); instead add to `/etc/docker/daemon.json`:

```json
{ "ip6tables": true, "userland-proxy": false }
```

and verify after starting the stack:

```bash
ss -tlnp | grep 8000      # must show 127.0.0.1:8000, never 0.0.0.0:8000
```

From your laptop: `nmap -Pn -p 22,80,443,8000 <server-ip>` — expect 22, 80 and 443 open, and **8000 closed**. Port 8000 showing as open means the Docker/UFW bypass has bitten you; fix the publish spec before going further.

---

## 4. Application deployment

### 4.1 Code and Docker

```bash
sudo apt install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker
sudo usermod -aG docker shiplog     # takes effect on shiplog's next login
```

Note that `docker` group membership is equivalent to root access (a container can mount the host filesystem). This is why `shiplog` is *not* also in the `sudo` group — it would add nothing but would widen what a stolen SSH key reaches.

Then, **logged in as `shiplog`** (`ssh shiplog@<ip>`), generate a key with `ssh-keygen -t ed25519`, add `~/.ssh/id_ed25519.pub` as a **read-only deploy key** on the GitHub repo, and clone:

```bash
git clone git@github.com:jirivrany/ship-log.git /home/shiplog/ship-log
cd /home/shiplog/ship-log/ship_log
```

### 4.2 A production compose file

The current `docker-compose.yml` is a dev file — it bind-mounts the whole source over `/app` and runs uvicorn with `--reload`. Do not use it in production. Add `docker-compose.prod.yml`:

```yaml
services:
  app:
    build: .
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"      # loopback only — nginx is the only client
    volumes:
      - ./data:/app/data           # data only; NOT the source tree
    environment:
      - DATABASE_URL=sqlite:////app/data/ship_log.db
      - UPLOAD_DIR=/app/data/uploads
      - STRAVA_CLIENT_ID=${STRAVA_CLIENT_ID:-}
      - STRAVA_CLIENT_SECRET=${STRAVA_CLIENT_SECRET:-}
      - STRAVA_REDIRECT_URI=https://<your-domain>/strava/callback
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Two deliberate differences from the dev file: no `.:/app` mount (the image's `COPY . .` is the code, so deploys are reproducible and a stray local file can't change server behaviour), and no `--reload` (halves idle memory and removes the file-watcher).

`--host 0.0.0.0` inside the container is correct and safe — the publish spec confines it to loopback on the host.

### 4.3 Container writes files as root

Your `data/` is already root-owned locally because the container runs as root. That is why the `Makefile` verify target shells into Alpine to copy files. On the server this makes backups and the sync in §7 need `sudo`.

Worth fixing while you're setting up production — add to the `Dockerfile` before `CMD`:

```dockerfile
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app
```

Then, once on the server, `sudo chown -R shiplog:shiplog data/` (uid 1000 in the image matches the first non-system account; verify with `id shiplog`). Afterwards backups, rsync and the dev pull all work **without `sudo`**, and a container escape lands as an unprivileged user.

This matters more than it looks under the §2.1 layout: `shiplog` has no sudo, so every `sudo` that appears in the data scripts below is a step you'd have to run as `<youruser>` instead. Doing this fix up front removes them all. If you defer it, run the data-touching commands in §7 from your sudo account instead.

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
scp /tmp/shiplog-migrate.tgz shiplog@<server>:/tmp/
```

```bash
# server, as shiplog — the tarball contains root-owned files, so extract
# with --no-same-owner to land them as shiplog rather than failing.
cd /home/shiplog/ship-log/ship_log
mkdir -p data
tar xzf /tmp/shiplog-migrate.tgz --no-same-owner -C data
docker compose -f docker-compose.prod.yml up -d --build
```

Check `https://<your-domain>` shows your voyages, then `rm /tmp/shiplog-migrate.tgz` on both machines.

### 7.2 Server → laptop pull (one-way, repeatable)

Server is authoritative. This **overwrites your local data** — that is the intended semantics of the choice you made, but it means never editing locally and expecting it to survive.

`scripts/pull-from-server.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SERVER=shiplog@<your-domain>
REMOTE=/home/shiplog/ship-log/ship_log/data
LOCAL="$(cd "$(dirname "$0")/.." && pwd)/data"

echo "This OVERWRITES $LOCAL with the server copy. Ctrl-C to abort."
read -r -p "Continue? [y/N] " a; [ "$a" = y ] || exit 1

# Consistent snapshot: SQLite's own backup API, safe while the app is running.
ssh "$SERVER" 'cd /home/shiplog/ship-log/ship_log && \
  docker compose -f docker-compose.prod.yml exec -T app \
  python -c "import sqlite3;s=sqlite3.connect(\"/app/data/ship_log.db\");d=sqlite3.connect(\"/app/data/_snapshot.db\");s.backup(d);d.close();s.close()"'

rsync -av --delete "$SERVER:$REMOTE/" "$LOCAL/"
mv "$LOCAL/_snapshot.db" "$LOCAL/ship_log.db"

# Remove the snapshot from inside the container: it wrote the file, so it owns it,
# and `shiplog` has no sudo. (After the §4.3 non-root fix, a plain rm also works.)
ssh "$SERVER" 'cd /home/shiplog/ship-log/ship_log && \
  docker compose -f docker-compose.prod.yml exec -T app rm -f /app/data/_snapshot.db'
echo "Done."
```

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

The `tar` reads root-owned files while running as `shiplog`, which works only because the container leaves them world-readable (`-rw-r--r--`). That's fragile — another reason to apply the §4.3 non-root fix, after which ownership is simply correct.

Install it as `shiplog`'s **own** crontab (no `sudo`, since the job needs no privileges):

```bash
chmod +x /home/shiplog/backup.sh
crontab -e     # as shiplog
# 0 3 * * *  /home/shiplog/backup.sh >> /home/shiplog/backup.log 2>&1
```

Local backups protect against app bugs, not against losing the VM. Add an off-box copy — either your provider's snapshots, or append to the pull script in §7.2 (running it after a trip is itself a backup). **Test a restore once**, before you rely on it at sea.

---

## 8. Deploy / update procedure

```bash
cd /home/shiplog/ship-log/ship_log
./  # -- steps:
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app   # confirm clean start
```

`init_db()` runs on startup (`app/main.py:13`), which creates missing tables but does **not** migrate changed columns. Any schema change beyond "new table" needs a hand-written migration — run `/home/shiplog/backup.sh` before deploying one.

---

## 9. Order of operations

Each step notes which account to run it from — `<youruser>` is your sudo account from the Ubuntu install, `shiplog` is the unprivileged app account from §2.1.

1. **`<youruser>`** — §2 hardening (create `shiplog`, SSH config, fail2ban) + §3 firewall, before anything is listening
2. DNS A record → server IP; confirm with `dig +short <your-domain>`
3. **`<youruser>`** — install Docker/nginx/certbot packages (§4.1, §5, §6)
4. **`shiplog`** — deploy key, clone, prod compose, `docker compose up` (§4.1–4.2); verify `ss -tlnp` shows `127.0.0.1:8000`
5. **`<youruser>`** — §5 certbot issuance + `certbot renew --dry-run`
6. **`<youruser>`** — §6 nginx basic auth, proxy and port-80 redirect; §6.1 Strava callback domain
7. **`shiplog`** (+ laptop) — §7.1 data migration
8. **`shiplog`** — §7.3 backups; then **verify a restore** before relying on it
9. From the laptop — `nmap` to confirm 22/80/443 open and 8000 closed (§3)

---

## 10. Follow-ups worth doing after it's live

- **Non-root container** (§4.3) — removes `sudo` from every data operation.
- **Real auth** if you ever add a second user; basic auth has no logout or session expiry.
- **A healthcheck** in the prod compose so Docker restarts a hung app rather than serving 502s from the boat.
