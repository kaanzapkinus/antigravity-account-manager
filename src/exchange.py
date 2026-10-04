import os
import sys
import json
import time
import base64
import urllib.request
import urllib.parse
import urllib.error

profile_dir = os.path.expanduser(
    os.environ.get("AGY_PROFILE_DIR", "~/.antigravity-profiles/default")
)
pkce_file = os.path.join(profile_dir, 'pending_pkce.json')

if not os.path.exists(pkce_file):
    print("Error: pending_pkce.json not found", file=sys.stderr)
    sys.exit(1)

try:
    with open(pkce_file, "r", encoding="utf-8") as f:
        pkce = json.load(f)
except Exception as e:
    print(f"Error reading pending_pkce.json: {e}", file=sys.stderr)
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python3 exchange.py <auth_code>", file=sys.stderr)
    sys.exit(1)

code = sys.argv[1].strip()
if not code:
    print("Error: Empty auth code", file=sys.stderr)
    sys.exit(1)

data = urllib.parse.urlencode({
    'client_id': pkce['client_id'],
    'code': code,
    'code_verifier': pkce['verifier'],
    'grant_type': 'authorization_code',
    'redirect_uri': pkce['redirect_uri']
}).encode('utf-8')

req = urllib.request.Request(
    'https://oauth2.googleapis.com/token',
    data=data,
    headers={'Content-Type': 'application/x-www-form-urlencoded'}
)

try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        tokens = json.loads(resp.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    err_body = e.read().decode('utf-8', errors='ignore')
    print(f"Token exchange HTTP {e.code}: {err_body}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Token exchange network error: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(tokens, dict) or not tokens.get('access_token'):
    print("Error: Token response does not contain access_token", file=sys.stderr)
    sys.exit(1)

gemini_dir = os.path.join(profile_dir, '.gemini')
os.makedirs(gemini_dir, exist_ok=True)
creds_path = os.path.join(gemini_dir, 'oauth_creds.json')

# Existing credentials check for fallback refresh_token
existing_refresh = None
if os.path.exists(creds_path):
    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            old_c = json.load(f)
            if isinstance(old_c, dict):
                existing_refresh = old_c.get('refresh_token')
    except Exception:
        pass

refresh_token = tokens.get('refresh_token') or existing_refresh
if not refresh_token:
    print("Error: No refresh_token returned by Google and no existing token to preserve", file=sys.stderr)
    sys.exit(1)

email = None
id_token = tokens.get('id_token', '')
if id_token and id_token.count('.') >= 2:
    try:
        payload = id_token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        email = claims.get('email')
    except Exception:
        pass

if not email:
    print("Error: id_token did not carry an email claim", file=sys.stderr)
    sys.exit(1)

expires_in = int(tokens.get('expires_in', 3600))
oauth_creds = {
    'access_token': tokens['access_token'],
    'refresh_token': refresh_token,
    'scope': tokens.get('scope', 'https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile openid'),
    'token_type': tokens.get('token_type', 'Bearer'),
    'id_token': id_token,
    'expiry_date': int(time.time() * 1000) + expires_in * 1000
}

# Atomic write with 0o600 permissions (B11)
tmp_creds = f"{creds_path}.tmp.{os.getpid()}"
with open(tmp_creds, 'w', encoding='utf-8') as f:
    json.dump(oauth_creds, f, indent=2)
os.chmod(tmp_creds, 0o600)
os.replace(tmp_creds, creds_path)

google_accounts = {
    'active': email,
    'old': []
}
accounts_path = os.path.join(gemini_dir, 'google_accounts.json')
tmp_accs = f"{accounts_path}.tmp.{os.getpid()}"
with open(tmp_accs, 'w', encoding='utf-8') as f:
    json.dump(google_accounts, f, indent=2)
os.chmod(tmp_accs, 0o600)
os.replace(tmp_accs, accounts_path)

# Cleanup pending_pkce on success (B11)
try:
    os.remove(pkce_file)
except OSError:
    pass

print(f"SUCCESS: Authenticated and saved profile for {email}")
