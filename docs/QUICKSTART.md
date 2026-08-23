# LiveBarn Manager Quick Start

## Start the Container

Clone the repository, create local configuration, and build the service:

```bash
git clone https://github.com/kineticman/LivebarnScrape.git
cd LivebarnScrape
cp .env.example .env
docker compose up -d --build
```

Set `LAN_IP` in `.env` to the address DVR clients use. `SERVER_PORT`
controls the host port and defaults to `5000`. The initial startup downloads the
venue catalog automatically and may take a minute or two.

Open `http://YOUR_SERVER_IP:5000`, then add favorite surfaces. In the
**LiveBarn Sign-in** card, save account credentials or choose **Use .env** to
use `LIVEBARN_EMAIL` and `LIVEBARN_PASSWORD` from `.env`. Saved credentials
take precedence and remain in the local SQLite database.

## Secure the Admin Page

Set these values in `.env` to require HTTP Basic authentication for the admin
UI and `/api/*` routes:

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=choose-a-strong-password
```

When `ADMIN_PASSWORD` is empty, admin routes remain open for compatibility.
The DVR-facing playlist, guide, and proxy URLs never require this Basic-auth
login.

## Configure a DVR Client

Add these URLs to Channels DVR or another compatible client:

```text
M3U:   http://YOUR_SERVER_IP:5000/playlist.m3u
XMLTV: http://YOUR_SERVER_IP:5000/xmltv
```

The first playback after sign-in may take several seconds while authentication
finishes. Later playback reuses the cached session until it approaches expiry.

## Useful Commands

```bash
docker compose logs -f          # Follow application and sign-in logs
docker compose restart          # Restart without rebuilding
docker compose up -d --build    # Rebuild after an update
docker compose down             # Stop and remove the container
```

## Troubleshooting

- No venues: wait for the initial catalog build, then inspect startup logs.
- No channels: add at least one favorite surface in the admin page.
- Sign-in fails: sign in once at `watch.livebarn.com` to resolve any account
  migration or CAPTCHA, then retry from the admin page.
- Playback fails: check `docker compose logs -f` for OAuth, playlist, or relay
  errors and confirm the subscription can view that surface in LiveBarn.
- Wrong generated port: set `SERVER_PORT` to the published host port and
  restart the container.

See the repository `README.md` for configuration and provider-development
details.
