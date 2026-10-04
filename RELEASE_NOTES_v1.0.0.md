## v1.0.0

Antigravity (Google) proxy hesap yönetim paneli ve otomatik hesap seçicinin ilk
açık sürümü.

### Panel — `agyauth.py`

- Hesap kartları: e-posta, proxy durumu, CLI/OMP kaynak durumu
- Kota barları: **5 saatlik** ve **haftalık**, Gemini (emerald) ve Claude/GPT (mor)
  olarak ayrı renklendirilmiş
- **Yenile** düğmesi: ölçülen süreyi gösterir; upstream değişiklik yalnızca
  doğrulanmış yeni veri geldiğinde "değişim" olarak raporlanır, bayat veya kısmi
  yanıtlar başarı gibi gösterilmez
- Yenilemede **önce/sonra** delta rozeti ve hayalet çubuk; ondalıklı değişimler
  (0.1 yüzde puan) korunur
- **Üç routing modu** — `auto` (zamanlayıcı seçer), `manual` (panel seçer,
  zamanlayıcı dokunmaz), `pool` (havuzdan dağıtır). Panelde *Havuza dön* ve
  *Otomatik* ayrı eylemlerdir
- **Geçiş günlüğü**: tetikleyici filtresi, serbest arama, kaynak türüne göre
  renkli kenar çubuğu, isteğe bağlı ham sistem kaydı
- **OAuth giriş**: panel üzerinden tarayıcıda giriş, doğrulama kodu besleme,
  otomatik OMP vault aktarımı
- Sekmeli arayüz (`Hesaplar` · `Geçiş Günlüğü`), açık/koyu tema — sistem
  tercihini izler, elle seçim kalıcıdır

### Zamanlayıcı — `agy-scheduler.py`

- Aile tespiti: proxy loglarındaki son 20 dakikanın çoğunluk model ailesi
- Seçim politikası: haftalık reseti **en yakın** hesap; eşitlikte **kalan
  kotası daha az** olan; 5 saatlik kovası tükenmiş hesaplar atlanır
- **Haftalık warmup**: penceresi dolmuş hesaba yeni sayaç başlatma
- Havuz dışı (`proxy_disabled`) ve doğrulama engelli hesaplar aday dışı
- `--dry-run` **hiçbir şey yazmaz**: state ve geçmiş dosyalarına dokunmaz,
  mutating istek yapmaz
- Pin yazımı sonrası readback ile doğrulama; belirsiz sonuçta sahiplik
  ilerletilmez

### Geçmiş — `history_store.py`

- Tüm read-modify-write `flock` ile korunur (kayıp kayıt ve `.tmp` yarışı giderilmiş)
- Benzersiz geçici dosya + `fsync` + atomik `replace`
- Alt-saniye çözünürlüklü zaman damgası ve kapasiteden bağımsız benzersiz ID
- Bozuk geçmiş **sessizce ezilmez**; `.corrupt.<ts>` olarak karantinaya alınır
- `trigger` alanı enum ile doğrulanır, istemciden gelen serbest metin saklanmaz
- Warmup olayları yönlendirme zincirinden ayrı tutulur

### Güvenlik

- **Host / Origin / `Sec-Fetch-Site`** doğrulaması; cross-site istekler `403`
- Mutasyonlarda `application/json` **zorunluluğu** (basit CSRF koruması)
- 64 KB gövde sınırı, okuma timeout'u, JSON nesne şeması; bozuk girdi `400`,
  aşırı gövde `413/400` — iç hata metni sızmaz
- Profil kökü **canonical** olarak doğrulanır; profil içi symlink'ler izinli
  kökün dışına çıkamaz
- Token'lar hiçbir API yanıtında veya logda görünmez
- Varsayılan olarak **yalnız loopback** dinler; ağ erişimi açıkça verilmelidir
  (`TAILNET_IP`)

### Arayüz

- `panel.html`: tek dosya — HTML + CSS + JS, build yok, bağımlılık yok
- PWA kabuğu: `sw.js`, `offline.html`, `manifest.webmanifest`, ikonlar
- Tasarım kararları `DESIGN.md` içinde (renk token'ları, tipografi, ölçek,
  hareket, erişilebilirlik, yasaklar)

### Yapılandırma

Kaynakta hiçbir kullanıcıya özel değer yoktur; tüm yollar ve adresler ortam
değişkenleridir:

| Değişken | Varsayılan |
|---|---|
| `AGY_BIN` / `OMP_BIN` | `~/.local/bin/…` |
| `AGY_MODEL` | `gemini-3.8-flash-medium` |
| `PORT` | `8098` |
| `LISTEN_LOOPBACK` | `127.0.0.1` |
| `TAILNET_IP` | *(boş — ağ erişimi kapalı)* |
| `AGY_PROFILES_ROOT` / `AGY_TOOLS_ROOT` | `~/.antigravity-profiles` / `~/.antigravity_tools` |
| `AGY_HISTORY_PATH` / `AGY_STATE_PATH` | profil kökü altında |
| `AGY_PROXY_API` | `http://127.0.0.1:8045` |

### Gereksinimler

Python 3.9+, Antigravity Tools (proxy `127.0.0.1:8045`), `agy` ve `omp` CLI'ları.

### Not

Antigravity Tools, `agy` ve `omp` bu projeye dahil değildir; bu depo yalnız
onların önündeki yönetim katmanıdır. `MIT` lisanslı.
