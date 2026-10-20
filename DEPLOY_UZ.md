# Telefondan Render.com ga joylash (bepul)

Kerak: telefon, internet, Telegram. Kompyuter shart emas.

## 1. GitHub akkaunti (kod saqlanadigan joy)
1. github.com → Sign up (email, parol, username)
2. Pastdagi «+» → **New repository** → nom: `kinobot` → **Public** → Create repository

## 2. Fayllarni yuklash
1. `kino_bot.zip` ni telefon fayl menejerida **arxivdan chiqaring** (Extract)
2. GitHub repositoriyada **Add file → Upload files**
3. Chiqqan papkadagi **barcha fayllarni** tanlang (papkalar yo'q, hammasi bir joyda) → **Commit changes**

> `.env.example` ko'rinmasa — muhim emas, kerak emas.

## 3. Render.com
1. render.com → Sign up → **GitHub bilan kiring**
2. **New + → Web Service** → `kinobot` repositoriyasini tanlang
3. Sozlamalar:
   - Language: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python main.py`
   - Instance Type: **Free**
4. **Environment Variables** bo'limida qo'shing:
   - `BOT_TOKEN` = @BotFather bergan token
   - `ADMIN_IDS` = sizning Telegram ID (@userinfobot dan)
   - `BOT_TITLE` = bot nomi (masalan: MEGA CINEMA)
   - `PYTHON_VERSION` = `3.12.8`
5. **Create Web Service** → 3–5 daqiqa kuting. «Bot ishga tushdi» yozuvi chiqsa tayyor.

Mini App manzili avtomatik olinadi (Render bergan `https://....onrender.com`), qo'lda yozish shart emas.

## 4. Bot uxlab qolmasligi uchun (muhim!)
Bepul Render server 15 daqiqa jim tursa uxlaydi. Uni uyg'oq saqlang:
1. uptimerobot.com → bepul akkaunt
2. **Add New Monitor** → turi: **HTTP(s)**
3. URL: `https://SIZNING-NOM.onrender.com/health`
4. Interval: **5 minutes** → Create

## 5. Baza yo'qolmasligi uchun
Bepul hostingda fayllar server qayta ishga tushganda o'chib ketadi. Shuning uchun:
- Har kino qo'shilgandan 2 daqiqa keyin va har 6 soatda bot sizga **baza fayli** yuboradi (💾 kino_zaxira_....db)
- **Bu fayllarni Telegramdan o'chirmang!**
- Baza yo'qolsa: botga `/restore` yozing → oxirgi zaxira faylini yuboring → hammasi qaytadi

## Muammo bo'lsa
Render sahifasida **Logs** bo'limini oching va oxirgi qizil yozuvni menga yuboring.

## Yangilash (muhim!)
Render har safar GitHub'ga yangi fayl yuklanganda botni qayta ishga tushiradi va **bazani o'chiradi**. Shuning uchun:
1. Yuklashdan OLDIN botda `/admin` → **💾 Zaxira nusxa** bosing (fayl Telegramga keladi)
2. Hamma fayllarni **bitta commit**da yuklang (Upload files → hammasini tanlang → Commit)
3. Bot qayta ishga tushgach `/restore` yozib zaxira faylini yuboring

## Katalog ichidagi Admin panel
Admin bo'lsangiz katalog tepasida **⚙️** tugmasi chiqadi:
- 📊 Statistika — foydalanuvchilar, ko'rishlar, top kinolar
- 🎬 Qo'shish — ma'lumotlarni to'ldirib «Saqlash» bosasiz, bot videoni so'raydi, yuborasiz
- 📋 Kinolar — ⏳ kutilayotganlar + «Hammasini chiqarish», qidirish, o'chirish
- ⚙️ Sozlama — himoya, majburiy obuna, kanallar

## Bo'limlar: Kino, Multfilm, Anime, Serial
Katalogda tepada bo'limlar bor (Hammasi / Kino / Multfilm / Serial / Anime).

**Kodlar:** kino — oddiy raqam (`12`), serial — `s.1`, qismli multfilm — `m.1`, qismli anime — `a.1`.
Foydalanuvchi botga `s.1` yozsa, bot «Katalogda ochish» tugmasini beradi. Katalog qidiruvda `s.1` ni topib, qismlar tugmalarini (1, 2, 3...) ko'rsatadi. Qism bosilsa — botga yuboriladi.

## Kanaldan avto-yuklash (⚙️ → 📥 Avto)
Ichida ikkita tugma bor:
- **⚙️ Sozlamalar** — kanal ulash. Botni kanalga ADMIN qiling, kanal @username ini yozing va turini tanlang:
  - **Kino / Multfilm / Anime (qismsiz):** kanalga video tashlab, izohiga yozing:
    ```
    Qasoskor
    Yili: 2023
    Janri: Jangari, Drama
    Davlati: AQSH
    ```
  - **Serial** (yoki «Qismli» yoqilgan multfilm/anime): har serial uchun ALOHIDA kanal. Serial nomi, yili, janri va hokazolarni ulayotganda bir marta kiritasiz. Keyin kanalga qismlarni tashlaysiz, **video izohiga faqat qism raqamini** yozing: `1`, `2`, `3`... (`3-qism` ham bo'ladi). Raqam yozilmasa bot sizga ogohlantirish yuboradi.
- **✅ Qo'shishga tayyor** — kanaldan kelganlar shu yerda yig'iladi: «Kinolar — 12 ta», «Qasoskorlar (s.1) — 8 qism». Guruh ustiga bossangiz ro'yxati ochiladi. Har guruhni alohida chiqarish yoki hammasini birdan chiqarish mumkin. Chiqarilganda foydalanuvchilarga **bitta umumiy xabar** ketadi («Xabarsiz» ham bor).

Kanalda oldindan turgan kontent: postlarni botning o'ziga **forward** qiling (qismli kanaldan forward qilsangiz qism sifatida o'qiladi).
Poster videoning Telegramdagi kadridan olinadi. Almashtirish: `/poster 12` yoki `/poster s.1` va rasm yuboring.

## Qo'lda qo'shish (⚙️ → 🎬 Qo'shish)
Avval turini tanlaysiz (Kino / Multfilm / Anime / Serial). Multfilm va anime uchun «Qismli» tugmasi bor. Qismli bo'lsa mavjud serialni tanlab qism raqamini yozasiz (yoki yangi yaratasiz). Saqlagach botga videoni yuborasiz.

⚠️ Yuklab bo'lgach zaxira nusxa oling — Render qayta ishga tushsa baza o'chadi (bot zaxirani o'zi ham yuborib turadi).

## 🎭 Teatr (ommaviy va shaxsiy xonalar)
Yoqish uchun Render → Environment ga qo'shing:
- `API_ID` va `API_HASH` — my.telegram.org → API development tools dan
- `THEATRE_TZ` = `5` (O'zbekiston vaqti; o'zgartirish shart emas)

Bot qayta ishga tushgach katalog tepasida **🎭** tugmasi chiqadi. Loglarda «Teatr: MTProto ulandi» yozuvi bo'lishi kerak.

**Ommaviy teatr:** har kuni 08:00–24:00, 2 soatlik 8 slot. Reja 00:00 da o'zi tuziladi: 1 serial qismi (har kuni shu soatda), 2 multfilm, qolgani kino. Kinolar katalogdan olinadi (faqat mp4, 2 soatgacha). Kinoning hajmi bot orqali asta-sekin aniqlanadi (eski kinolar uchun bir necha daqiqa–soat ketishi mumkin).

**Premyera:** Admin → «Qo'shish» da «⭐ Premyera» ni yoqing (yoki 🎭 → ⭐ Premyera dan kutilayotgan kinoni tanlang). Kino ertaga tanlangan vaqtda teatrda chiqadi, tugagach katalogga qo'shiladi.

**Video:** hozirgi va keyingi kino diskka yuklanadi, eskisi o'chadi. Joy yetmasa yoki yuklanmagan bo'lsa — to'g'ridan-to'g'ri Telegramdan oqim.

Build xato bersa (tgcrypto): `requirements.txt` dan `tgcrypto` qatorini o'chiring — ishlaydi, lekin sekinroq.
