# AGY Hesap Yönetimi — Amber Panel

Google/Antigravity proxy hesaplarını yöneten tek sayfalık operasyon paneli.
Referans görsel dil: **ompweb** (amber/warm-paper palet). Hedef: **minimal, sade, veri öncelikli.**

---

## 1. Tasarım niyeti

Süsleme değil, okunabilirlik. Ekranda iki gerçek iş yapılır:

1. **Hesap seçimi** — hangi hesap proxy tercihi, hangi kovada ne kadar kota var.
2. **Değişim takibi** — kim ne zaman neden geçiş yaptı.

Bu ikisi **birbirine girmez**. Sekmeler bunu zorlar; günlüğün hesap listesinin altına
gömülü olması, kayıtları okunur kılmıyor.

Üç kural:

- Her sayı **mono + tabular**, hizalı okunsun.
- Her renk **anlam taşısın**; dekoratif renk yok.
- Bir ekranda **tek ana vurgu** olsun (amber accent), geri kalanı nötr.

---

## 2. Renk tokenları

Ompweb'in warm-paper ailesinden birebir. Light ve dark aynı token adlarını kullanır,
sadece değerler değişir.

### Light — "sıcak kâğıt"

| Token | Değer | Kullanım |
|---|---|---|
| `--bg` | `#FAF9F6` | sayfa zemini |
| `--bg-panel` | `#F2F0EA` | kart yüzeyi |
| `--bg-hover` | `#EAE7DF` | hover |
| `--bg-selected` | `#E5E0D4` | seçili segment |
| `--border` | `#E2DDD2` | hairline |
| `--text` | `#2B2823` | birincil metin (13.9:1) |
| `--text-muted` | `#69635A` | ikincil (5.6:1) |
| `--text-dim` | `#6A6458` | üçüncül / zaman damgası |
| `--accent` | `#B03E22` | terracotta — ana aksiyon |
| `--accent-hover` | `#96331B` | aksiyon hover |
| `--on-accent` | `#FFFFFF` | aksiyon üstü metin |

### Dark — "sıcak kül"

| Token | Değer | Kullanım |
|---|---|---|
| `--bg` | `#1B1916` | sayfa zemini |
| `--bg-panel` | `#231F1B` | kart yüzeyi |
| `--bg-hover` | `#2B2721` | hover |
| `--bg-selected` | `#332E26` | seçili segment |
| `--border` | `#38322B` | hairline |
| `--text` | `#EBE6DC` | birincil metin |
| `--text-muted` | `#A39B8E` | ikincil |
| `--text-dim` | `#938C81` | üçüncül |
| `--accent` | `#E07B54` | amber-terracotta — ana aksiyon |
| `--accent-hover` | `#E89371` | hover |
| `--on-accent` | `#1B1916` | aksiyon üstü metin |

### Anlamsal durum renkleri

Renk yalnız durum bildirir. Aynı token light ve dark'ta.

| Durum | Light | Dark |
|---|---|---|
| sağlıklı / yeterli kota | `#18794E` | `#69D5A5` |
| uyarı / düşük kota | `#8A5A00` | `#F0C36A` |
| hata / kota bitti | `#B42318` | `#FF8A80` |
| bilgi / aktif | `--accent` | `--accent` |
| pasif / nötr | `--text-dim` | `--text-dim` |

Kota çubukları **aileden** renk alır, durumdan değil:

- Gemini → emerald `#10B981` (light `#059669`)
- Claude/GPT → `#7C3AED` / `#A855F7`

Delta (önceki → güncel) yönü:

- düşüş → `--status-error`
- artış → `--status-success`

---

## 3. Tipografi

İki font. Başka hiçbir şey.

- **Inter** — arayüz metni. 13px taban.
- **JetBrains Mono** — her sayı, yüzde, süre, tarih, sayaç, ID.

```css
--font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
```

| Rol | Font | Boyut | Ağırlık | Not |
|---|---|---|---|---|
| Sayfa başlığı | Inter | 15px | 650 | -0.01em |
| Bölüm/segment etiketi | Mono | 10px | 600 | uppercase, letter-spacing .08em, `--text-dim` |
| Kart başlığı (e-posta) | Inter | 14px | 600 | -0.01em |
| **Kota değeri** | Mono | 17px | 700 | tabular-nums, -0.02em |
| Alt metin | Inter | 11.5px | 400 | `--text-muted` |
| Zaman damgası | Mono | 10.5px | 500 | `--text-dim` |
| Rozet/çip | Mono | 10px | 700 | |

**Kural:** yüzde gördüğünde mono olmalı. Asla `font-variant-numeric` olmadan.

---

## 4. Şekil

| Öğe | Yarıçap |
|---|---|
| Buton, input, çip | 6px |
| Kart | 10px |
| Ana bölüm / modal | 14px |
| Rozet (çip) | tam pill |

Gölge yok. Derinlik yalnız yüzey tonu + hairline'dan gelir.

---

## 5. Yerleşim

### 5.1 Kabuk

```
┌──────────────────────────────────────────────┐
│ ● ● ●   Google Hesapları        [tema] [↻]   │  header, sticky
├──────────────────────────────────────────────┤
│  [ Hesaplar · 3 ]   [ Geçiş Günlüğü · 64 ]  │  sekmeler, sticky
├──────────────────────────────────────────────┤
│                                              │
│                 içerik                       │
│                                              │
└──────────────────────────────────────────────┘
```

- Maks genişlik **760px**, ortalanmış. Fazlası okunmaz.
- Header sticky: 52px. Sekme çubuğu sticky: 44px. İçerik kaydırılır.
- Mobil (<560px): başlık ikon + metin, aksiyonlar ikinci satıra iner.

### 5.2 Sekmeler — log ekranı sorununun çözümü

İki sekme, **aynı DOM ağacında**, `hidden` ile açılıp kapanır.

- Sekme etiketi + sayaç: `Hesaplar · 3`, `Geçiş Günlüğü · 64`
- Seçili sekme: `--bg-panel` yüzey + `--text` etiket + alt çizgi `--accent` 2px
- Sekme değişimi içeriği değiştirir, **sayfayı yeniden yüklemez**
- Günlüğe geçildiğinde veri zaten bellekte; ek istek ancak boşsa

**Yasak:** Günlüğü hesap listesinin altına akış halinde koymak. Bu, mevcut karmanın
kaynağı.

### 5.3 Hesaplar sekmesi

```
┌─ Durum satırı ─────────────────────────────────────────┐
│ Tercih: a***@gmail.com      Port 8045 · Çalışıyor    │
│ [ Sonraki hesap → ] [ Otomatik ] [ Havuz ]             │
└────────────────────────────────────────────────────────┘

┌─ Hesap kartı ──────────────────────────────────────────┐
│ G  a***@gmail.com          [ Proxy tercihi ]           │
│    PROXY HESABI · token var                            │
│                                                        │
│  GEMINI (Gemini)                    Flash & Pro havuz   │
│  5 saatlik   %98.9  ▓▓▓▓▓▓▓▓▓▓░  ↺ 4s 45dk          │
│  Haftalık    %93.7  ▓▓▓▓▓▓▓▓░░░  ↺ 4g 9s             │
│                                                        │
│  CLAUDE / GPT               Sonnet, Opus, OpenAI       │
│  5 saatlik   %100  ▓▓▓▓▓▓▓▓▓▓▓  ↺ 4s 45dk           │
│  Haftalık    %66.1  ▓▓▓▓▓▓░░░░░  ↺ 1g 5s              │
│                                                        │
│  ▸ CLI profili · OMP vault · kaynak durumu             │
└────────────────────────────────────────────────────────┘
```

Kart içi hiçbir zaman yatay taşma. Kova satırı:

```
etiket(72px)  değer(64px)  çubuk(1fr, min 60px)  sayaç(1fr, wrap)
```

Kota çubuğu: 6px yükseklik, `rounded-full`, izsiz. Dolu bölüm aile rengi.
Ghost bar arkasında, `--status-error`/`--status-success` %20 opak, `no-preference`
dışında animasyon kapalı.

### 5.4 Geçiş Günlüğü sekmesi

```
┌─ Ara ─────────────────────────────────────────────────┐
│ [ Tümü 64 ] [ Otomatik 3 ] [ Manuel 23 ] [ Havuz 11 ]│
│ [ Ara: e-posta, neden…                    ] [ ↻ ]    │
├────────────────────────────────────────────────────────┤
│ ● 19:54  Otomatik Zamanlayıcı      gemini             │
│   a***@gmail.com  →  b***@gmail.com                   │
│   Otomatik Seçim: gemini ailesi için reset=06.10 …     │
├────────────────────────────────────────────────────────┤
│ ● 16:58  Haftalık Warmup                                │
│   c***@gmail.com                                       │
│   Haftalık reset penceresi dolan hesap …               │
└────────────────────────────────────────────────────────┘
```

- **Tek sütun liste.** Kart değil, satır. Kartlar yan yana değil, alt alta.
- Satır yüksekliği sabit ~56px, satırlar arası `hairline` ayırıcı
- Sol kenarda 2px durum çubuğu: kaynak tipine göre renk
  (otomatik=accent, manuel=text-dim, havuz=success, warmup=warning)
- Zaman: saat + dakika, mono, `--text-dim`
- Neden: tek satır, taşarsa ellipsis, tam metin `title` ve altında `raw log` detayı
- Ham sistem kaydı `<details>` ile gizli — **varsayılan kapalı**

### 5.5 Görsel gürültü kesme

Mevcut karmaşıklığın kaynağı ve çözümü:

| Sorun | Çözüm |
|---|---|
| Hero + ayrı durum rozeti + ayrı meta satırı | Tek durum satırı; metin ve port aynı satırda |
| Kart içinde iç içe 3 kutu (limit kartı → kova → değer) | Kart düz; sadece iki aile bloğu, kova satırları |
| Her yerde ayrı ayrı renk (yeşil, mor, mavi, sarı, kırmızı) | Renk = durum veya aile; nötr gerisi |
| Buton her satırda farklı stil | 3 buton tipi: `primary`, `ghost`, `icon` |
| "Yükle", "↻", metin hepsi ayrı boy | Tek ikon + metin; ikon 14px |
| macOS trafik ışıkları süs | Kaldırıldı — çalışmıyor, yer kaplıyor |

---

## 6. Bileşenler

### Buton

| Tip | Kullanım | Görünüm |
|---|---|---|
| `primary` | "Sonraki hesap", "Proxy tercihi yap" | `--accent` dolgu, `--on-accent` metin |
| `ghost` | "Havuz", "Otomatik" | şeffaf, `1px --border`, `--text` metin |
| `icon` | tema toggle, yenile | 28×28, sadece ikon, `--text-muted` |

Tümü: 32px yükseklik, 6px radius, 120ms geçiş, `:active { scale: .98 }`.
Devre dışı: `--text-dim` metin, şeffaf zemin, `cursor: not-allowed`.

### Durum rozeti

6px nokta + 10px mono etiket + pill. Renk = durum.
Nokta aktif durumda 2s `opacity .5→1` nefes alır; `prefers-reduced-motion` altında durur.

### Sekme

48px yükseklik. Etiket + sayaç rozeti. Seçili: alt çizgi `--accent` 2px.
Sayı değişince (yeni kayıt) 400ms sonra `is-new` vurgusu, tek seferlik.

### Kota satırı

- Etiket: 10px mono uppercase `--text-dim`, sabit 72px
- Değer: 17px mono 700, aile rengi
- Çubuk: 6px, izsiz; dolu = aile rengi; ghost = önceki seviye
- Sayaç: 10.5px mono `--text-dim`, `↺ 4s 45dk sonra`
- Delta: 10px mono çip, yön oku + yüzde puan (`▼ -2.5 puan`)
- Bilinmeyen: `--` yerine `%100` **asla**; çubuk boş

### Metin disiplini

- Kart başlığı: e-posta. Uzunsa ellipsis, `title` tam.
- Alt bilgi: `PROXY HESABI · TOKEN VAR` — 10px mono uppercase, `--text-dim`
- Yardım metni: tek cümle, `--text-muted`. Paragraf yok.

---

## 7. Hareket

| Süre | Kullanım |
|---|---|
| 120ms | buton hover, rozet |
| 200ms | sekme geçişi, kart yüksekliği |
| 320ms | modal |

Easing: giriş `cubic-bezier(0.22, 1, 0.36, 1)`, çıkış `cubic-bezier(0.4, 0, 0.2, 1)`.
Sekme içeriği: `opacity 0→1` + `translateY 4px→0`, 200ms.
Zıplama, overshoot, parallax yok.

`@media (prefers-reduced-motion: reduce)` — tüm geçişler ve animasyonlar `none`.

---

## 8. Erişilebilirlik

- Kontrast: metin/arkaplan ≥ 4.5:1, büyük metin ≥ 3:1 (token tablolarında doğrulandı)
- Durum **asla yalnız renkle** anlatılmaz: her rozet metin + nokta taşır
- `#refresh-banner` → `role="status"`, `aria-live="polite"`
- Hesap listesi → `aria-live="polite"`
- Sekmeler → `role="tablist"` / `role="tab"` / `aria-selected` / `role="tabpanel"`
- Tema toggle → `aria-label`, `aria-pressed` yok, `aria-label` değişir
- Odak halkası: `outline: 2px solid var(--accent)`, `outline-offset: 2px`
- Dokunma hedefi ≥ 40px (mobilde 44px)

---

## 9. Tema davranışı

1. `localStorage['agy-theme']` = `light` | `dark` | yok
2. Yoksa `prefers-color-scheme` izlenir, `matchMedia` change dinlenir
3. `<html data-theme="light|dark">` — CSS tek yerden token değiştirir
4. Toggle üç durumlu değil, iki durumlu: **Aydınlık ↔ Karanlık**
5. İlk ziyarette `prefers-color-scheme` uygulanır, sonra kullanıcı tercihi kalıcı

Tema değişimi **yeniden render gerektirmez** — tüm renkler CSS değişkeni.

---

## 10. Yasaklar

- Emoji (🔄, ↻ hariç metrik karakter; ikon yerine vektör veya Unicode ok)
- Kart üstünde gölge
- Neon/gradient dolgu
- Cam efekti (backdrop-filter) — yalnız modal arka planı
- İki aksan rengi aynı ekranda
- Dekoratif renk
- Sayıyı proportional fontla göstermek

---

## 11. Örnek ekran — Hesaplar sekmesi, dark

```
╔══════════════════════════════════════════════════════╗
║  AGY Hesap Yönetimi           Antigravity           ║
║                                    [ ☀ ]  [ ↻ Yenile ]║
╠══════════════════════════════════════════════════════╣
║  ▎ Hesaplar · 3        Geçiş Günlüğü · 64             ║
╠══════════════════════════════════════════════════════╣
║  ● Tercih: a***@gmail.com   Port 8045 · Çalışıyor    ║
║    [ Sonraki hesap → ] [ Havuz ]                     ║
╠══════════════════════════════════════════════════════╣
║  G  a***@gmail.com                    ● Proxy tercihi ║
║     PROXY HESABI · TOKEN VAR                         ║
║                                                      ║
║     GEMINI (GEMINI)              Flash & Pro havuzu   ║
║     5 SAATLIK   %98.9 ▓▓▓▓▓▓▓▓▓▓░ ↺ 4s 45dk        ║
║     HAFTALIK    %93.7 ▓▓▓▓▓▓▓▓░░░ ↺ 4g 9s           ║
║                                                      ║
║     CLAUDE / GPT                Sonnet, Opus, OpenAI ║
║     5 SAATLIK   %100  ▓▓▓▓▓▓▓▓▓▓▓ ↺ 4s 45dk        ║
║     HAFTALIK    %66.1 ▓▓▓▓▓▓░░░░░ ↺ 1g 5s           ║
║                                                      ║
║     ▸ CLI profili · OMP vault · kaynak durumu         ║
╠══════════════════════════════════════════════════════╣
║  W  b***@gmail.com                 Proxy tercihi yap   ║
║     PROXY HESABI · TOKEN VAR                         ║
║     …                                                ║
╚══════════════════════════════════════════════════════╝
```

## 12. Örnek ekran — Geçiş Günlüğü sekmesi, light

```
╔══════════════════════════════════════════════════════╗
║  AGY Hesap Yönetimi           Antigravity           ║
║                                    [ 🌙 ] [ ↻ Yenile ]║
╠══════════════════════════════════════════════════════╣
║    Hesaplar · 3      ▎Geçiş Günlüğü · 64             ║
╠══════════════════════════════════════════════════════╣
║  [ Tümü 64 ][ Otomatik 3 ][ Manuel 23 ][ Havuz 11 ]  ║
║  [ Ara: e-posta, neden…                      ] [↻]  ║
╠══════════════════════════════════════════════════════╣
║  ▎19:54  OTOMATİK ZAMANLAYICI              GEMINI   ║
║    a***@gmail.com  →  b***@gmail.com                   ║
║    Otomatik Seçim: gemini ailesi için reset=06.10 …     ║
║  ──────────────────────────────────────────────────── ║
║  ▎16:58  HAFTALIK WARMUP                                ║
║    c***@gmail.com                                       ║
║    Haftalık reset penceresi dolan hesap için yeni …     ║
║  ──────────────────────────────────────────────────── ║
║  ▎14:20  MANUEL TERCİH (PANEL)                         ║
║    b***@gmail.com  →  a***@gmail.com                   ║
║    Panel üzerinden seçildi                              ║
╚══════════════════════════════════════════════════════╝
```