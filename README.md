# Discord Botu (Keysiz AI)

AI için API anahtarı gerekmez (ücretsiz Pollinations endpoint'i). Sadece Discord bot tokenı lazım.

## Kurulum
1. https://discord.com/developers/applications → New Application → Bot → Token'ı kopyala.
2. Aynı sayfada **Privileged Gateway Intents** altında **Message Content Intent** ve **Server Members Intent** aç.
3. OAuth2 → URL Generator → `bot` + `applications.commands` seç, botu sunucuna ekle.
4. Çalıştır:

```bash
pip install -r requirements.txt
export DISCORD_TOKEN="tokenin"        # Windows: set DISCORD_TOKEN=tokenin
python bot.py
```

## Özellikler
- **AI:** /sor, /odev, /acikla, /cevir, /ozet, /hikaye, botu etiketleyince sohbet (kanal hafızalı)
- **Eğlence:** /espri, /ai_espri, /yazitura, /zar, /8top, /ship, /tkm, /sayitahmin, /cesaret_dogruluk, /roast
- **Araçlar:** /hesapla, /anket, /hatirlat, /avatar, /kullanici, /sunucu, /ping, /temizle, /yardim
- **Ters mod:** `973573136685608970` ID'li kullanıcıya her yerde ters, alaycı cevap verir (etiketlerse, yanıtlarsa, komut kullanırsa, ayrıca mesajlarına %15 ihtimalle kendiliğinden). Oran `bot.py` içinde `RASTGELE_TERS_ORAN`.

## Notlar
- AI servisi ücretsiz/üçüncü taraf; bazen yavaşlayabilir veya adresi değişebilir. `AI_URL` değişkenini güncellemen yeterli.
- Slash komutların görünmesi ilk açılışta birkaç dakika sürebilir.
