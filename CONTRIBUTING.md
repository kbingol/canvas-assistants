# Asistan katkısı

1. assistants/turkish-translator/ klasörünü yeni, kısa bir klasör adıyla kopyala.
2. package/assistant.json içinde benzersiz id ve pluginId kullan.
   Topluluk asistanları için community.ad / community.assistant.ad örneğini izle.
3. package/SKILL.md içine görevi ve karar kurallarını yaz. Ek skill dosyalarını
   manifestte bildir. İzinli araçlar: read_skill, inspect_image, web_search, generate_image.
4. listing.json içindeki yayıncı ve kullanım bilgisini düzenle.
   Yayıncı alanı incelemede kontrol edilir; doğrulanmış yayıncı rozeti sağlamaz.
5. Canvas'ta .flowast dosyasını kurarak gerçek bir örnek üzerinde dene.
   Çalıştırma normal model/sağlayıcı kullanımına tabi olabilir.
6. Yerel doğrulamayı çalıştır ve kaynak dosyalarla PR aç.

    python -m unittest discover -s tests -v
    python scripts/build_catalog.py

Üretim sonucu dist/ altına yazılır. catalog.json ve .flowast dosyalarını elle
değiştirme. Maintainer incelemesinden sonra main dalına alınan paket yayınlanır.

## İnceleme ölçütleri

- Açıklama, talimatlar ve kullanım örneği aynı görevi anlatmalı.
- Paket kimlikleri benzersiz olmalı; mevcut bir yayıncının kimliği taklit edilmemeli.
- Anahtar, hesap bilgisi, kişisel veri ve proje içeriği bulunmamalı.
- Yalnız assistant.json, ana SKILL.md ve bildirilen skill Markdown dosyaları bulunmalı.
- Paket Canvas'ta geçerli olmalı ve model Canvas'ın Harness döngüsünde araç sonuçlarını
  değerlendirerek çalışmalı.
- Arşiv en fazla 2 MB, açılmış veri en fazla 512 KB ve dosya sayısı en fazla 128 olabilir.

## Sürümler

Yayımlanan id ve pluginId sabit tutulur. Talimat veya skill değişirse
major.minor.patch biçimindeki sürümü artır. Aynı sürüme farklı içerik yüklenmesi
yayınlamayı durdurur. Önceden yayımlanan sürümler korunur.

Kullanacağın içerik ve kaynaklar üzerinde paylaşma hakkın olduğundan emin ol;
üçüncü taraf içeriğini gerekli atıf ve lisans bilgileriyle PR açıklamasında belirt.
