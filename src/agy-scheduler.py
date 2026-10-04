#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agy-scheduler — Antigravity Tools icin otomatik hesap secimi + haftalik reset warmup.

Politika (elle pin secilmedigi surece):
  1) Istenen modele gore aile secilir: gemini-* -> "Gemini Models", digerleri -> "Claude and GPT models".
  2) O ailede haftalik reset suresi EN YAKIN olan hesap onceliklidir.
  3) Reset suresi esitse kalan kotasi DAHA AZ olan hesap secilir.
  4) 5 saatlik kovasi tukenmis hesap atlanir; sira bir sonraki en yakin resete gecer.
  5) Haftalik penceresi bitmis hesaba (reset_time gecmiste) warmup istegi gonderilir; boylece
     yeni 7 gunluk sayac baslar.

Routing Modlari (Ortak Durum: agy-scheduler-state.json):
  - "auto": Scheduler en uygun hesabi secer ve runtime pin belirler.
  - "manual": Kullanici panelden elle bir hesap secmistir; scheduler bu pini ezmez.
  - "pool": Kullanici pini kaldirmis, serbest dagitim istemistir; scheduler pin atmaz.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zoneinfo
from datetime import datetime, timezone

WARSAW_TZ = zoneinfo.ZoneInfo("Europe/Warsaw")

PROFILES_ROOT = os.environ.get(
    "AGY_PROFILES_ROOT", os.path.expanduser("~/.antigravity-profiles")
)
TOOLS_ROOT = os.environ.get(
    "AGY_TOOLS_ROOT", os.path.expanduser("~/.antigravity_tools")
)
if PROFILES_ROOT not in sys.path:
    sys.path.insert(0, PROFILES_ROOT)
try:
    import history_store
except ImportError:
    history_store = None

GUI_CONFIG = os.environ.get(
    "AGY_TOOLS_CONFIG", os.path.join(TOOLS_ROOT, "gui_config.json")
)
LOG_DIR = os.environ.get("AGY_LOG_DIR", os.path.join(TOOLS_ROOT, "logs"))
STATE_PATH = os.environ.get(
    "AGY_STATE_PATH", os.path.join(PROFILES_ROOT, "agy-scheduler-state.json")
)
PAUSE_PATH = os.environ.get(
    "AGY_PAUSE_PATH", os.path.join(PROFILES_ROOT, "agy-scheduler.pause")
)
API = os.environ.get("AGY_PROXY_API", "http://127.0.0.1:8045")

MIN_WEEKLY_REMAINING = 0.01  # haftalik kalan bu orandan azsa hesap "tukendi" sayilir
MIN_5H_REMAINING = 0.01      # 5 saatlik kova tukendiyse sira sonraki hesaba gecer
WARMUP_COOLDOWN_S = 12 * 3600
ACTIVE_FAMILY_WINDOW_S = 20 * 60
POLL_S = 20

GROUP_PREFIX = {"gemini": "gemini", "claude": "3p"}
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.+-]+).*?Model: ([A-Za-z0-9._:\-/]+)")


def format_warsaw_time(dt_or_str, fmt="%d.%m %H:%M"):
    if not dt_or_str:
        return "belirsiz"
    if isinstance(dt_or_str, str):
        dt = parse_ts(dt_or_str)
    else:
        dt = dt_or_str
    if not dt:
        return "belirsiz"
    # S4: Yil siniri kontrolu (overflow onleme)
    if hasattr(dt, "year") and dt.year >= 2099:
        return "belirsiz"
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(WARSAW_TZ).strftime(fmt)
    except (OverflowError, ValueError):
        return "belirsiz"


def log(msg):
    print("%s %s" % (datetime.now(WARSAW_TZ).strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def api_key():
    try:
        with open(GUI_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh)["proxy"]["api_key"]
    except Exception:
        return ""


def request(method, path, body=None, timeout=15):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method)
    k = api_key()
    if k:
        req.add_header("Authorization", "Bearer " + k)
    if data is not None:
        req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def parse_ts(value):
    """S9: UTC-Z ve timezone ofsetlerini standart UTC datetime nesnesine donusturur."""
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def family_of(model):
    m = str(model).lower()
    return "gemini" if m.startswith("gemini") or "gemini" in m else "claude"


def bucket_of(quota, family, window):
    prefix = GROUP_PREFIX.get(family, "")
    for group in quota.get("quota_groups") or []:
        for bucket in group.get("buckets") or []:
            b_id = str(bucket.get("bucket_id", ""))
            if (b_id.startswith(prefix + "-") or b_id == prefix) and bucket.get("window") == window:
                return bucket
    return None


def family_view(account, family):
    """(haftalik kalan oran, haftalik reset, 5h kalan oran) dondurur; veri yoksa None."""
    quota = account.get("quota") or {}
    weekly = bucket_of(quota, family, "weekly")
    fiveh = bucket_of(quota, family, "5h")
    if weekly is not None:
        remaining = weekly.get("remaining_fraction")
        reset = parse_ts(weekly.get("reset_time"))
    else:
        models = [m for m in quota.get("models") or [] if family_of(m.get("name")) == family]
        if not models:
            return None
        remaining = min((m.get("percentage") or 0) for m in models) / 100.0
        resets = [parse_ts(m.get("reset_time")) for m in models]
        resets = [r for r in resets if r]
        reset = min(resets) if resets else None
    if remaining is None:
        return None
    fiveh_remaining = fiveh.get("remaining_fraction") if fiveh else None
    return float(remaining), reset, (None if fiveh_remaining is None else float(fiveh_remaining))


def pick_account(accounts, family):
    """
    Politika: en yakin haftalik reset; esitlikte en az kalan kota.
    S4: reset_time None ise datetime.max overflow olmadan sona alinir.
    S5: proxy_disabled olan hesaplar secim disi birakilir.
    """
    scored = []
    skipped = []
    safe_sentinel = datetime(2099, 1, 1, tzinfo=timezone.utc)

    for account in accounts:
        # S5: proxy_disabled kontrolu
        if account.get("disabled") or account.get("proxy_disabled") or account.get("validation_blocked"):
            skipped.append("%s:devre disi" % account.get("email", "bilinmeyen"))
            continue
        view = family_view(account, family)
        if view is None:
            skipped.append("%s:veri yok" % account.get("email", "bilinmeyen"))
            continue
        remaining, reset, fiveh_remaining = view
        if remaining <= MIN_WEEKLY_REMAINING:
            skipped.append("%s:haftalik bitti" % account.get("email", "bilinmeyen"))
            continue
        if fiveh_remaining is not None and fiveh_remaining <= MIN_5H_REMAINING:
            skipped.append("%s:5h bitti" % account.get("email", "bilinmeyen"))
            continue

        # S4: reset None ise en sona koy, overflow olmadan sirala
        has_reset = 0 if reset is not None else 1
        sort_reset = reset if reset is not None else safe_sentinel
        scored.append((has_reset, sort_reset, remaining, account, reset))

    if not scored:
        return None, "uygun hesap yok (%s)" % ", ".join(skipped)

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    _, _, remaining, account, original_reset = scored[0]
    reset_str = format_warsaw_time(original_reset, "%d.%m %H:%M")
    reason = "reset=%s kalan=%.1f%%" % (reset_str, remaining * 100)
    if skipped:
        reason += " | atlanan: " + ", ".join(skipped)
    return account, reason


def active_family():
    """
    Proxy log kuyrugundan son 20 dakikadaki isteklerin cogunluk ailesini dondurur.
    S9: Gece yarisi log rotasyonunda son 2 log dosyasini birlestirir;
    gelecek tarihli satirlari eler (0 <= age <= 20dk).
    """
    try:
        names = sorted(f for f in os.listdir(LOG_DIR) if f.startswith("app.log."))
    except OSError:
        return None
    if not names:
        return None

    # Son iki dosya (rotasyon desteği)
    target_files = names[-2:] if len(names) >= 2 else names[-1:]
    raw_lines = ""
    for name in target_files:
        path = os.path.join(LOG_DIR, name)
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                fh.seek(max(0, size - 250_000))
                raw_lines += fh.read().decode("utf-8", "replace") + "\n"
        except OSError:
            continue

    matches = TS_RE.findall(raw_lines)
    if not matches:
        return None

    now = datetime.now(timezone.utc)
    recent = []
    for stamp, model in matches:
        ts = parse_ts(stamp)
        if ts is not None:
            age = (now - ts).total_seconds()
            # S9: Sadece 0 <= age <= 20 dk araligini kabul et (gelecek loglari ele)
            if 0 <= age <= ACTIVE_FAMILY_WINDOW_S:
                recent.append((ts, family_of(model)))

    if not recent:
        return None

    counts = {}
    for _, fam in recent:
        counts[fam] = counts.get(fam, 0) + 1
    top = max(counts.values())
    winners = [fam for fam, cnt in counts.items() if cnt == top]
    return winners[0] if len(winners) == 1 else recent[-1][1]


def load_state():
    """S8: Guvenli varsayilanlarla state yukleme ve sema dogrulama."""
    default_state = {
        "mode": "auto",
        "ours": None,
        "manual_pin": None,
        "family": "gemini",
        "warmup_seen": {},
        "warmup_at": {},
    }
    if not os.path.exists(STATE_PATH):
        return default_state
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return default_state
        for k, v in default_state.items():
            if k not in data or data[k] is None and k in ("warmup_seen", "warmup_at"):
                data[k] = v
        if not isinstance(data.get("warmup_seen"), dict):
            data["warmup_seen"] = {}
        if not isinstance(data.get("warmup_at"), dict):
            data["warmup_at"] = {}
        return data
    except Exception:
        return default_state


def save_state(state):
    """S8: Atomik state kaydetme."""
    tmp = f"{STATE_PATH}.tmp.{os.getpid()}_{time.time_ns()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_PATH)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def warmup_scan(accounts, state, dry):
    """
    S6: Haftalik penceresi bitmis hesaplara warmup gonderir.
    - Sadece reset <= now oldugunda tetiklenir (gelecekteki reset degisimi tetiklemez).
    - validation_blocked veya proxy_disabled hesaplar atlanir.
    - S6: Cooldown ve state sadece basarili POST sonrasinda kaydedilir.
    """
    now = datetime.now(timezone.utc)
    for account in accounts:
        if account.get("disabled") or account.get("proxy_disabled") or account.get("validation_blocked"):
            continue
        due = []
        for family in ("claude", "gemini"):
            view = family_view(account, family)
            if view is None:
                continue
            _, reset, _ = view
            if reset is None:
                continue
            key = "%s|%s" % (account.get("id"), family)
            seen = state["warmup_seen"].get(key)
            # S6: Yalnizca reset suresi gercekten dolmussa warmup gonder
            if reset <= now:
                due.append((family, key, reset))
            else:
                state["warmup_seen"][key] = reset.isoformat()

        if not due:
            continue

        acc_id = account.get("id")
        last_warmup = state["warmup_at"].get(acc_id, 0)
        if time.time() - last_warmup < WARMUP_COOLDOWN_S:
            continue

        detail = ", ".join("%s reset=%s" % (fam, format_warsaw_time(rst, "%d.%m %H:%M")) for fam, _, rst in due)
        if dry:
            log("warmup(dry) %s — %s" % (account.get("email"), detail))
            continue

        try:
            result = request("POST", "/api/accounts/%s/warmup" % acc_id, {})
            # S6: Basari sonrasinda cooldown ve seen guncelle
            state["warmup_at"][acc_id] = time.time()
            for _, key, reset in due:
                state["warmup_seen"][key] = reset.isoformat()

            log("warmup %s — %s -> %s" % (account.get("email"), detail, str(result)[:80]))
            if history_store:
                history_store.record_switch(
                    to_id=acc_id,
                    to_email=account.get("email"),
                    trigger="warmup",
                    reason="Haftalık reset penceresi dolan hesap (%s) için yeni periyot sayacı tetiklendi (%s)" % (account.get("email"), detail),
                    raw="warmup %s — %s" % (account.get("email"), detail),
                )
        except Exception as exc:  # noqa: BLE001 - servis asla olmemeli
            log("warmup HATA %s: %s" % (account.get("email"), exc))


def cycle(state, forced_family=None, dry=False):
    """
    S1 & S2: Tek tur koordinasyonu:
    - mode == "pool": Serbest dagitim; scheduler asla pin atmaz.
    - mode == "manual": Kullanici secimi; scheduler pini degistirmez.
    - mode == "auto": En uygun hesabi hesaplar, gerekirse pini degistirir ve teyit eder.
    - S3: dry-run sirasinda hicbir sey diske veya ag POST'a yazilmaz.
    """
    accounts = request("GET", "/api/accounts")
    if isinstance(accounts, dict):
        accounts = accounts.get("accounts") or []
    if not accounts:
        log("hesap listesi bos; atlandi")
        return state

    if os.path.exists(PAUSE_PATH):
        log("DURAKLATILDI (%s var) — pin yonetilmiyor" % PAUSE_PATH)
        warmup_scan(accounts, state, dry)
        return state

    pin = request("GET", "/api/proxy/preferred-account")
    pin = pin if isinstance(pin, str) else None

    # S2: Havuz Modu kontrolü
    current_mode = state.get("mode", "auto")
    if current_mode == "pool":
        if pin is not None:
            # Proxy'de hala pin kalmissa temizle
            if not dry:
                request("POST", "/api/proxy/preferred-account", {"accountId": None})
        log("havuz modu aktif (serbest dagitim) — pin belirlenmiyor")
        warmup_scan(accounts, state, dry)
        return state

    # S1: Manuel Mod kontrolü
    if current_mode == "manual":
        manual_pin = state.get("manual_pin")
        if pin is not None and manual_pin is None:
            state["manual_pin"] = pin
        log("manuel pin modu aktif (%s) — otomatik secim duraklatildi" % (state.get("manual_pin", pin) or "belirsiz")[:8])
        warmup_scan(accounts, state, dry)
        return state

    # Dışarıdan elle pin seçilmişse algıla (S1)
    ours = state.get("ours")
    if pin is not None and ours is not None and pin != ours:
        log("harici manuel pin algilandi (%s) — manuel moda gecildi" % pin[:8])
        state["mode"] = "manual"
        state["manual_pin"] = pin
        if history_store and not dry:
            history_store.record_switch(
                to_id=pin,
                from_id=ours,
                trigger="manual",
                reason="Kullanıcı tercihi algılandı: Panelden elle hesap seçildi",
                raw="elle pin algilandi (%s)" % pin[:8],
            )
        warmup_scan(accounts, state, dry)
        return state

    # Otomatik Mod: Aile ve hesap tespiti
    family = forced_family or active_family() or state.get("family") or "gemini"
    state["family"] = family
    account, reason = pick_account(accounts, family)

    if account is None:
        log("aile=%s %s — pin degistirilmedi" % (family, reason))
    else:
        wanted = account.get("id")
        if wanted != pin:
            if dry:
                log("pin(dry) aile=%s -> %s (%s) | %s" % (family, account.get("email"), wanted[:8], reason))
            else:
                # S7: Pin commit ve dogrulama readback'i
                request("POST", "/api/proxy/preferred-account", {"accountId": wanted})
                confirm = request("GET", "/api/proxy/preferred-account")
                confirm = confirm if isinstance(confirm, str) else None
                if confirm == wanted:
                    state["ours"] = wanted
                    state["mode"] = "auto"
                    state["manual_pin"] = None
                    log("pin aile=%s -> %s (%s) | %s" % (family, account.get("email"), wanted[:8], reason))
                    if history_store:
                        history_store.record_switch(
                            to_id=wanted,
                            to_email=account.get("email"),
                            from_id=pin,
                            trigger="scheduler",
                            family=family,
                            reason="Otomatik Seçim: %s ailesi için %s" % (family.title(), reason),
                            raw="pin aile=%s -> %s (%s) | %s" % (family, account.get("email"), wanted[:8], reason),
                        )
                else:
                    log("pin onaylanamadi (beklenen: %s, gercek: %s)" % (wanted[:8], str(confirm)[:8]))
        else:
            state["ours"] = wanted
            state["mode"] = "auto"
            log("pin sabit aile=%s -> %s | %s" % (family, account.get("email"), reason))

    warmup_scan(accounts, state, dry)
    return state


def main(argv):
    dry = "--dry-run" in argv
    once = "--once" in argv
    forced_family = None
    if "--family" in argv:
        forced_family = argv[argv.index("--family") + 1]
    state = load_state()
    while True:
        try:
            state = cycle(state, forced_family, dry)
            if not dry:
                save_state(state)
        except Exception as exc:  # noqa: BLE001 - servis asla olmemeli
            log("tur hatasi: %s" % exc)
        if once:
            return
        time.sleep(POLL_S)


if __name__ == "__main__":
    main(sys.argv[1:])
