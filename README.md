# Fisoku

Fişi telefonla çek, bilgisayarındaki **AugeLab Studio** okusun: tarih, VKN, KDV oranları, matrah ve toplam otomatik ayıklanır, tutarlar birbiriyle kontrol edilir, sonuç tek tıkla Excel'e aktarılır.

Her şey kendi bilgisayarında çalışır. Fişler buluta gönderilmez.

```
 Telefon (tarayıcı)            Bilgisayar (Fisoku.exe)                   AugeLab Studio
 ──────────────────            ─────────────────────────                 ──────────────
 QR'ı okut, fişi çek  ──Wi-Fi──▶  yerel sunucu  ──MCP (127.0.0.1)──▶  Load Image → Fisoku OCR
                                  satırları kur, KDV'yi ayıkla, kontrol et       (RapidOCR)
                                  SQLite + fotoğraflar ─▶ Excel
```

## Gereksinimler

- Windows 10/11
- AugeLab Studio 3.12 veya üstü, açık ve **MCP etkin**: Studio → Settings → *MCP configuration* → `Status: running at 127.0.0.1:8765/mcp`
- Telefon ve bilgisayar aynı Wi-Fi ağında

İnternet gerekmez; OCR motoru Fisoku.exe'nin içinde gelir.

Studio kurulu değilse Fisoku panelinde bir kurulum sihirbazı açılır:

1. **Studio'yu kur**: Fisoku, AugeLab'in resmi kurulum dosyasını (yaklaşık 36 MB) indirir. Visual C++ bileşeni eksikse onu Microsoft'tan indirir, Microsoft imzasını doğrular ve kurar. Ardından Studio'yu standart klasöre kurar. Bu sürede AugeLab hesap sayfası sihirbazın içinde açık kalır; hesabınızı orada oluşturabilirsiniz. Otomatik kurulum olmazsa Studio'nun kendi kurulum penceresi açılabilir.
2. **Studio'yu aç**: kurulumdan sonra Fisoku Studio'yu başlatır.
3. **Etkinleştir**: Studio'nun doğrulama penceresine AugeLab hesabınızdaki doğrulama kodunu siz girersiniz (hesap sayfası sihirbazın içinde açıktır). Studio'nun MCP arayüzü varsayılan olarak kendiliğinden başlar; başlamazsa sihirbaz *AI Tools → AI Chat Window → Settings → MCP configuration → Start* adımını gösterir.
4. **Hazır**: Fisoku Studio'ya bağlanır. Bu sırada çekilen fişler de sırayla okunur.

İnternet yalnızca bu ilk Studio kurulumunda gerekir.

## Kullanım

1. AugeLab Studio'yu açın.
2. `Fisoku.exe`'yi [son sürümden](https://github.com/lyunusl/fisoku/releases/latest) indirip çift tıklayın. Bilgisayarda Fisoku paneli açılır.
3. Windows güvenlik duvarı sorarsa **Özel ağlar** için izin verin (telefonun bağlanabilmesi için).
4. Paneldeki QR kodu telefonun kamerasıyla okutun ve **Fiş çek**'e basın.
5. Okunan fişler panelde listelenir:
   - **✓ Tamam**: tüm tutarlar birbirini tutuyor.
   - **Kontrol et**: bir şey tutmuyor; fişe tıklayıp fotoğrafa bakarak düzeltin.
6. **Excel'e aktar** ile oran bazında matrah ve KDV dökümünü alın.

Studio kapalıyken çekilen fişler sırada bekler; Studio açılınca kendiliğinden okunur.

## Studio tarafında ne olur?

Fisoku ilk çalıştığında Studio'nun MCP arayüzüyle iki şey yapar; elle bir şey hazırlamanız gerekmez:

1. İki custom blok ekler (`fisoku/studio_block.py`):
   - **Fisoku OCR**: RapidOCR ile (PaddleOCR'ın PP-OCRv4 modelleri, ONNX) yazıları ve kutularını okur. Studio'nun Python ortamına **hiçbir paket kurulmaz**. Motor dosyaları, Studio'nun custom blok klasörünün yanına (`%APPDATA%\AugeLab Studio\marketplace\fisoku_engine`) kopyalanır; blok onu kendi dosya konumundan bulur. Sahnede kullanıcıya özel bir yol saklanmaz, okunan fotoğrafın yolu da her okumadan sonra sahneden silinir. Temiz bir Studio kurulumunda olmayan iki kütüphane (onnxruntime, shapely) motorla gelir ve arama yolunun sonuna eklenir; Studio'da kendi sürümleri varsa her zaman onlar kullanılır.
   - **Fisoku Light Flatten**: görüntüyü çok bulanıklaştırılmış kopyasına bölerek parlamayı, gölgeyi ve soluk termal baskıyı düzler.
2. Yeni bir belgede bir sahne kurar ve Studio açık kaldıkça hep onu kullanır:

```
Fisoku Image → Load Image From Path ─┬────────────────────────────────┐
                                          ├→ Contrast Optimization ───┤
                                          ├→ Auto Contrast ───────────┼→ Multi Port Switch → Fisoku OCR
                                          └→ Fisoku Light Flatten ────┘          ↑
Fisoku Variant (0 ham · 1 CLAHE · 2 Auto Contrast · 3 Light Flatten) ────────────┘
```

Her fotoğraf önce ham okunur. Okumada eksik ya da tutmayan alan kalırsa aynı fotoğraf diğer üç filtreyle tekrar okunur ve yalnızca eksikler, güvenilebilen değerlerle doldurulur:

- VKN: kontrol hanesi tutmalı.
- Tutarlar: tüm para kontrollerinden geçmeli.
- Tarih ve fiş no: iki yedek okuma aynı değeri bulmalı. Tek okumadan gelen değer doldurulur ama fiş "Kontrol et"te kalır.

Ham okumanın bulduğu bir değer hiçbir zaman değiştirilmez. Yol seçici sayesinde her okuma tek bir OCR çalıştırmasıdır.

## Doğrulama

OCR hata yapar; Fisoku'nun işi bu hataları yakalamaktır. Her fişte şunlar kontrol edilir:

| Kontrol | Tutmazsa |
|---|---|
| Oran bazlı KDV'lerin toplamı = TOPKDV | Kontrol et |
| Satırların toplamı = TOPLAM | Kontrol et |
| Her oran için satırlardan hesaplanan KDV = fişteki KDV | Kontrol et |
| VKN (10 hane) / TCKN (11 hane) kontrol hanesi | Kontrol et |
| Tarih geçerli ve gelecekte değil | Kontrol et |
| Aynı VKN + fiş no + tarih daha önce eklenmiş mi | Çift kayıt uyarısı |

Termal fişlerde OCR sık sık `%`'yi `7`, `*`'ı `#` okur, ondalık virgülünü ya da "KDV" etiketini kaçırır. Ayrıştırıcı bunları tanır, ama okunan her değeri yukarıdaki kontrollerden geçirir:

- Fişin kendi KDV tablosu varsa (market fişlerindeki "KDV TUTARI / KDV'Lİ TOPLAM"), her satırı fişin tarihindeki geçerli oranla kuruşu kuruşuna doğrulanır; satırlar fiş toplamını tutuyorsa KDV ve matrah buradan alınır.
- Etiketi okunamayan ya da yanlış satıra kaymış KDV tutarı, ancak satırlardan hesaplanan KDV fişte birebir basılıysa kabul edilir.
- Okunamayan tek bir oran tutarı TOPKDV'den türetilir ve satırlarla yeniden kontrol edilir.
- Satırlar okunamazsa matrah, KDV tutarlarından ve net toplamdan hesaplanır.
- Aynı fiş ikinci kez çekilirse (fiş no ve toplam aynı, okunan VKN/tarih çelişmiyor) çift kayıt uyarısı verilir.

Ölçüm (Studio 3.12, Fisoku OCR bloğu):

| Set | Sonuç |
|---|---|
| `samples/` – 6 sentetik fiş (gerçek kişi ya da VKN içermez) | 6/6 otomatik doğru |
| Telefonla çekilmiş 6 gerçek termal fiş (kafe, restoran, manav, market, e-Arşiv) | VKN, tarih, fiş no, toplam, KDV: 29/30 doğru. Tek eksik (fotoğrafta parlayan başlıktaki VKN) "Kontrol et" olarak işaretlendi; yanlış değer kabul edilmedi |

Aynı 6 fişte filtrelerin tek başına sonucu: ham 27/30, CLAHE 24/30, Auto Contrast 25/30, Light Flatten 25/30, Adaptive Threshold 11/30. Hiçbir filtre ham okumadan iyi değil, ama ham okumanın kaçırdığı alanları buluyorlar. Bu yüzden yedek olarak kullanılıyorlar; Adaptive Threshold ise sahnede yok.

Gerçek fiş fotoğrafları kişisel veri içerebileceği için depoya eklenmez.

## Bilinen sınırlar

- OCR modeli Türkçe harfleri (ş, ğ, ı, İ) her zaman tanımaz; satıcı adlarında düz harfe dönüşebilir. Rakamlar ve tutarlar etkilenmez.
- Şimdilik yazar kasa fişi ve e-Arşiv fiş biçimine odaklıdır. Çok sayfalı faturalar ve e-Fatura/e-Arşiv XML'i henüz desteklenmiyor.
- Telefon sayfası düz HTTP üzerinden çalışır; yalnızca yerel ağda ve eşleşme anahtarıyla erişilebilir. Bilgisayar paneli sadece bilgisayarın kendisinden açılır.

## Veriler nerede?

`Belgeler\Fisoku Fisleri`:

| | |
|---|---|
| `fisoku.db` | Fişler (SQLite) |
| `photos\` | Fiş fotoğrafları |
| `pairing.key` | Telefon eşleşme anahtarı; silip Fisoku'yu yeniden başlatırsanız eşleşme sıfırlanır |
| `studio_scene.json` | Studio'daki Fisoku sahnesinin kimliği (Studio açık kaldıkça aynı sahne kullanılır) |

OCR motoru ise Studio tarafındadır: `%APPDATA%\AugeLab Studio\marketplace\fisoku_engine`.

Veriler `%LOCALAPPDATA%` yerine Belgeler'de durur, çünkü Studio'nun görüntü yükleme bloğu oradaki dosyaları açamıyor.

## Geliştirme

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\pip install --no-deps rapidocr_onnxruntime==1.4.4
.venv\Scripts\python tools\fetch_engine_deps.py
.venv\Scripts\pytest
.venv\Scripts\python -m fisoku
```

`--no-deps`: RapidOCR yalnızca Studio içinde çalışır; Fisoku onu import etmez, sadece Studio'ya taşır.

| Dosya | Görev |
|---|---|
| `fisoku/studio.py` | Studio MCP köprüsü: bloğu ve sahneyi kurar, fotoğrafı OCR'dan geçirir |
| `fisoku/studio_block.py` | Fisoku OCR custom bloğunun tanımı |
| `fisoku/engine.py` | OCR motorunu Studio'nun custom blok klasörünün yanına koyar |
| `fisoku/studio_setup.py` | Studio kurulu değilse bulur, indirir, kurulumunu açar ve başlatır (paneldeki sihirbaz) |
| `tools/fetch_engine_deps.py` | Temiz Studio'da olmayan onnxruntime ve shapely'yi (Python 3.12, Windows x64) indirir |
| `fisoku/layout.py` | OCR kutularından fiş satırlarını yeniden kurar (eğim düzeltme) |
| `fisoku/receipt.py` | Ayıklama ve doğrulama |
| `fisoku/server.py` | Yerel sunucu, QR eşleşme, OCR kuyruğu |
| `fisoku/web/` | Telefon sayfası ve bilgisayar paneli |
| `tools/make_samples.py` | Sentetik fiş üretir |
| `tools/capture_ocr.py` | Fotoğrafları Studio'dan geçirip test verisini (`*.ocr.txt`, `*.words.json`) günceller |

Tek dosyalık exe: `build.cmd` → `dist\Fisoku.exe`.

Testler Studio olmadan çalışır; Studio'dan alınmış OCR çıktıları `samples/` altında durur.

## Lisans

MIT. AugeLab Studio ayrı, lisanslı bir üründür ve bu depoda yer almaz; Fisoku, Studio ile yalnızca Studio'nun yerel MCP arayüzü üzerinden konuşur.

Exe ile dağıtılan üçüncü parti bileşenler: [RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0) ve içindeki PaddleOCR PP-OCRv4 modelleri (Apache-2.0), [ONNX Runtime](https://github.com/microsoft/onnxruntime) (MIT), [Shapely](https://github.com/shapely/shapely) (BSD-3-Clause, GEOS: LGPL-2.1).
