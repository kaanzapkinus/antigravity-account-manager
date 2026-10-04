"""auth-runner — agy ile etkileşimli Google giriş akışı için ince sarmalayıcı.

agy PTY üzerinde çalışır, OAuth URL'ini ekrana basar, kullanıcı tarayıcıda
giriş yapıp kodu girince token'ı kendi HOME dizinine yazar.

Bu yardımcı, agyauth.py içindeki login akışının elle/terminal üzerinden
denenmesi içindir. Panelden giriş yapmak için `agyauth.py` kullanılır.

Kullanım:
    AGY_PROFILE_DIR=~/.antigravity-profiles/<profil> python3 auth-runner.py

Profil dizini verilmezse `~/.antigravity-profiles/default` kullanılır.
Token dosyaları yalnız profilin kendi HOME dizinine yazılır.
"""
import os
import subprocess
import sys

profile_dir = os.path.expanduser(
    os.environ.get("AGY_PROFILE_DIR", "~/.antigravity-profiles/default")
)
os.makedirs(profile_dir, mode=0o700, exist_ok=True)

agy_bin = os.environ.get("AGY_BIN", os.path.expanduser("~/.local/bin/agy"))
url_out = os.path.expanduser(
    os.environ.get("AGY_URL_FILE", os.path.join(profile_dir, "oauth-url.txt"))
)

env = dict(os.environ)
env["HOME"] = profile_dir
env["TERM"] = "xterm-256color"

print("[runner] agy baslatiliyor: %s" % agy_bin, flush=True)

proc = subprocess.Popen(
    [agy_bin, "-p", "hi", "--output-format", "json"],
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

captured = False
for line in proc.stdout:
    sys.stdout.write(line)
    sys.stdout.flush()
    if not captured and "https://accounts.google.com" in line:
        url = line.strip()
        fd = os.open(url_out, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(url + "\n")
        print("\n[runner] URL kaydedildi: %s" % url_out, flush=True)
        captured = True

ret = proc.wait()
print("\n[runner] agy %d ile sonlandi" % ret, flush=True)
sys.exit(ret)