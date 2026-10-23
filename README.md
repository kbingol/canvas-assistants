# Canvas Asistan Kütüphanesi

[Canvas](https://github.com/kbingol/canvas) için herkese açık asistan kataloğu.
Asistanlar talimatları, skill'leri ve Canvas'ın izin verdiği araçların bildirimlerini
taşıyan .flowast veri paketleridir. Canvas'ın kendi Harness motorunda çalışırlar.

Canvas'ta **Ayarlar → Asistanlar → Kataloğu keşfet** üzerinden asistanları inceleyip
yükleyebilirsin. Yüklenen asistan uygulamayı yeniden açmadan kullanılabilir;
kişisel asistanlar bölümünden silinebilir ve dışa aktarılabilir.

## Katalog

| Asistan | Ne yapar? | Kaynak |
| --- | --- | --- |
| Türkçe Çevirmen | Bağlı metinleri ve gerekirse görseldeki metni Türkçeye çevirir. | [Paket](assistants/turkish-translator/package) |

Canvas, yayınlanan [catalog.json](catalog.json) dosyasını okur ve seçilen sürümü
[GitHub Releases](https://github.com/kbingol/canvas-assistants/releases) üzerinden
indirir. GitHub hesabı katalog okumak veya paket indirmek için gerekmez.
Paket yüklemek model çalıştırmaz. Asistanı çalıştırmak Canvas'taki hesap/model
bağlantısını kullanır; yüklenen paketlerin yerel kullanımı kataloğa bağlı değildir.

## Katkı ve yayınlama

Yeni bir asistan için bu repoya pull request gönder.
[Katkı rehberi](CONTRIBUTING.md) paket yapısını ve inceleme adımlarını anlatır.

PR'larda doğrulama ve tekrarlanabilir paket oluşturma çalışır. main üzerindeki
onaylı değişikliklerde yayınlama iş akışı .flowast sürümlerini oluşturur;
indirmeler doğrulandıktan sonra catalog.json dosyasını günceller.
Aynı kimlik ve sürümle yayımlanmış dosya değiştirilmez; değişiklik için sürüm artırılır.

Canvas'ın ilk katalog sürümü yeni kurulumları destekler. Mevcut bir asistanın
üzerine yazmaz veya otomatik güncelleme yapmaz.

## Yerel doğrulama

Python 3.12 veya üzeri yeterlidir; ek bağımlılık yoktur.

    python -m unittest discover -s tests -v
    python scripts/build_catalog.py

Üretilen katalog ve paketler dist/ klasöründedir; bu klasör commit edilmez.
Kök catalog.json yayınlama iş akışı tarafından yönetilir.
