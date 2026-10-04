#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agyauth — Google (Antigravity) hesaplarini yonetme merkezi.

- Tum CLI profillerini (~/.gemini + ~/.antigravity-profiles/*) tarar: e-posta,
  yetki tarihi, token yenileme zamani, OMP vault durumu, 429 karantina blogu.
- Yeni gmail girisi: profil klasoru acar, agy PTY'sini yonetir, URL'yi uretir,
  kodu canli surece besler, basarili exchange'de OMP vault'a otomatik aktarir.
- Sadece 127.0.0.1 + tailnet IP'ye baglidir; token sirr asla gosterilmez/loglanmaz.
"""
import base64
import fcntl
import glob
import json
import math
import os
import re
import shlex
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib import parse as urlparse

HOME = os.path.expanduser("~")

# Tüm yollar ve ağ adresleri ortam değişkenleriyle verilir; kaynakta
# kullanıcıya özel değer bulunmaz.
AGY_BIN = os.environ.get("AGY_BIN", os.path.join(HOME, ".local", "bin", "agy"))
OMP_BIN = os.environ.get("OMP_BIN", os.path.join(HOME, ".local", "bin", "omp"))
MODEL = os.environ.get("AGY_MODEL", "gemini-3.8-flash-medium")
PORT = int(os.environ.get("PORT", "8098"))

# Dinlenecek adresler. Varsayılan yalnız loopback; tailnet gibi ek adresler
# TAILNET_IP ile açıkça verilmelidir. Boş bırakılırsa sadece 127.0.0.1.
LOOPBACK = os.environ.get("LISTEN_LOOPBACK", "127.0.0.1")
TAILNET = os.environ.get("TAILNET_IP", "")
BIND_ADDRESSES = [a for a in (LOOPBACK, TAILNET) if a]

PROFILES_ROOT = os.environ.get(
    "AGY_PROFILES_ROOT", os.path.join(HOME, ".antigravity-profiles")
)
if PROFILES_ROOT not in sys.path:
    sys.path.insert(0, PROFILES_ROOT)
try:
    import history_store
except ImportError:
    history_store = None
DEFAULT_PROFILE = os.environ.get("AGY_DEFAULT_PROFILE", os.path.join(HOME, ".gemini"))
AGENT_DB = os.environ.get(
    "AGY_AGENT_DB", os.path.join(HOME, ".omp", "agent", "agent.db")
)
AG_TOOLS_CONFIG = os.environ.get(
    "AGY_TOOLS_CONFIG", os.path.join(HOME, ".antigravity_tools", "gui_config.json")
)
PROXY_SWITCH_LOCK = threading.Lock()


def child_path():
    """Alt sureclerin (agy/omp) kullanacagi PATH. Kullaniciya ozel yol icermez."""
    return os.environ.get(
        "AGY_CHILD_PATH",
        os.pathsep.join([
            os.path.join(HOME, ".local", "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            os.path.dirname(os.path.abspath(sys.executable)),
            "/usr/bin",
            "/bin",
        ]),
    )
ANSI_RE = re.compile(r"\x1b(?:\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
URL_RE = re.compile(r"https://accounts\.google\.com/o/oauth2/auth\?access_type=offline.*?state=[A-Za-z0-9_\-]{22}")


# ---------------------------------------------------------------- helpers
def profile_gem_dir(profile_dir):
    """Profilin .gemini koku. ~/.gemini icin dogrudan kendisi."""
    if os.path.basename(profile_dir) == ".gemini":
        return profile_dir
    return os.path.join(profile_dir, ".gemini")


def existing_profile_gem_dir(profile_dir):
    """Yalnizca mevcut, desteklenen CLI profilinin .gemini kokunu kabul et."""
    if not isinstance(profile_dir, str) or not profile_dir or "\0" in profile_dir:
        raise ValueError("CLI profili eksik; bu hesap icin once CLI girisi yapin")
    if not os.path.isabs(profile_dir):
        raise ValueError("gecersiz CLI profil yolu")
    profile_dir = os.path.realpath(profile_dir)
    default_real = os.path.realpath(DEFAULT_PROFILE)
    profiles_root_real = os.path.realpath(PROFILES_ROOT)
    if (profile_dir != default_real
            and os.path.dirname(profile_dir) != profiles_root_real):
        raise ValueError("desteklenmeyen CLI profil yolu")
    gem = profile_dir if profile_dir == default_real else os.path.join(profile_dir, ".gemini")
    if not os.path.isdir(profile_dir) or not os.path.isdir(gem):
        raise ValueError("CLI profili bulunamadi; bu hesap icin once CLI girisi yapin")
    real_gem = os.path.realpath(gem)
    if profile_dir == default_real:
        if real_gem != default_real:
            raise ValueError("desteklenmeyen profil baglantisi")
    else:
        if not real_gem.startswith(profile_dir + os.sep) and real_gem != profile_dir:
            raise ValueError("profil icindeki symlink izin verilen dizinin disina cikamaz")
    return real_gem


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def idt_email(id_token):
    try:
        p = id_token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("email")
    except Exception:
        return None


def parse_iso_ms(s):
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return None


# ---------------------------------------------------------------- account scan
def proxy_api(method, path, payload=None):
    """Antigravity Tools admin API; kimlik bilgisi disari sizmaz."""
    cfg = read_json(AG_TOOLS_CONFIG) or {}
    proxy = cfg.get("proxy") or {}
    port = int(proxy.get("port") or 8045)
    key = proxy.get("api_key")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(payload).encode() if payload is not None else None
    req = urlrequest.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urlrequest.urlopen(req, timeout=8) as res:
            raw = res.read()
            return res.status, json.loads(raw) if raw else None
    except urlerror.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw) if raw else None
        except Exception:
            body = {"error": "HTTP %s" % e.code}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def proxy_snapshot():
    """Proxy hesaplari ve secim durumunun guvenli gorunumu."""
    st_accounts, body_accounts = proxy_api("GET", "/api/accounts")
    st_status, body_status = proxy_api("GET", "/api/proxy/status")
    st_pref, preferred = proxy_api("GET", "/api/proxy/preferred-account")
    accounts = []
    current_account_id = None
    accounts_valid = False
    if st_accounts == 200:
        if isinstance(body_accounts, dict):
            accounts = body_accounts.get("accounts")
            current_account_id = body_accounts.get("current_account_id")
        elif isinstance(body_accounts, list):
            accounts = body_accounts
        accounts_valid = isinstance(accounts, list) and all(
            isinstance(account, dict)
            and isinstance(account.get("id"), str) and bool(account["id"])
            and isinstance(account.get("email", ""), str)
            for account in accounts
        )
        accounts_valid = accounts_valid and (
            current_account_id is None or isinstance(current_account_id, str)
        )
    preferred_valid = preferred is None or isinstance(preferred, str)
    if isinstance(preferred, dict):
        keys = ("account_id", "accountId", "id")
        preferred_valid = any(key in preferred for key in keys) and all(
            preferred[key] is None or isinstance(preferred[key], str)
            for key in keys if key in preferred
        )
    status_valid = isinstance(body_status, dict) and isinstance(body_status.get("running"), bool)
    ok = (st_accounts == 200 and st_status == 200 and st_pref == 200
          and accounts_valid and status_valid and preferred_valid)
    return {
        "ok": ok,
        "accounts": accounts if accounts_valid else [],
        "status": body_status if status_valid else {},
        "preferred": preferred if st_pref == 200 and preferred_valid else None,
        "current_account_id": current_account_id if accounts_valid else None,
    }


def public_proxy_account(account):
    quota = account.get("quota") or {}
    models = []
    raw_models = quota.get("models") if isinstance(quota, dict) else None
    for model in raw_models if isinstance(raw_models, list) else []:
        if not isinstance(model, dict):
            continue
        pct_val = model.get("percentage")
        if isinstance(pct_val, (int, float)) and not math.isnan(pct_val) and not math.isinf(pct_val):
            pct_val = round(max(0.0, min(100.0, float(pct_val))), 1)
        else:
            pct_val = None
        models.append({
            "name": model.get("name"),
            "percentage": pct_val,
            "reset_time": model.get("reset_time"),
        })

    raw_groups = quota.get("quota_groups", []) if isinstance(quota, dict) else []
    quota_groups = []
    ag_pct_5, ag_pct_wk = None, None
    ag_rst_5, ag_rst_wk = None, None
    cg_pct_5, cg_pct_wk = None, None
    cg_rst_5, cg_rst_wk = None, None

    for g in raw_groups if isinstance(raw_groups, list) else []:
        if not isinstance(g, dict):
            continue
        disp = str(g.get("display_name") or "")
        buckets = []
        raw_buckets = g.get("buckets")
        for b in raw_buckets if isinstance(raw_buckets, list) else []:
            if not isinstance(b, dict):
                continue
            rem_frac = b.get("remaining_fraction")
            pct = None
            if isinstance(rem_frac, (int, float)) and not math.isnan(rem_frac) and not math.isinf(rem_frac):
                pct = round(max(0.0, min(100.0, float(rem_frac * 100))), 1)
            win = b.get("window")
            rst = b.get("reset_time")
            b_id = str(b.get("bucket_id") or "").lower()
            buckets.append({
                "bucket_id": b.get("bucket_id"),
                "window": win,
                "remaining_fraction": rem_frac,
                "percentage": pct,
                "reset_time": rst,
                "display_name": b.get("display_name"),
                "description": b.get("description"),
            })

            # B3: Kararlı bucket_id ve display_name eşlemesi
            is_gem = b_id.startswith("gemini-") or b_id == "gemini" or "gemini" in disp.lower()
            if is_gem:
                if win == "5h" and pct is not None:
                    ag_pct_5 = pct
                    ag_rst_5 = rst
                elif win == "weekly" and pct is not None:
                    ag_pct_wk = pct
                    ag_rst_wk = rst
            else:
                if win == "5h" and pct is not None:
                    cg_pct_5 = pct
                    cg_rst_5 = rst
                elif win == "weekly" and pct is not None:
                    cg_pct_wk = pct
                    cg_rst_wk = rst

        quota_groups.append({
            "display_name": disp,
            "description": g.get("description"),
            "buckets": buckets,
        })

    # Fallback to models if quota_groups didn't provide buckets
    if models and (ag_pct_5 is None or cg_pct_5 is None):
        gem_models = [m for m in models if "gemini" in str(m.get("name", "")).lower() and m.get("percentage") is not None]
        cg_models = [m for m in models if "gemini" not in str(m.get("name", "")).lower() and m.get("percentage") is not None]
        if ag_pct_5 is None and gem_models:
            min_p = min(m["percentage"] for m in gem_models)
            ag_pct_5 = min_p
            ag_pct_wk = min_p
            resets = [m.get("reset_time") for m in gem_models if m.get("reset_time")]
            if resets:
                ag_rst_5 = resets[0]
                ag_rst_wk = resets[0]
        if cg_pct_5 is None and cg_models:
            min_p = min(m["percentage"] for m in cg_models)
            cg_pct_5 = min_p
            cg_pct_wk = min_p
            resets = [m.get("reset_time") for m in cg_models if m.get("reset_time")]
            if resets:
                cg_rst_5 = resets[0]
                cg_rst_wk = resets[0]

    ai_limits = {
        "gemini": {
            "name": "Antigravity (Gemini)",
            "subtitle": "Flash & Pro paylaşımlı havuz",
            "p5": {"percentage": ag_pct_5, "reset_time": ag_rst_5},
            "weekly": {"percentage": ag_pct_wk, "reset_time": ag_rst_wk},
        },
        "claude_gpt": {
            "name": "Claude / GPT",
            "subtitle": "Sonnet, Opus & OpenAI modelleri",
            "p5": {"percentage": cg_pct_5, "reset_time": cg_rst_5},
            "weekly": {"percentage": cg_pct_wk, "reset_time": cg_rst_wk},
        },
    }
    return {
        "id": account.get("id"),
        "name": account.get("name"),
        "disabled": bool(account.get("disabled")),
        "disabled_reason": account.get("disabled_reason"),
        "proxy_disabled": bool(account.get("proxy_disabled")),
        "proxy_disabled_reason": account.get("proxy_disabled_reason"),
        "validation_blocked": bool(account.get("validation_blocked")),
        "validation_blocked_reason": account.get("validation_blocked_reason"),
        "quota_models": models,
        "quota_groups": quota_groups,
        "ai_limits": ai_limits,
        "subscription_tier": quota.get("subscription_tier") if isinstance(quota, dict) else None,
    }


def scan_accounts():
    """CLI, OMP vault ve Antigravity Tools hesaplarini e-postayla birlestirir."""
    now_ms = int(time.time() * 1000)
    vault = {}
    blocks = {}
    try:
        db = sqlite3.connect("file:%s?mode=ro" % AGENT_DB, uri=True)
        for email, expires, authed, proj, cid in db.execute(
            "SELECT json_extract(data,'$.email'), json_extract(data,'$.expires'), "
            "json_extract(data,'$.authorizedAt'), json_extract(data,'$.projectId'), id "
            "FROM auth_credentials WHERE provider='google-antigravity'"
        ):
            if email:
                vault[email.strip().lower()] = {
                    "expires": expires, "authorizedAt": authed,
                    "projectId": proj, "cred_id": cid,
                }
        for cid, scope, until in db.execute(
            "SELECT credential_id, block_scope, blocked_until_ms FROM auth_credential_blocks "
            "WHERE provider_key='google-antigravity:oauth'"
        ):
            blocks.setdefault(cid, {})[scope] = until
        db.close()
    except Exception:
        pass

    prof_dirs = []
    if os.path.isdir(DEFAULT_PROFILE):
        prof_dirs.append(("varsayilan", DEFAULT_PROFILE))
    for d in sorted(glob.glob(os.path.join(PROFILES_ROOT, "*"))):
        if os.path.isdir(d) and not d.endswith(".py"):
            prof_dirs.append((os.path.basename(d), d))

    merged = {}
    unknown = []
    for label, pdir in prof_dirs:
        try:
            gem = profile_gem_dir(pdir)
            ga = read_json(os.path.join(gem, "google_accounts.json"))
            email = None
            if isinstance(ga, dict):
                email = (ga.get("active") or "").strip().lower() or None
            elif isinstance(ga, list) and ga:
                first = ga[0] if isinstance(ga[0], dict) else {}
                email = (first.get("email") or first.get("active") or "").strip().lower() or None

            tok_ms = None
            last_refresh = None
            has_refresh = False
            newtok = os.path.join(gem, "antigravity-cli", "antigravity-oauth-token")
            oldtok = os.path.join(gem, "oauth_creds.json")
            src = None
            if os.path.isfile(newtok):
                src = newtok
                d = read_json(newtok)
                if isinstance(d, dict):
                    t = d.get("token")
                    if isinstance(t, dict):
                        tok_ms = parse_iso_ms(t.get("expiry", ""))
                        has_refresh = bool(t.get("refresh_token"))
                    if not email and d.get("id_token"):
                        email = (idt_email(d["id_token"]) or "").strip().lower() or None
            if tok_ms is None and os.path.isfile(oldtok):
                src = src or oldtok
                d = read_json(oldtok)
                if isinstance(d, dict):
                    tok_ms = d.get("expiry_date")
                    has_refresh = bool(d.get("refresh_token"))
                    if not email and d.get("id_token"):
                        email = (idt_email(d["id_token"]) or "").strip().lower() or None
            if src:
                try:
                    last_refresh = int(os.stat(src).st_mtime * 1000)
                except OSError:
                    pass
            cli = {
                "label": label, "dir": pdir, "home": os.path.dirname(gem),
                "has_token": bool(src), "has_refresh": has_refresh,
                "cli_refresh": last_refresh, "cli_expires": tok_ms,
            }
            if email:
                row = merged.setdefault(email, {"email": email, "cli_profiles": []})
                row["cli_profiles"].append(cli)
            else:
                unknown.append({**cli, "email": None, "cli_profiles": [cli]})
        except Exception as e:
            print(f"[scan_accounts] Profil okuma uyarisi ({label}: {pdir}): {e}", file=sys.stderr)
            continue
    for email, v in vault.items():
        merged.setdefault(email, {"email": email, "cli_profiles": []})

    proxy = proxy_snapshot()
    for account in proxy["accounts"]:
        email = (account.get("email") or "").strip().lower()
        if email:
            merged.setdefault(email, {"email": email, "cli_profiles": []})["proxy"] = \
                public_proxy_account(account)

    current_id = proxy.get("current_account_id")
    preferred_id = _preferred_id(proxy.get("preferred"))
    active_id = current_id or preferred_id

    accounts = []
    for email in sorted(merged):
        row = merged[email]
        profiles = row.get("cli_profiles") or []
        # B4: Token ve refresh token içeren en iyi profili seç
        if profiles:
            cli = max(profiles, key=lambda p: (
                bool(p.get("has_token")),
                bool(p.get("has_refresh")),
                p.get("cli_refresh") or 0
            ))
        else:
            cli = {}
        v = vault.get(email)
        blk = blocks.get(v["cred_id"], {}) if v else {}
        pxy = row.get("proxy") or {}
        row.update(cli)
        is_pref = bool(pxy and (pxy.get("id") == preferred_id))
        is_current = bool(pxy and (pxy.get("id") == current_id))
        if pxy:
            pxy["is_preferred"] = is_pref
            pxy["is_current"] = is_current
        row.update({
            "label": cli.get("label") or "yalnizca proxy/OMP",
            "has_token": bool(cli.get("has_token")),
            "has_refresh": bool(cli.get("has_refresh")),
            "omp": bool(v),
            "omp_authorized": (v or {}).get("authorizedAt"),
            "omp_expires": (v or {}).get("expires"),
            "omp_project": (v or {}).get("projectId"),
            "block_google": blk.get("counter:google"),
            "block_anthropic": blk.get("counter:anthropic"),
            "proxy": pxy or None,
            "proxy_preferred": is_pref,
            "proxy_current": is_current,
            "is_preferred": is_pref,
            "now": now_ms,
        })
        accounts.append(row)
    accounts.extend(unknown)
    return {
        "accounts": accounts,
        "preferred_account": next((a["email"] for a in accounts if a.get("is_preferred")), None),
        "proxy": {
            "ok": bool(proxy.get("ok")),
            "running": bool(proxy.get("status", {}).get("running")),
            "active_accounts": proxy.get("status", {}).get("active_accounts"),
            "preferred_account_id": preferred_id,
            "current_account_id": current_id,
            "active_account_id": active_id,
        },
    }



def _preferred_id(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("account_id") or value.get("accountId") or value.get("id")
    return None


def _public_proxy_state():
    snapshot = proxy_snapshot()
    current_id = snapshot.get("current_account_id")
    preferred = _preferred_id(snapshot.get("preferred"))
    active_id = current_id or preferred
    return {
        "ok": bool(snapshot.get("ok")),
        "running": bool(snapshot.get("status", {}).get("running")),
        "active_accounts": snapshot.get("status", {}).get("active_accounts"),
        "preferred_account_id": preferred,
        "current_account_id": current_id,
        "active_account_id": active_id,
        "accounts": [
            {
                "id": account.get("id"),
                "email": account.get("email"),
                "proxy_disabled": bool(account.get("proxy_disabled")),
                "disabled": bool(account.get("disabled")),
                "validation_blocked": bool(account.get("validation_blocked")),
            }
            for account in snapshot.get("accounts", [])
        ],
    }
SCHEDULER_STATE_PATH = os.path.join(PROFILES_ROOT, "agy-scheduler-state.json")


def _update_scheduler_state(mode=None, manual_pin=None, ours=None):
    """Scheduler durum dosyasını günceller (S1 & S2 mod senkronizasyonu)."""
    try:
        data = {}
        if os.path.exists(SCHEDULER_STATE_PATH):
            try:
                with open(SCHEDULER_STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        if mode is not None:
            data["mode"] = mode
        if manual_pin is not None or mode in ("auto", "pool"):
            data["manual_pin"] = manual_pin
        if ours is not None or mode == "pool":
            data["ours"] = ours
        data["updated_at"] = int(time.time())
        tmp = f"{SCHEDULER_STATE_PATH}.tmp.{os.getpid()}_{time.time_ns()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SCHEDULER_STATE_PATH)
    except Exception as e:
        print(f"[_update_scheduler_state] Durum guncelleme uyarisi: {e}", file=sys.stderr)


def switch_proxy_account(account_id=None, email=None, trigger="manual", reason=None):
    """Proxy-global preferred account switch with verification and rollback."""
    if (account_id is not None and not isinstance(account_id, str)
            or email is not None and not isinstance(email, str)
            or not account_id and not email):
        return {"ok": False, "err": "gecerli hesap kimligi veya e-posta gerekli"}
    with PROXY_SWITCH_LOCK:
        before = proxy_snapshot()
        if not before.get("ok"):
            return {"ok": False, "err": "Antigravity proxy API erisilemiyor"}
        if account_id:
            target = next((a for a in before["accounts"] if a.get("id") == account_id), None)
        else:
            clean_email = (email or "").strip().lower()
            target = next((a for a in before["accounts"] if (a.get("email") or "").strip().lower() == clean_email), None)
        if not target:
            return {"ok": False, "err": "proxy hesabi bulunamadi"}
        account_id = target.get("id")
        if target.get("disabled") or target.get("validation_blocked"):
            return {"ok": False, "err": "hesap devre disi veya dogrulama engelli"}

        previous_preferred = _preferred_id(before.get("preferred"))
        previous_current = before.get("current_account_id")
        was_proxy_disabled = bool(target.get("proxy_disabled"))
        enabled_here = False
        selection_attempted = False
        try:
            if was_proxy_disabled:
                status, _ = proxy_api(
                    "POST",
                    "/api/accounts/%s/toggle-proxy" % urlparse.quote(account_id, safe=""),
                    {"enable": True, "reason": "agyauth hesap secimi"},
                )
                if status != 200:
                    raise RuntimeError("hesap proxy havuzuna eklenemedi (HTTP %s)" % status)
                enabled_here = True

            refreshed = proxy_snapshot()
            live_target = next(
                (a for a in refreshed.get("accounts", []) if a.get("id") == account_id),
                None,
            )
            if (not refreshed.get("ok") or not live_target or live_target.get("proxy_disabled")
                    or live_target.get("disabled") or live_target.get("validation_blocked")):
                raise RuntimeError("hesap etkinlestirme dogrulamasi basarisiz")

            # Antigravity Tools ana aktif hesabi ve proxy tercihini ayni anda guncelle
            selection_attempted = True
            st_sw, _ = proxy_api("POST", "/api/accounts/switch", {"accountId": account_id})
            st_pr, _ = proxy_api("POST", "/api/proxy/preferred-account", {"accountId": account_id})
            if st_sw != 200 or st_pr != 200:
                raise RuntimeError("hesap gecisi basarisiz (switch: %s, pref: %s)" % (st_sw, st_pr))

            after = proxy_snapshot()
            if (not after.get("ok") or after.get("current_account_id") != account_id
                    or _preferred_id(after.get("preferred")) != account_id):
                raise RuntimeError("aktif hesap dogrulanamadi")

            # S1: Scheduler durumunu manuel moda gecir
            _update_scheduler_state(mode="manual", manual_pin=account_id)

            # B6: History yazma hatasi basarili switch islemini geri almamalidir
            if history_store:
                try:
                    prev_acc = next((a for a in before.get("accounts", []) if a.get("id") == previous_current), None)
                    from_email = prev_acc.get("email") if prev_acc else None
                    history_store.record_switch(
                        to_id=account_id,
                        to_email=live_target.get("email"),
                        from_id=previous_current,
                        from_email=from_email,
                        trigger=trigger or "manual",
                        reason=reason or "Google Hesapları panelinden kullanıcı tarafından elle tercih belirlendi",
                    )
                except Exception as h_err:
                    print(f"[switch_proxy_account] Gecmis kaydetme uyarisi: {h_err}", file=sys.stderr)

            return {
                "ok": True,
                "account_id": account_id,
                "email": live_target.get("email"),
                "active_accounts": after.get("status", {}).get("active_accounts"),
            }
        except Exception as exc:
            rolled_back = False
            try:
                if selection_attempted:
                    if previous_current is not None:
                        proxy_api("POST", "/api/accounts/switch", {"accountId": previous_current})
                    # None restores automatic proxy routing, not the failed target.
                    proxy_api("POST", "/api/proxy/preferred-account", {"accountId": previous_preferred})
                if was_proxy_disabled and not enabled_here:
                    # A failed enable response may still have changed server state.
                    failed = proxy_snapshot()
                    enabled_here = failed.get("ok") and any(
                        a.get("id") == account_id and not a.get("proxy_disabled")
                        for a in failed.get("accounts", [])
                    )
                if enabled_here:
                    proxy_api(
                        "POST",
                        "/api/accounts/%s/toggle-proxy" % urlparse.quote(account_id, safe=""),
                        {"enable": False, "reason": target.get("proxy_disabled_reason") or "agyauth gecis geri alma"},
                    )
                restored = proxy_snapshot()
                restored_target = next(
                    (a for a in restored.get("accounts", []) if a.get("id") == account_id), None
                )
                rolled_back = bool(
                    restored.get("ok") and restored_target
                    and _preferred_id(restored.get("preferred")) == previous_preferred
                    and restored.get("current_account_id") == previous_current
                    and bool(restored_target.get("proxy_disabled")) == was_proxy_disabled
                )
            except Exception:
                pass
            return {"ok": False, "err": str(exc)[:180], "rolled_back": rolled_back}


def clear_proxy_pin():
    """Proxy sabit hesap tercihini kaldirir ve serbest dagitim (round-robin / pool) moduna gecer."""
    with PROXY_SWITCH_LOCK:
        before = proxy_snapshot()
        st, _ = proxy_api("POST", "/api/proxy/preferred-account", {"accountId": None})
        if st != 200:
            return {"ok": False, "err": "pin kaldirilamadi (HTTP %s)" % st}

        # B5: Pin kaldırma sonucunu readback ile doğrula
        after = proxy_snapshot()
        if not after.get("ok") or _preferred_id(after.get("preferred")) is not None:
            return {"ok": False, "err": "pin kaldirma dogrulanamadi, tercih hala secili"}

        # S2: Scheduler durumunu pool moduna gecir
        _update_scheduler_state(mode="pool", manual_pin=None, ours=None)

        # B6: History hatası pin kaldırmanın başarılı sonucunu bozmamalıdır
        if history_store:
            try:
                prev_acc = next((a for a in before.get("accounts", []) if a.get("id") == before.get("current_account_id")), None)
                from_email = prev_acc.get("email") if prev_acc else None
                history_store.record_switch(
                    to_id=None,
                    to_email="Tüm Hesaplar (Havuz)",
                    from_id=before.get("current_account_id"),
                    from_email=from_email,
                    trigger="round_robin",
                    reason="Sabit pin kaldırıldı; istekler havuzdaki hesaplara dengeli dağıtılıyor",
                )
            except Exception as h_err:
                print(f"[clear_proxy_pin] Gecmis kaydetme uyarisi: {h_err}", file=sys.stderr)
        return {"ok": True, "mode": "pool"}


def set_proxy_auto():
    """Otomatik zamanlayıcı moduna doner."""
    with PROXY_SWITCH_LOCK:
        _update_scheduler_state(mode="auto", manual_pin=None)
        if history_store:
            try:
                history_store.record_switch(
                    trigger="system",
                    reason="Otomatik hesap seçimi moduna dönüldü",
                )
            except Exception:
                pass
        return {"ok": True, "mode": "auto"}

def rotate_proxy_account():
    snapshot = proxy_snapshot()
    if not snapshot.get("ok"):
        return {"ok": False, "err": "Antigravity proxy API erisilemiyor"}
    enabled = [
        a for a in snapshot.get("accounts", [])
        if not a.get("proxy_disabled") and not a.get("disabled")
        and not a.get("validation_blocked") and a.get("id")
    ]
    enabled.sort(key=lambda a: ((a.get("email") or "").lower(), a["id"]))
    if not enabled:
        return {"ok": False, "err": "kullanilabilir proxy hesabi yok"}
    current = snapshot.get("current_account_id") or _preferred_id(snapshot.get("preferred"))
    current_index = next(
        (i for i, account in enumerate(enabled) if account["id"] == current), -1
    )
    return switch_proxy_account(
        enabled[(current_index + 1) % len(enabled)]["id"],
        trigger="rotate",
        reason="Panel üzerinden sonraki proxy hesabına geçiş yapıldı (rotasyon)",
    )

# ---------------------------------------------------------------- login flow
class LoginFlow:
    """Tek aktif giris oturumu (bir profil). PTY'yi yonetir, URL uretir, kodu besler."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.active = False
        self.generation = 0
        self._finalized = False
        self.profile = None
        self.want_email = None
        self.master = None
        self.pid = None
        self.url = None
        self.url_ts = 0.0
        self.phase = "idle"  # idle|menu|waiting-code|success|error|stopped
        self.note = ""
        self.lines = []
        self._raw = ""
        self._errors = 0
        self._auto = True
        self._creds0 = 0.0
        self._thread = None
        self._menu_t = None
        self._pushes = 0
        self._result = None  # {ok, email}

    def _mt(self):
        for cand in (os.path.join(profile_gem_dir(self.profile), "antigravity-cli", "antigravity-oauth-token"),
                     os.path.join(profile_gem_dir(self.profile), "oauth_creds.json")):
            try:
                return os.stat(cand).st_mtime
            except OSError:
                pass
        return 0.0

    def start(self, profile_dir, email):
        with self.lock:
            if self.active:
                return False, "baska bir giris oturumu aktif"
            self.active = True
            self.generation += 1
            gen = self.generation
            self._finalized = False
            self.profile = profile_dir
            self.want_email = (email or "").lower() or None
            self.url = None
            self.url_ts = 0.0
            self.phase = "menu"
            self.note = "agy baslatiliyor..."
            self.lines = []
            self._raw = ""
            self._errors = 0
            self._auto = True
            self._result = None
            self._creds0 = self._mt()
        # B9: Kurulum hatalarinda active kilidini temizle
        try:
            os.makedirs(profile_dir, mode=0o700, exist_ok=True)
            os.makedirs(profile_gem_dir(profile_dir), exist_ok=True)
            pid, master = pty.fork()
            if pid == 0:
                env = dict(os.environ)
                env["HOME"] = profile_dir
                env["PATH"] = child_path()
                env["TERM"] = "xterm-256color"
                try:
                    os.execve(AGY_BIN, [AGY_BIN, "--model", MODEL], env)
                except Exception:
                    os._exit(127)
            try:
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 200, 0, 0))
            except OSError:
                pass
            with self.lock:
                self.pid = pid
                self.master = master
            self._thread = threading.Thread(target=self._run, args=(gen, master, pid), daemon=True)
            self._thread.start()
            return True, "baslatildi"
        except Exception as exc:
            with self.lock:
                self.active = False
                self.phase = "error"
                self.note = f"baslatma hatasi: {exc}"
            return False, f"baslatma hatasi: {exc}"

    def _write(self, s):
        try:
            with self.lock:
                if self.master is not None:
                    os.write(self.master, s.encode())
        except OSError:
            pass

    def new_link(self):
        """Taze URL: su anki sureci kapatip ayni profille yeniden baslat."""
        with self.lock:
            prof, mail = self.profile, self.want_email
        if not prof:
            return
        self.stop()
        time.sleep(0.6)
        self.start(prof, mail or "")
        with self.lock:
            self.note = "yeni link uretiliyor..."

    def submit_code(self, code):
        code = (code or "").strip()
        if code:
            self._write(code + "\r")
            with self.lock:
                self.note = "kod gonderildi, exchange bekleniyor..."

    def stop(self):
        with self.lock:
            pid = self.pid
            self.pid = None
            self.active = False
            if self.phase not in ("success",):
                self.phase = "stopped"
                self.note = "durduruldu"
        if pid:
            threading.Thread(target=self._kill_tree, args=(pid,), daemon=True).start()

    def _run(self, gen, master_fd, pid):
        """B8: Generation kontrollü PTY okuma döngüsü."""
        while True:
            try:
                data = os.read(master_fd, 8192)
            except OSError:
                break
            if not data:
                break
            with self.lock:
                if self.generation != gen:
                    break
            self._consume(data.decode("utf-8", "replace"))

        with self.lock:
            # B8: Sadece bu generation hala aktifse state'i güncelle
            if self.generation == gen:
                if self.active and self.phase not in ("success",):
                    self.phase = "stopped"
                    self.note = "süreç sonlandı"
                self.active = False
                if self.master == master_fd:
                    self.master = None
                if self.pid == pid:
                    self.pid = None

        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    @staticmethod
    def _kill_tree(pid):
        """pty.fork child kendi pgid+sid'sinin lideri."""
        def dead():
            try:
                r, _ = os.waitpid(pid, os.WNOHANG)
                if r == pid:
                    return True
            except ChildProcessError:
                return True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            return False
        for sig, tries in ((signal.SIGTERM, 15), (signal.SIGKILL, 20)):
            try:
                os.killpg(pid, sig)
            except OSError:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            for _ in range(tries):
                if dead():
                    return
                time.sleep(0.1)

    def _menu_push(self):
        """Menu gorunurken Enter'a basmaya devam et."""
        for _ in range(14):
            time.sleep(1.5)
            with self.lock:
                if self.phase != "menu" or not self.active:
                    return
                self._pushes += 1
            self._write("\r")

    def _consume(self, text):
        clean = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
        should_finalize = False
        with self.lock:
            for ln in clean.split("\n"):
                ln = ln.rstrip()
                if ln:
                    self.lines.append(ln)
            if len(self.lines) > 160:
                self.lines = self.lines[-160:]
            self._raw = (self._raw + text)[-40000:]
            compact = re.sub(r"[\s\x00-\x1f\x7f]+", "", self._raw)
            ms = URL_RE.findall(compact)
            if ms and ms[-1] != self.url:
                self.url = ms[-1]
                self.url_ts = time.time()
                self.phase = "waiting-code"
            low = clean.lower()
            if "select login method" in low and self._auto:
                self.phase = "menu"
                if not self._menu_t or not self._menu_t.is_alive():
                    self._menu_t = threading.Thread(target=self._menu_push, daemon=True)
                    self._menu_t.start()
            if "token exchange failed" in low or "invalid code verifier" in low:
                self._errors += 1
                self.note = "exchange başarısız — eski/bayat kod. 'Yeni Link' ile taze URL al."
                if self._errors >= 3:
                    self._auto = False
                    self.phase = "error"
            if "press any key" in low and self._auto:
                threading.Timer(0.7, self._write, ["\r"]).start()
            # B7: signed in algılandığında finalize tetikle
            if "signed in" in low:
                should_finalize = True

        if should_finalize or self._mt() > self._creds0:
            self._finish_success()

    def _finish_success(self):
        """B7: Idempotent başarı finalizasyonu — OMP import ve child cleanup."""
        with self.lock:
            if self._finalized:
                return
            self._finalized = True
            self.phase = "success"
            self.note = "giris tamamlandi, OMP aktarimi yapiliyor..."
        prof = self.profile
        email = self.want_email
        try:
            d = read_json(os.path.join(profile_gem_dir(prof), "antigravity-cli", "antigravity-oauth-token")) or {}
            e = idt_email(d.get("id_token", ""))
            if e:
                email = e.lower()
        except Exception:
            pass
        for cand in (os.path.join(profile_gem_dir(prof), "antigravity-cli", "antigravity-oauth-token"),
                     os.path.join(profile_gem_dir(prof), "oauth_creds.json")):
            try:
                os.chmod(cand, 0o600)
            except OSError:
                pass
        imp = omp_import(prof, email)
        with self.lock:
            self._result = {"ok": True, "email": email, "omp_import": imp}
            self.note = ("giris tamamlandi" + (" · OMP'ye aktarildi" if imp.get("ok") else " · OMP aktarim: " + str(imp.get("err", "?"))))
            self.phase = "success"
        self.stop()

    def state(self):
        with self.lock:
            return {
                "active": self.active,
                "phase": self.phase,
                "note": self.note,
                "email": self.want_email,
                "url": self.url,
                "url_age": round(time.time() - self.url_ts) if self.url else None,
                "errors": self._errors,
                "result": self._result,
                "log": "\n".join(self.lines[-30:]),
            }


FLOW = LoginFlow()


# ---------------------------------------------------------------- OMP import
def omp_import(profile_dir, email):
    """
    Profilin CLI token'ini OMP vault'a aktar.
    B10: Hem antigravity-oauth-token hem oauth_creds.json formatlarini destekler.
    """
    try:
        gem = existing_profile_gem_dir(profile_dir)
    except (ValueError, OSError) as exc:
        return {"ok": False, "err": str(exc)[:180]}

    newtok = os.path.join(gem, "antigravity-cli", "antigravity-oauth-token")
    oldtok = os.path.join(gem, "oauth_creds.json")

    access_token = None
    refresh_token = None
    expiry = None
    id_tok = None

    if os.path.isfile(newtok):
        d = read_json(newtok)
        if isinstance(d, dict):
            id_tok = d.get("id_token")
            t = d.get("token")
            if isinstance(t, dict):
                access_token = t.get("access_token")
                refresh_token = t.get("refresh_token")
                expiry = t.get("expiry")

    if not access_token and os.path.isfile(oldtok):
        d = read_json(oldtok)
        if isinstance(d, dict):
            id_tok = id_tok or d.get("id_token")
            access_token = d.get("access_token")
            refresh_token = d.get("refresh_token")
            if d.get("expiry_date"):
                try:
                    expiry = datetime.fromtimestamp(d["expiry_date"] / 1000.0, tz=timezone.utc).isoformat()
                except Exception:
                    expiry = str(d.get("expiry_date"))

    if not access_token or not refresh_token:
        return {"ok": False, "err": "token dosyasi yok veya gecersiz (refresh_token eksik)"}

    if not email and id_tok:
        try:
            detected = idt_email(id_tok)
            if detected:
                email = detected.lower()
        except Exception:
            pass

    flat = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expired": expiry or datetime.now(timezone.utc).isoformat(),
    }
    if email:
        flat["email"] = email
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(flat, f)
        os.chmod(tmp, 0o600)
        r = subprocess.run([OMP_BIN, "auth-broker", "import", tmp,
                            "--provider", "google-antigravity"],
                           capture_output=True, text=True, timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        result = {"ok": r.returncode == 0, "out": out[-300:]}
        if r.returncode != 0:
            result["err"] = "OMP aktarimi basarisiz (cikis %s)" % r.returncode
        return result
    except Exception as e:
        return {"ok": False, "err": str(e)[:120]}
    finally:
        try:
            if tmp is not None:
                os.unlink(tmp)
        except OSError:
            pass


def omp_test(profile_dir):
    """Izole agy -p ile gercek cikarim testi (sir sifir)."""
    try:
        gem = existing_profile_gem_dir(profile_dir)
    except (ValueError, OSError) as exc:
        return {"ok": False, "status": "error", "err": str(exc)[:180]}
    env = dict(os.environ)
    env["HOME"] = os.path.dirname(gem)
    env["PATH"] = child_path()
    try:
        r = subprocess.run([AGY_BIN, "--model", MODEL, "-p", "Sadece OK yaz"],
                           env=env, capture_output=True, text=True, timeout=90)
        out = (r.stdout or r.stderr or "").strip()
        low = out.lower()
        status = "ok" if r.returncode == 0 and "ok" in low else \
            ("rate_limited" if "429" in low or "exhaust" in low else "error")
        result = {"ok": status == "ok", "status": status, "exit": r.returncode, "out": out[-200:]}
        if status != "ok":
            result["err"] = "CLI cikarim testi basarisiz (%s, cikis %s)" % (status, r.returncode)
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "err": "CLI cikarim testi zaman asimina ugradi"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "status": "error", "err": str(exc)[:180]}


def sanitize_profile(email):
    local = (email.split("@")[0] if "@" in email else email) or "hesap"
    local = re.sub(r"[^a-z0-9._-]", "_", local.lower())[:40] or "hesap"
    base = local
    i = 2
    while os.path.exists(os.path.join(PROFILES_ROOT, local)):
        local = "%s%d" % (base, i)
        i += 1
    return local

def omp_unblock(email):
    """OMP vault'taki gecici 429 karantina bloklarini kaldirir (counter:*)."""
    try:
        db = sqlite3.connect(AGENT_DB)
        cur = db.execute(
            "DELETE FROM auth_credential_blocks WHERE provider_key='google-antigravity:oauth' "
            "AND block_scope LIKE 'counter:%' "
            "AND credential_id IN (SELECT id FROM auth_credentials WHERE provider='google-antigravity' "
            "AND lower(json_extract(data,'$.email'))=lower(?))", (email or "",))
        n = cur.rowcount
        db.commit()
        db.close()
        return {"ok": True, "cleared": n}
    except Exception as e:
        return {"ok": False, "err": str(e)[:120]}


PUBLIC_FILES = {
    "/": ("panel.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/offline.html": ("offline.html", "text/html; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}


# İzinli Host/Origin kümesi yalnız gerçekten dinlenen adreslerden türetilir.
ALLOWED_HOSTS = {a.lower() for a in BIND_ADDRESSES} | {"localhost"}
MAX_BODY_BYTES = 65536  # 64 KB


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _verify_origin_and_host(self):
        """B1 & B12: Origin, Host ve CSRF sınırlarını doğrular."""
        # 1. Host header doğrulaması
        host = self.headers.get("host", "").split(":")[0].strip().lower()
        if host and host not in ALLOWED_HOSTS:
            self._json({"ok": False, "err": "Erisim engellendi: Gecersiz Host"}, 403)
            return False

        # 2. Sec-Fetch-Site kontrolü (Cross-Site istekleri reddet)
        sec_fetch = self.headers.get("sec-fetch-site", "").strip().lower()
        if sec_fetch == "cross-site":
            self._json({"ok": False, "err": "Erisim engellendi: Cross-site istek"}, 403)
            return False

        # 3. Origin doğrulaması
        origin = self.headers.get("origin")
        if origin:
            parsed = urlparse.urlsplit(origin)
            origin_host = (parsed.hostname or "").lower()
            if origin_host not in ALLOWED_HOSTS:
                self._json({"ok": False, "err": "Erisim engellendi: Gecersiz Origin"}, 403)
                return False

        return True

    def _body(self):
        """B12: Güvenli, sınırlı ve doğrulanmış JSON gövde ayrıştırması."""
        len_header = self.headers.get("content-length")
        if not len_header:
            return {}
        try:
            n = int(len_header)
        except (ValueError, TypeError):
            raise ValueError("Gecersiz Content-Length degeri")
        if n < 0:
            raise ValueError("Negatif Content-Length kabul edilmez")
        if n > MAX_BODY_BYTES:
            raise ValueError(f"Govde boyutu siniri asildi (maksimum {MAX_BODY_BYTES} bayt)")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            raise ValueError(f"Gecersiz JSON verisi: {str(e)[:100]}")
        if not isinstance(parsed, dict):
            raise ValueError("JSON govdesi nesne (dict) olmalidir")
        return parsed

    def do_GET(self):
        try:
            if not self._verify_origin_and_host():
                return
            self._get()
        except Exception as e:
            self._fail(e)

    def _get(self):
        parsed_url = urlparse.urlsplit(self.path)
        path = parsed_url.path
        if path in PUBLIC_FILES:
            filename, content_type = PUBLIC_FILES[path]
            try:
                with open(os.path.join(PROFILES_ROOT, filename), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(503, "Panel asset unavailable")
                return
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-cache")
            self.send_header("x-content-type-options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/accounts":
            self._json(scan_accounts())
        elif path == "/api/proxy/status":
            self._json(_public_proxy_state())
        elif path == "/api/flow":
            self._json(FLOW.state())
        elif path == "/healthz":
            self._json({"ok": True})
        elif path == "/api/switch-history":
            qs = urlparse.parse_qs(parsed_url.query)
            limit_val = qs.get("limit", [100])[0]
            try:
                limit = int(limit_val)
                if limit <= 0:
                    limit = 100
                elif limit > 500:
                    limit = 500
            except (ValueError, TypeError):
                limit = 100
            trigger = qs.get("trigger", [None])[0]
            family = qs.get("family", [None])[0]
            search = qs.get("search", [None])[0]
            if history_store:
                self._json(history_store.get_history(limit=limit, trigger=trigger, family=family, search=search))
            else:
                self._json({"ok": True, "total": 0, "history": [], "items": []})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            if not self._verify_origin_and_host():
                return
            self._post()
        except ValueError as ve:
            self._json({"ok": False, "err": str(ve)}, 400)
        except Exception as e:
            self._fail(e)

    def _fail(self, e):
        traceback.print_exc()
        try:
            self._json({"ok": False, "err": "sunucu hatasi: " + str(e)[:200]}, 500)
        except Exception:
            pass

    def _post(self):
        # Mutasyon isteklerinde Content-Type kontrolü
        ct = self.headers.get("content-type", "").lower()
        content_len = int(self.headers.get("content-length") or 0)
        if content_len > 0 and not ct.startswith("application/json"):
            self._json({"ok": False, "err": "Content-Type application/json olmalidir"}, 415)
            return

        b = self._body()
        path = urlparse.urlsplit(self.path).path

        if path == "/api/login":
            email = (b.get("email") or "").strip()
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
                self._json({"ok": False, "err": "gecersiz e-posta"}, 400)
                return
            name = sanitize_profile(email)
            pdir = os.path.join(PROFILES_ROOT, name)
            ok, msg = FLOW.start(pdir, email)
            self._json({"ok": ok, "err": None if ok else msg})
        elif path == "/api/newlink":
            FLOW.new_link()
            self._json({"ok": True})
        elif path == "/api/stop":
            FLOW.stop()
            self._json({"ok": True})
        elif path == "/api/code":
            code = b.get("code")
            if not code or not isinstance(code, str):
                self._json({"ok": False, "err": "Gecersiz veya eksik dogrulama kodu"}, 400)
                return
            FLOW.submit_code(code)
            self._json({"ok": True})
        elif path == "/api/test":
            self._json(omp_test(b.get("home")))
        elif path == "/api/import":
            self._json(omp_import(b.get("home"), b.get("email")))
        elif path == "/api/unblock":
            self._json(omp_unblock(b.get("email")))
        elif path in ("/api/proxy/switch", "/api/switch-account"):
            trigger = b.get("trigger", "manual")
            if not isinstance(trigger, str) or trigger not in ("manual", "scheduler", "rotate", "round_robin", "system", "warmup", "pin_clear"):
                trigger = "manual"
            self._json(switch_proxy_account(
                b.get("account_id"),
                email=b.get("email"),
                trigger=trigger,
                reason=b.get("reason"),
            ))
        elif path == "/api/proxy/rotate":
            self._json(rotate_proxy_account())
        elif path in ("/api/proxy/clear-pin", "/api/proxy/round-robin"):
            self._json(clear_proxy_pin())
        elif path == "/api/proxy/auto":
            self._json(set_proxy_auto())
        else:
            self.send_response(404)
            self.end_headers()


def serve():
    servers = []
    bound = []
    for addr in BIND_ADDRESSES:
        try:
            s = ThreadingHTTPServer((addr, PORT), H)
            s.daemon_threads = True
            threading.Thread(target=s.serve_forever, daemon=True).start()
            servers.append(s)
            bound.append(f"{addr}:{PORT}")
        except OSError as e:
            print(f"bind fail {addr}:{PORT} {e}", flush=True)
    if not servers:
        print("HATA: Hicbir adrese baglanilamadi, agyauth sonlandiriliyor", flush=True)
        sys.exit(1)
    print(f"agyauth listening on {' + '.join(bound)}", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    serve()
