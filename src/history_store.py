#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Hesapları Geçiş ve Tercih Geçmişi (Switch History Store)
Antigravity Tools ve agyauth / agy-scheduler arasındaki tüm geçişleri
kronolojik, nedenleriyle birlikte saklar ve sunar.
Process-safe kilit (flock) ve atomik dosya değişimi ile çalışır.
"""

import os
import sys
import fcntl
import json
import time
import zoneinfo
import contextlib
from datetime import datetime, timezone

PROFILES_ROOT = os.environ.get(
    "AGY_PROFILES_ROOT", os.path.expanduser("~/.antigravity-profiles")
)
TOOLS_ROOT = os.environ.get(
    "AGY_TOOLS_ROOT", os.path.expanduser("~/.antigravity_tools")
)

WARSAW_TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
HISTORY_PATH = os.environ.get(
    "AGY_HISTORY_PATH", os.path.join(PROFILES_ROOT, "switch-history.json")
)
LOCK_PATH = os.environ.get(
    "AGY_HISTORY_LOCK", os.path.join(PROFILES_ROOT, "switch-history.lock")
)
ACCOUNTS_PATH = os.environ.get(
    "AGY_ACCOUNTS_PATH", os.path.join(TOOLS_ROOT, "accounts.json")
)
MAX_ENTRIES = int(os.environ.get("AGY_HISTORY_MAX", "500"))

ALLOWED_TRIGGERS = {
    "scheduler",
    "manual",
    "rotate",
    "round_robin",
    "system",
    "warmup",
    "pin_clear",
}

TRIGGER_LABELS = {
    "scheduler": "Otomatik Zamanlayıcı",
    "manual": "Manuel Tercih (Panel)",
    "rotate": "Sonraki Hesap (Rotasyon)",
    "round_robin": "Serbest Dağıtım (Havuz)",
    "system": "Sistem Başlangıcı",
    "warmup": "Haftalık Warmup",
    "pin_clear": "Pin Kaldırıldı",
}


@contextlib.contextmanager
def history_lock():
    """Tüm read-modify-write döngüsü boyunca process-safe kilit sağlar."""
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def _get_accounts_map():
    try:
        with open(ACCOUNTS_PATH) as f:
            data = json.load(f)
            return {a["id"]: a.get("email", "") for a in data.get("accounts", [])}
    except Exception:
        return {}


def load_history():
    """Geçmiş verisini yükler. Bozuk dosya tespit edilirse karantinaya alır."""
    if not os.path.exists(HISTORY_PATH):
        return {"version": "1.0", "history": []}
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {"version": "1.0", "history": []}
            return json.loads(content)
    except Exception as e:
        # H4: Bozuk geçmiş dosyasını sessizce ezme; karantinaya al
        corrupt_backup = f"{HISTORY_PATH}.corrupt.{int(time.time())}"
        try:
            if os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) > 0:
                os.replace(HISTORY_PATH, corrupt_backup)
                print(f"[history_store] Bozuk gecmis karantinaya alindi: {corrupt_backup} ({e})", file=sys.stderr)
        except Exception:
            pass
        return {"version": "1.0", "history": []}


def save_history(data):
    """Benzersiz geçici dosya ve fsync ile atomik yazma yapar."""
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp_path = f"{HISTORY_PATH}.tmp.{os.getpid()}_{time.time_ns()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, HISTORY_PATH)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def record_switch(
    to_id=None,
    to_email=None,
    from_id=None,
    from_email=None,
    trigger="scheduler",
    reason="",
    family=None,
    raw=None,
):
    """
    Yeni bir geçiş veya durum olayını geçmişe ekler.
    Tüm RMW (Read-Modify-Write) döngüsü process-safe lock altında yürütülür.
    """
    # B2: Trigger whitelist kontrolü
    if not isinstance(trigger, str) or trigger not in ALLOWED_TRIGGERS:
        trigger = "system"

    now = datetime.now(WARSAW_TZ)
    ts_iso = now.isoformat()
    epoch = int(now.timestamp())
    epoch_ms = int(now.timestamp() * 1000)
    acc_map = _get_accounts_map()

    if to_id and not to_email:
        to_email = acc_map.get(to_id, to_id[:8] if len(to_id) > 8 else to_id)
    if not to_id and not to_email:
        to_email = "Tüm Hesaplar (Havuz)"
        to_id = None

    with history_lock():
        data = load_history()
        history = data.get("history", [])

        # H2: from_id ve from_email çözümleme
        if from_id is not None and not from_email:
            from_email = acc_map.get(from_id, from_id[:8])
        elif from_id is None and from_email is None:
            # Warmup routing olayı sayılmaz; son gerçek yönlendirme olayını bul
            routing_events = [ev for ev in history if ev.get("trigger") != "warmup"]
            if routing_events:
                last_routing = routing_events[-1]
                from_email = last_routing.get("to_account", "Bilinmiyor")
                from_id = last_routing.get("to_id")
            else:
                from_email = "Başlangıç"
                from_id = None

        # H3: Dedupe - sadece 0 <= t_diff < 5 aralığında (saat geriye giderse kaybolmaz)
        if history:
            last = history[-1]
            t_diff = epoch - last.get("time_epoch", 0)
            if (
                0 <= t_diff < 5
                and last.get("to_account") == to_email
                and last.get("trigger") == trigger
            ):
                if reason and not last.get("reason"):
                    last["reason"] = reason
                save_history(data)
                return last

        # H3: Kapasite ve saniyeden bağımsız benzersiz ID
        entry_id = f"sw_{epoch}_{time.time_ns() % 1000000:06d}"
        entry = {
            "id": entry_id,
            "timestamp": ts_iso,
            "time_epoch": epoch,
            "time_ms": epoch_ms,
            "from_account": from_email,
            "from_id": from_id,
            "to_account": to_email,
            "to_id": to_id,
            "trigger": trigger,
            "trigger_label": TRIGGER_LABELS.get(trigger, trigger.title()),
            "family": family,
            "reason": reason or "Hesap tercihi güncellendi",
            "raw": raw,
        }

        history.append(entry)
        if len(history) > MAX_ENTRIES:
            history = history[-MAX_ENTRIES:]
        data["history"] = history
        save_history(data)
        return entry


def get_history(limit=100, trigger=None, family=None, search=None):
    """
    Geçmişi en yeniden eskiye sıralı olarak döner.
    İsteğe bağlı filtreleri uygular.
    """
    with history_lock():
        data = load_history()
    items = list(data.get("history", []))

    if trigger:
        items = [i for i in items if i.get("trigger") == trigger]
    if family:
        items = [i for i in items if i.get("family") == family]
    if search:
        s = search.lower()
        items = [
            i
            for i in items
            if s in i.get("to_account", "").lower()
            or s in i.get("from_account", "").lower()
            or s in i.get("reason", "").lower()
            or s in i.get("trigger_label", "").lower()
        ]

    # H3: En yeni ilk sırada (milisaniye çözünürlüğü ile)
    items.sort(
        key=lambda x: x.get("time_ms") or (x.get("time_epoch", 0) * 1000),
        reverse=True,
    )
    total_matched = len(items)
    if limit and limit > 0:
        items = items[:limit]
    return {
        "ok": True,
        "total": total_matched,
        "history": items,
        "items": items,
        "version": data.get("version", "1.0"),
    }
