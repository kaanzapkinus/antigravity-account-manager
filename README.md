## Özellikler

- Hesap kartlarında **5 saatlik / haftalık** kota çubukları (Gemini ve Claude/GPT ayrı renk)
- **Yenile** düğmesi: gerçek ölçülen süreyi gösterir; upstream değişiklik ancak
  doğrulanmış yeni veriyle "değişim" sayılır
- Yenilemede **önce/sonra** delta rozeti ve hayalet çubuk
- **Üç routing modu**: `auto` (zamanlayıcı seçer), `manual` (panel seçer, zamanlayıcı
  dokunmaz), `pool` (havuzdan dağıtır)
- **Geçiş günlüğü**: filtre, arama, kaynak türüne göre renkli çubuk, ham sistem kaydı
- **OAuth giriş**: panel üzerinden tarayıcıda giriş, kod besleme, otomatik OMP aktarımı
- Sekmeli arayüz, **açık/koyu tema** (sistem tercihini izler), mobil uyumlu
- PWA: kurulabilir, çevrimdışı kabuk

## Mimari

```
panel (agyauth.py :8098)          scheduler (agy-scheduler.py)
  ├ hesap kartları / kota            ├ aile tespiti (gemini|claude)
  ├ manuel tercih / havuz             ├ en yakın haftalık reset seçimi
  ├ geçiş günlüğü                     └ haftalık warmup
  └ OAuth giriş
          └──────────┬─────────────┘
             Antigravity Tools proxy (127.0.0.1:8045)
```

**Seçim politikası:** haftalık reseti en yakın hesap önceliklidir; eşitlikte kalan
kotası daha az olan seçilir. 5 saatlik kovası tükenmiş hesap atlanır. Aile, proxy
loglarındaki son 20 dakikanın çoğunluk model ailesinden çıkar.

**Zamanlama:** haftalık penceresi dolmuş hesaba warmup gönderilir, böylece yeni
sayaç başlar. Geçici hatada 12 saat soğuma uygulanmaz; sınırlı tekrar denenir.

## Durum dosyaları

`agy-scheduler-state.json` routing modunu ve zamanlayıcının sahiplendiği pini
taşır. Panel ve zamanlayıcı aynı sözleşmeyi okur; manuel kullanıcı seçimi
zamanlayıcı tarafından ezilmez.

## Gereksinimler

- Python 3.9+ (macOS sistem Python'ı ile test edildi)
- Antigravity Tools — proxy API'si `127.0.0.1:8045`
- `agy` ve `omp` binary'leri (OAuth giriş ve vault aktarımı için)

## Lisans

[MIT](LICENSE)
