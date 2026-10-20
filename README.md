# 🎬 MEGA CINEMA (Telegram bot + Mini App)

## Imkoniyatlar
- Chiroyli uzun salomlashuv + «🎬 Katalog» (Mini App) tugmasi
- Kino kodi yoki nomi bilan qidirish
- Mini App katalog: poster, janr filtri, Yangi / Top / Yil / Saralangan, qidiruv, cheksiz skroll
- Top, yangilari, saralanganlar va boshqa bo'limlar — faqat katalogda (Mini App). Botda: kod yuborish, nom bo'yicha qidiruv, do'stlarga ulashish (`t.me/bot?start=m_5`)
- Majburiy obuna (bir nechta kanal) + «Tekshirish» tugmasi
- Admin panel `/admin`: kino qo'shish/o'chirish, statistika, ommaviy xabar, kanallar, ban/unban, sozlamalar
- Kontentni himoyalash (forward va saqlashni taqiqlash) — admin sozlamalarida
- Baza zaxirasi: avtomatik adminga yuboriladi, `/restore` bilan tiklanadi

## Telefondan, bepul joylash
**DEPLOY_UZ.md** faylini o'qing (Render.com + UptimeRobot).

## Ishga tushirish (kompyuterda)
1. @BotFather → `/newbot` → token oling
2. `.env.example` ni `.env` ga nusxalang va to'ldiring (BOT_TOKEN, ADMIN_IDS, WEBAPP_URL)
3. `pip install -r requirements.txt`
4. `python main.py`

## Mini App uchun HTTPS (shart)
Telegram Mini App faqat `https://` manzilni qabul qiladi. Bot ichki serverni `PORT` (8080) da ishga tushiradi.

- **Tez sinash:** `cloudflared tunnel --url http://localhost:8080` → chiqqan `https://....trycloudflare.com` manzilni `.env` dagi `WEBAPP_URL` ga yozing
- **Doimiy:** VPS + domen + Caddy: `caddy reverse-proxy --from sizning-domen.uz --to :8080`

## Admin: kino qo'shish
1. Botga `/admin` → «🎬 Kino qo'shish»
2. Videoni yuboring → nom, tavsif, yil, janr, davlat, til, sifat, poster (ixtiyoriylari «O'tkazib yuborish» bilan)
3. Bot kino kodini beradi

**Katta kinolar:** Bot API orqali 50 MB gacha yuklash mumkin. Kattalarini o'z kanalingizga yuklab, botga **forward** qiling — hajm cheklovi yo'q.

## Majburiy obuna
1. Botni kanalga **admin** qiling
2. `/admin` → «📢 Majburiy obuna» → «Kanal qo'shish» → kanaldan post forward qiling yoki `@username` yuboring

## Eslatma
Faqat o'zingizda tarqatish huquqi bor kontentni joylang.

## Yangi imkoniyatlar

**Kodlar.** Har bo'lim o'z raqamini alohida sanaydi: `s.1, s.2` (serial), `m.1` (multfilm), `a.1` (anime).
Eski bazadagi seriallar ishga tushganda avtomatik shu tartibda qayta raqamlanadi.

**Adminlar** (Mini App → ⚙️ Sozlama → 👥 Adminlar):
- 👑 *Bosh admin* — `.env` dagi `ADMIN_IDS`. Hamma huquq, o'chirib bo'lmaydi.
- ⭐ *Starshiy admin* — hamma huquq, oddiy adminlarni qo'shadi/o'chiradi (uni faqat bosh admin tayinlaydi).
- 👤 *Admin* — faqat belgilangan huquqlar (kino qo'shish, o'chirish, chiqarish, xabar, kanallar, ban, statistika, sozlamalar)
  va faqat belgilangan bo'limlar (kino, serial, anime, multfilm).

**Hammaga xabar** (⚙️ Sozlama → 📣): matn, rasm yoki video (video 49 MB gacha). Hisobot botga keladi.

**Muqova rasmi** (faqat Mini App ichida ko'rinadi, botdagi e'lonlarga tegmaydi):
- Qo'shish/tahrirlash formasining eng tepasida rasm tanlanadi (telefon rasmi avtomatik kichraytiriladi).
- Serial/multfilm/anime (qismli) — bitta muqova, serial yaratilganda yoki keyin tahrirlashda qo'yiladi.
- Avto-yuklashdagi yakka kinolar muqovasiz tushadi, keyin tahrirlashda qo'yiladi.
- Muqova qo'yilmagan joyda `logo.png` avtomatik chiqadi (logoni almashtirish uchun shu faylni almashtiring).

## 👑 Pullik (VIP) rejim
- Mini App → ⚙️ Admin → Sozlamalar → **«Pullik rejim (VIP)»** tugmasi. O'chiq bo'lsa hamma narsa tekin; yoqilsa «Pullik» deb belgilanganlar darrov pullik bo'ladi.
- Kino/serial qo'shishda yoki ✏️ tahrirlashda **«👑 Pullik (PRO)»** tugmasi. Serialda pullik yoqilganda **«🎁 Tekin qo'shish»** chiqadi (dastlabki 3 qism tekin).
- Tariflar va chek uchun karta ma'lumoti: Sozlamalar → «👑 VIP sozlamalari».
- **Click / Payme** (Telegram ichida): BotFather → /mybots → Bot Settings → Payments → provayderni ulang va tokenni `.env` ga yozing:
  `PAYMENT_TOKEN_CLICK=...`, `PAYMENT_TOKEN_PAYME=...` (qaysi biri bo'sh bo'lsa, u usul ko'rinmaydi).
- **Chek**: foydalanuvchi chek rasmini yuboradi, chek **bitta kanalga** («✅ Tasdiqlash / ❌ Rad etish» tugmalari bilan) tushadi; tasdiqlashni faqat bosh/starshiy admin bosa oladi. Kanal ID si `.env` dagi `VIP_CHECK_CHANNEL` da (bot kanalda admin bo'lsin). Bo'sh bo'lsa — cheklar adminlar lichkasiga boradi.
- Foydalanuvchi uchun: `/vip` buyrug'i yoki Mini App tepasidagi 👑 tugmasi.

## Slayder, lenta va reklama
**Bosh sahifa slayderi** (rasm kesilmaydi — to'liq ko'rinadi, bo'sh joy xiralashtirib to'ldiriladi):
1. eng oxirgi qo'yilgan kontent — **NEW**;
2. oxirgi 24 soatda eng ko'p ko'rilgan — **TOP** (hech kim ko'rmagan bo'lsa — umumiy eng ko'p ko'rilgan);
3-4. tasodifiy: pullik rejim yoqilgan va pullik kontent bo'lsa 1 ta **PRO** + 1 ta **FREE**, bo'lmasa 2 ta **FREE**;
5. reklama. Reklama qo'yilsa — slayderning **eng boshida** chiqadi (bir nechta bo'lishi mumkin). Reklama umuman bo'lmasa — oxirida «Bu yerda sizning reklamangiz bo'lishi mumkin, pastdagi chat tugmasidan adminga yozing» turadi.

**Kartochkalar** (pastdagi ro'yxat): tepada faqat **PRO** (pullik) yoki **FREE**.

**Reklama** — admin panel → «Reklama»: rasm (tik yoki keng), havola, tugma yozuvi («O'tish») va **muddat** (1/2/3/7 kun, aniq sana-vaqt yoki cheksiz). Muddat tugagach reklama o'zi yo'qoladi. Bosilishlar soni hisoblanadi. Faqat bosh/starshiy admin (🛠 Sozlamalar huquqi) boshqaradi.


## Kanallarga e'lon va Instagram treylerlar

**Kanallarga e'lon.** Katalog → ⚙️ Admin → Avto → 🎞 Treyler → ⚙️ Kanallar sozlamasi: kino+serial, anime va multfilm uchun alohida kanal ulang
(bot har birida admin bo'lsin). «Chiqarish + xabar» bosilganda har bir yangi kontent o'z kanaliga rasm + «▶️ Tomosha qilish» tugmasi bilan tushadi.
Tugma botga olib o'tadi: oddiy kino darhol yuboriladi; serial/anime/multfilm uchun bot odatdagidek «katalogni oching» deydi.

**Instagram treylerlar.** Treyler kanalini ulang va videolarga kod yozing: `1, 2, 3` (kino) · `s.1` (serial) · `a.1` (anime) · `m.1` (multfilm).
Izoh bo'sh bo'lsa — reklama: uchala akkauntga «Silka bioda» izohi bilan yuklanadi. Takroriy kodlar o'tkazib yuborilmaydi.
Akkauntlar `.env` da (`IG_KINO_*`, `IG_ANIME_*`, `IG_MULT_*`). Avto → 🎞 Treyler bo'limida har bo'lim bo'yicha nechta kutayotgani ko'rinadi:
bo'lim yoki «Hammasini yuklash» tugmasi bilan yuklanadi, natija botga hisobot bo'lib keladi.
Eslatma: treyler Telegram orqali 20 MB gacha yuklab olinadi; serverda `ffmpeg` (moviepy bilan keladi) kerak bo'ladi.
