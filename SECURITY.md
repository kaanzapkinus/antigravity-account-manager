# Güvenlik Politikası

## Desteklenen sürümler

`main` dalındaki en güncel sürüm.

## Raporlama

Bir açık bulmayı **özel olarak** bildirin — açık issue açmayın.

Güvenlik açığı, token sızdıran bir API, kimlik doğrulama veya yetkilendirme
eksiği, panelin tasarımı gereği yalnız loopback'e bağlı olmasına rağmen
uzaktan erişilebilir hâle gelmesi, state/geçmiş dosyalarının bozulmasına
yol açan bir yarış koşulu veya kimliği doğrulanmamış bir rota kararı olarak
tanımlanır.

## Kapsam dışı

Aşağıdakiler açık değildir:

- Antigravity Tools veya `agy` / `omp` CLI'larının kendi açıkları — bu proje
  yalnız onların *önündeki* yönetim katmanıdır
- `TAILNET_IP` verilerek bilinçli olarak açılan ağ erişiminin kendisi; bu
  bir yapılandırma kararıdır, açık değildir
- Kullanıcının kendi makinelerinde gevşek ACL kurması

## Yapılandırma güvenliği

`agyauth.py` bir **yönetim API'sidir**. Güvenliği yapılandırmaya bağlıdır.

| Ayar | Güvenli varsayılan | Not |
|---|---|---|
| `TAILNET_IP` | *(boş)* | Boşken yalnız `127.0.0.1` dinlenir. Değer verirseniz erişim açılır. |
| `LISTEN_LOOPBACK` | `127.0.0.1` | Değiştirirseniz erişim yine açılır. |
| `AGY_TOOLS_CONFIG` | `~/.antigravity_tools/gui_config.json` | Proxy API anahtarı buradan okunur; **repoya kopyalamayın.** |

Paneli ağa açmadan önce:

1. `TAILNET_IP` vermeyin ya da tailnet ACL'inizi daraltın
2. TLS sonlandıran bir reverse proxy tercih edin
3. Proxy API anahtarını dosya izinleriyle koruyun (`0600`)
4. `switch-history.json` içinde hesap e-posta adresleri tutulur — bu dosyayı
   paylaşmayın veya sıfırlamayın

## Veri saklama

| Dosya | İçerik | Paylaşılmalı mı |
|---|---|---|
| `switch-history.json` | hesap e-posta geçmişi | hayır |
| `agy-scheduler-state.json` | hesap kimlikleri, mod durumu | hayır |
| `*/.gemini/…` | OAuth token'ları | hayır |

Hepsi `.gitignore` ile kapsanır. Hiçbirı kaynakla birlikte dağıtılmaz.
