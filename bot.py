import os
import re
import ast
import time
import random
import asyncio
import operator
from collections import defaultdict, deque

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ============================================================
#  AYARLAR
# ============================================================
TOKEN = os.getenv("DISCORD_TOKEN", "")
HEDEF_ID = 973573136685608970          # Ters cevap verilecek kullanıcı
RASTGELE_TERS_ORAN = 0.15              # Hedefin mesajlarına kendiliğinden cevap verme olasılığı (0-1)
ISIM_TETIK = ["n0rx123"]               # Bu isimler mesajda geçerse bot ters moda girer
ISIM_BEKLEME = 8                       # Aynı kanalda tekrar tepki vermeden önce bekleme (saniye)

# Keysiz AI (API anahtarı gerekmez). Servis değişirse sadece burayı güncelle.
AI_URL = "https://text.pollinations.ai/openai"
AI_MODEL = "openai"

SISTEM_NORMAL = (
    "Sen Türkçe konuşan, samimi ve yardımsever bir Discord botusun. "
    "Kısa, net ve anlaşılır cevap ver. Cevapların 1800 karakteri geçmesin."
)
SISTEM_ODEV = (
    "Sen sabırlı bir öğretmensin. Öğrencinin ödev sorusunu adım adım, "
    "anlaşılır şekilde çöz ve mantığını açıkla. Sonda kısa bir özet ver. "
    "Türkçe cevap ver. 1800 karakteri geçme."
)
SISTEM_TERS = (
    "Sen Türkçe konuşan, ters, huysuz, aşırı alaycı ve sivri dilli bir Discord botusun. "
    "Karşındaki arkadaş grubundan biri ve onunla atışıyorsun. Cevapların agresif, "
    "iğneleyici ve komik olsun; sorusuna cevap ver ama sürekli dalga geç, terslen. "
    "SINIRLAR: küfür, hakaret içeren ağır sözler, ırk/din/cinsiyet/engel/aile gibi "
    "konulara laf atma, tehdit ve gerçek kişisel bilgi YOK. Sadece esprili 'ters' tavır. "
    "En fazla 3-4 cümle yaz."
)

TERS_HAZIR = [
    "Yine mi sen? Bugün de mi başıma geldin...",
    "Sorunun cevabı belli, ama sana söylemeye de üşendim.",
    "Vay vay, kim gelmiş. Alkış yok mu?",
    "Beni uyandırdığına değecek bir şey söyle bari.",
    "Bu mesajı yazarken bir an bile düşündün mü? Hayır, belli.",
    "Ya bırak şimdi, işim var. (Yok aslında ama seninle uğraşmayacağım.)",
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

gecmis = defaultdict(lambda: deque(maxlen=8))  # kanal başına kısa hafıza
session: aiohttp.ClientSession | None = None
son_isim_tepki: dict[int, float] = {}
AI_KILIT = asyncio.Lock()
AI_HATA = "AI servisi şu an cevap vermiyor, biraz sonra tekrar dene."


# ============================================================
#  YARDIMCILAR
# ============================================================
async def ai_sor(prompt: str, sistem: str = SISTEM_NORMAL, kanal_id: int | None = None) -> str:
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()
    mesajlar = [{"role": "system", "content": sistem}]
    if kanal_id is not None:
        mesajlar += list(gecmis[kanal_id])
    mesajlar.append({"role": "user", "content": prompt})
    cevap = None
    async with AI_KILIT:  # anonim kullanımda aynı anda tek istek (429'u önler)
        for deneme in range(4):
            try:
                async with session.post(
                    AI_URL,
                    json={"model": AI_MODEL, "messages": mesajlar},
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as r:
                    if r.status == 200:
                        veri = await r.json()
                        cevap = veri["choices"][0]["message"]["content"].strip()
                        if cevap:
                            break
                    else:
                        print(f"[AI] HTTP {r.status} (deneme {deneme + 1})")
            except Exception as e:
                print(f"[AI] Hata: {e!r} (deneme {deneme + 1})")
            await asyncio.sleep(2 + deneme * 2)  # 2, 4, 6 sn bekle ve tekrar dene
    if not cevap:
        return AI_HATA
    if kanal_id is not None:
        gecmis[kanal_id].append({"role": "user", "content": prompt})
        gecmis[kanal_id].append({"role": "assistant", "content": cevap})
    return cevap[:1900]


def hedef_mi(user: discord.abc.User) -> bool:
    return user.id == HEDEF_ID


def sistem_sec(user, normal=SISTEM_NORMAL):
    return SISTEM_TERS if hedef_mi(user) else normal


# Güvenli hesap makinesi
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def guvenli_hesap(node):
    if isinstance(node, ast.Expression):
        return guvenli_hesap(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = guvenli_hesap(node.left), guvenli_hesap(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 100:
            raise ValueError("Üs çok büyük")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](guvenli_hesap(node.operand))
    raise ValueError("Geçersiz ifade")


# ============================================================
#  OLAYLAR
# ============================================================
SES_KANAL_ID = int(os.getenv("SES_KANAL_ID", "0") or 0)  # Railway'de açılışta girilecek ses kanalı
ses_hedef: dict[int, int] = {}  # sunucu_id -> kalınacak ses kanalı id


async def sese_baglan(kanal: discord.abc.Connectable):
    guild = kanal.guild
    vc = guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != kanal.id:
            await vc.move_to(kanal)
        return vc
    if vc:
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass
    return await kanal.connect(reconnect=True, self_deaf=True)


@tasks.loop(seconds=30)
async def ses_bekci():
    """Her 30 sn'de ses bağlantısını kontrol eder; düştüyse/taşındıysa geri döner."""
    for guild_id, kanal_id in list(ses_hedef.items()):
        guild = bot.get_guild(guild_id)
        kanal = bot.get_channel(kanal_id)
        if guild is None or kanal is None:
            continue
        vc = guild.voice_client
        if vc is None or not vc.is_connected() or vc.channel.id != kanal_id:
            try:
                await sese_baglan(kanal)
                print(f"[SES] {guild.name} / {kanal.name} kanalına bağlandı.")
            except Exception as e:
                print(f"[SES] Bağlanamadı: {e!r}")


@bot.tree.command(name="gel", description="Ses kanalına girer ve çıkmadan AFK bekler")
@app_commands.guild_only()
@app_commands.describe(kanal="Girilecek ses kanalı (boşsa bulunduğun kanal)")
async def gel(inter: discord.Interaction, kanal: discord.VoiceChannel = None):
    hedef = kanal or (inter.user.voice.channel if inter.user.voice else None)
    if hedef is None:
        return await inter.response.send_message("Bir ses kanalına gir ya da kanal seç.", ephemeral=True)
    await inter.response.defer()
    try:
        await sese_baglan(hedef)
    except Exception as e:
        return await inter.followup.send(f"Bağlanamadım: `{e}`\nBotun kanala Bağlan/Konuş yetkisi var mı?")
    ses_hedef[inter.guild_id] = hedef.id
    await inter.followup.send(
        f"🔊 **{hedef.name}** kanalındayım, çıkmayacağım.\n"
        f"Railway yeniden başlasa da dönmesi için Variables'a `SES_KANAL_ID` = `{hedef.id}` ekle."
    )


@bot.tree.command(name="git", description="Ses kanalından çıkar")
@app_commands.guild_only()
async def git(inter: discord.Interaction):
    ses_hedef.pop(inter.guild_id, None)
    vc = inter.guild.voice_client
    if vc:
        await vc.disconnect(force=True)
        return await inter.response.send_message("👋 Çıktım.")
    await inter.response.send_message("Zaten bir ses kanalında değilim.", ephemeral=True)


ses_atma_aktif = True  # /sesatma komutuyla açılıp kapanır

SES_ATMA_LAFLARI = [
    "Hop! Nereye? Bu kanalda işin yok.",
    "Sese girmeye çalıştın ama kapıdan döndün. Güle güle.",
    "Giriş yasak. Bugünlük de bu kadar.",
    "Yine mi sen? Dışarı.",
]


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Hedef kullanıcı bir ses kanalına girerse otomatik olarak atılır."""
    if not ses_atma_aktif or member.id != HEDEF_ID:
        return
    if after.channel is None or after.channel == before.channel:
        return
    kanal = after.channel
    try:
        await member.move_to(None, reason="Otomatik ses atma")
    except discord.Forbidden:
        print("[SESATMA] Yetki yok: botun 'Üyeleri Taşı' (Move Members) yetkisi ve rolü hedefin üstünde olmalı.")
        return
    except discord.HTTPException as e:
        print(f"[SESATMA] Hata: {e!r}")
        return
    try:
        await kanal.send(f"{member.mention} {random.choice(SES_ATMA_LAFLARI)}", delete_after=15)
    except Exception:
        pass


@bot.tree.command(name="sesatma", description="Hedef kullanıcıyı sesten otomatik atmayı aç/kapat")
@app_commands.guild_only()
@app_commands.checks.has_permissions(move_members=True)
@app_commands.choices(durum=[
    app_commands.Choice(name="Aç", value="ac"),
    app_commands.Choice(name="Kapat", value="kapat"),
])
async def sesatma(inter: discord.Interaction, durum: app_commands.Choice[str]):
    global ses_atma_aktif
    ses_atma_aktif = durum.value == "ac"
    await inter.response.send_message(f"Otomatik ses atma: **{'AÇIK' if ses_atma_aktif else 'KAPALI'}**")


@sesatma.error
async def sesatma_hata(inter: discord.Interaction, hata):
    if isinstance(hata, app_commands.MissingPermissions):
        await inter.response.send_message("Yetkin yok.", ephemeral=True)


@bot.event
async def on_ready():
    if SES_KANAL_ID:
        k = bot.get_channel(SES_KANAL_ID)
        if k is not None:
            ses_hedef[k.guild.id] = k.id
    if not ses_bekci.is_running():
        ses_bekci.start()
    synced = await bot.tree.sync()
    print(f"[SYNC] {len(synced)} komut: {', '.join(c.name for c in synced)}")
    await bot.change_presence(activity=discord.Game("/yardim | @beni etiketle"))
    print(f"{bot.user} aktif! {len(bot.guilds)} sunucuda.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    etiketlendi = bot.user in message.mentions
    yanit_mi = False
    if message.reference and message.reference.resolved:
        ref = message.reference.resolved
        yanit_mi = isinstance(ref, discord.Message) and ref.author.id == bot.user.id

    # Hedefin ismi geçerse / etiketlenirse (başkası yazdıysa) ters mod
    icerik = message.content.lower()
    isim_gecti = any(t in icerik for t in ISIM_TETIK) or any(u.id == HEDEF_ID for u in message.mentions)
    if isim_gecti and not hedef_mi(message.author):
        simdi = time.time()
        if simdi - son_isim_tepki.get(message.channel.id, 0) >= ISIM_BEKLEME:
            son_isim_tepki[message.channel.id] = simdi
            async with message.channel.typing():
                cevap = await ai_sor(
                    f"Sunucuda biri şunu yazdı: \"{message.content[:400]}\"\n"
                    f"Mesajda {ISIM_TETIK[0]} adlı arkadaşımızdan bahsediliyor. "
                    f"Ona ters ters, alaycı ve komik bir şekilde laf at, mesaja da değin.",
                    SISTEM_TERS,
                )
            if cevap == AI_HATA:
                cevap = random.choice(TERS_HAZIR)
            await message.reply(cevap, mention_author=False)
        return

    # Hedef kullanıcı: ters mod
    if hedef_mi(message.author):
        if etiketlendi or yanit_mi or random.random() < RASTGELE_TERS_ORAN:
            metin = re.sub(r"<@!?\d+>", "", message.content).strip()
            async with message.channel.typing():
                if metin:
                    cevap = await ai_sor(metin, SISTEM_TERS, message.channel.id)
                else:
                    cevap = random.choice(TERS_HAZIR)
            if cevap == AI_HATA:
                cevap = random.choice(TERS_HAZIR)
            await message.reply(cevap, mention_author=False)
            return

    # Diğerleri: etiketlenirse / yanıtlanırsa AI sohbeti
    elif etiketlendi or yanit_mi:
        metin = re.sub(r"<@!?\d+>", "", message.content).strip()
        if metin:
            async with message.channel.typing():
                cevap = await ai_sor(metin, SISTEM_NORMAL, message.channel.id)
            await message.reply(cevap, mention_author=False)
            return

    await bot.process_commands(message)


# ============================================================
#  AI / YARDIM KOMUTLARI
# ============================================================
@bot.tree.command(name="sor", description="Yapay zekaya her şeyi sor")
@app_commands.describe(soru="Sorun nedir?")
async def sor(inter: discord.Interaction, soru: str):
    await inter.response.defer()
    cevap = await ai_sor(soru, sistem_sec(inter.user), inter.channel_id)
    await inter.followup.send(f"**Soru:** {soru[:300]}\n\n{cevap}")


@bot.tree.command(name="odev", description="Ödev sorusunu adım adım çözer")
@app_commands.describe(soru="Ödev sorusu", ders="Ders (opsiyonel)")
async def odev(inter: discord.Interaction, soru: str, ders: str = ""):
    await inter.response.defer()
    prompt = f"Ders: {ders}\nSoru: {soru}" if ders else soru
    cevap = await ai_sor(prompt, sistem_sec(inter.user, SISTEM_ODEV))
    await inter.followup.send(f"📚 **Ödev Yardımı**\n\n{cevap}")


@bot.tree.command(name="cevir", description="Metni başka bir dile çevirir")
@app_commands.describe(metin="Çevrilecek metin", dil="Hedef dil (örn: İngilizce)")
async def cevir(inter: discord.Interaction, metin: str, dil: str = "İngilizce"):
    await inter.response.defer()
    cevap = await ai_sor(
        f"Şu metni {dil} diline çevir, sadece çeviriyi yaz:\n{metin}",
        "Sen profesyonel bir çevirmensin.",
    )
    await inter.followup.send(f"🌍 **{dil}:**\n{cevap}")


@bot.tree.command(name="ozet", description="Uzun metni özetler")
@app_commands.describe(metin="Özetlenecek metin")
async def ozet(inter: discord.Interaction, metin: str):
    await inter.response.defer()
    cevap = await ai_sor(f"Şunu kısaca özetle:\n{metin}", SISTEM_NORMAL)
    await inter.followup.send(f"📝 **Özet:**\n{cevap}")


@bot.tree.command(name="acikla", description="Bir konuyu basitçe anlatır")
@app_commands.describe(konu="Konu", seviye="Anlatım seviyesi")
@app_commands.choices(seviye=[
    app_commands.Choice(name="5 yaşındaki çocuk gibi", value="5 yaşındaki bir çocuğa anlatır gibi"),
    app_commands.Choice(name="Lise seviyesi", value="lise öğrencisine anlatır gibi"),
    app_commands.Choice(name="Üniversite seviyesi", value="üniversite seviyesinde ayrıntılı"),
])
async def acikla(inter: discord.Interaction, konu: str, seviye: app_commands.Choice[str]):
    await inter.response.defer()
    cevap = await ai_sor(f"{konu} konusunu {seviye.value} anlat.", sistem_sec(inter.user))
    await inter.followup.send(f"💡 **{konu}**\n\n{cevap}")


@bot.tree.command(name="hesapla", description="Matematik işlemi yapar")
@app_commands.describe(ifade="Örn: (5+3)*2**4")
async def hesapla(inter: discord.Interaction, ifade: str):
    try:
        sonuc = guvenli_hesap(ast.parse(ifade, mode="eval"))
        await inter.response.send_message(f"🧮 `{ifade}` = **{sonuc}**")
    except Exception:
        await inter.response.send_message("Geçersiz işlem. Sadece + - * / ** % // kullan.", ephemeral=True)


@bot.tree.command(name="hafiza_sil", description="Bu kanaldaki AI sohbet hafızasını sıfırlar")
async def hafiza_sil(inter: discord.Interaction):
    gecmis[inter.channel_id].clear()
    await inter.response.send_message("🧹 Hafıza temizlendi.")


# ============================================================
#  EĞLENCE
# ============================================================
ESPRILER = [
    "Bilgisayar neden soğuk? Çünkü Windows'u açık.",
    "Programcı markete gider: 'Bir ekmek al, yumurta varsa 6 tane al.' 6 ekmek getirir.",
    "Neden bilgisayarlar hiç acıkmaz? Çünkü bir sürü byte'ı var.",
    "Kahve içmeyen yazılımcı = syntax error.",
    "Dün gece uyumadım, bug'larla savaştım. Bug'lar kazandı.",
]
SEKIZ_TOP = [
    "Kesinlikle evet.", "Bence evet.", "Olabilir.", "Şimdi söylemeyeyim.",
    "Hiç sanmam.", "Kesinlikle hayır.", "Tekrar sor.", "Belirtiler olumlu.",
]
CESARET = [
    "Son attığın mesajı sesli oku.", "Profil fotoğrafını 1 saat değiştir.",
    "En utanç verici şarkını paylaş.", "Birine komik bir hikaye anlat.",
]
DOGRULUK = [
    "En son kimi stalkladın?", "Hiç yalan söyleyip sınıfı kırdın mı?",
    "En garip korkun ne?", "Gizli bir yeteneğin var mı?",
]


@bot.tree.command(name="espri", description="Rastgele espri")
async def espri(inter: discord.Interaction):
    await inter.response.send_message(f"😂 {random.choice(ESPRILER)}")


@bot.tree.command(name="ai_espri", description="AI konuya özel espri yazar")
@app_commands.describe(konu="Espri konusu")
async def ai_espri(inter: discord.Interaction, konu: str):
    await inter.response.defer()
    cevap = await ai_sor(f"'{konu}' hakkında kısa, komik, kırıcı olmayan bir espri yaz.")
    await inter.followup.send(f"😂 {cevap}")


@bot.tree.command(name="yazitura", description="Yazı tura at")
async def yazitura(inter: discord.Interaction):
    await inter.response.send_message(f"🪙 **{random.choice(['Yazı', 'Tura'])}**")


@bot.tree.command(name="zar", description="Zar at")
@app_commands.describe(yuz="Zarın yüz sayısı")
async def zar(inter: discord.Interaction, yuz: app_commands.Range[int, 2, 1000] = 6):
    await inter.response.send_message(f"🎲 d{yuz}: **{random.randint(1, yuz)}**")


@bot.tree.command(name="8top", description="Sihirli 8 topa sor")
@app_commands.describe(soru="Evet/hayır sorusu")
async def top8(inter: discord.Interaction, soru: str):
    await inter.response.send_message(f"🎱 **{soru}**\n{random.choice(SEKIZ_TOP)}")


@bot.tree.command(name="ship", description="İki kişinin uyumunu ölç")
async def ship(inter: discord.Interaction, kisi1: discord.Member, kisi2: discord.Member):
    rnd = random.Random(min(kisi1.id, kisi2.id) + max(kisi1.id, kisi2.id))
    oran = rnd.randint(0, 100)
    bar = "█" * (oran // 10) + "░" * (10 - oran // 10)
    await inter.response.send_message(f"💘 {kisi1.display_name} x {kisi2.display_name}\n`{bar}` **%{oran}**")


@bot.tree.command(name="tkm", description="Taş kağıt makas oyna")
@app_commands.choices(secim=[
    app_commands.Choice(name="Taş", value="tas"),
    app_commands.Choice(name="Kağıt", value="kagit"),
    app_commands.Choice(name="Makas", value="makas"),
])
async def tkm(inter: discord.Interaction, secim: app_commands.Choice[str]):
    bot_secim = random.choice(["tas", "kagit", "makas"])
    kazan = {("tas", "makas"), ("kagit", "tas"), ("makas", "kagit")}
    if secim.value == bot_secim:
        sonuc = "Berabere!"
    elif (secim.value, bot_secim) in kazan:
        sonuc = "Sen kazandın! 🎉"
    else:
        sonuc = "Ben kazandım! 😎"
    isim = {"tas": "Taş", "kagit": "Kağıt", "makas": "Makas"}
    await inter.response.send_message(f"Sen: **{isim[secim.value]}** | Ben: **{isim[bot_secim]}**\n{sonuc}")


@bot.tree.command(name="sayitahmin", description="1-100 arası sayı tahmin et")
async def sayitahmin(inter: discord.Interaction):
    sayi = random.randint(1, 100)
    await inter.response.send_message("🔢 1-100 arası bir sayı tuttum! 30 saniyede kanala tahminini yaz (6 hakkın var).")

    def kontrol(m):
        return m.author == inter.user and m.channel == inter.channel and m.content.isdigit()

    for hak in range(6, 0, -1):
        try:
            m = await bot.wait_for("message", check=kontrol, timeout=30)
        except asyncio.TimeoutError:
            return await inter.channel.send(f"⏰ Süre doldu! Sayı **{sayi}** idi.")
        t = int(m.content)
        if t == sayi:
            return await inter.channel.send(f"🎉 Bildin! Sayı **{sayi}**.")
        await inter.channel.send(f"{'⬆️ Daha büyük' if t < sayi else '⬇️ Daha küçük'} | Kalan hak: {hak - 1}")
    await inter.channel.send(f"Kaybettin! Sayı **{sayi}** idi.")


@bot.tree.command(name="cesaret_dogruluk", description="Cesaret mi doğruluk mu?")
@app_commands.choices(tur=[
    app_commands.Choice(name="Cesaret", value="c"),
    app_commands.Choice(name="Doğruluk", value="d"),
])
async def cd(inter: discord.Interaction, tur: app_commands.Choice[str]):
    liste, e = (CESARET, "🔥") if tur.value == "c" else (DOGRULUK, "🕵️")
    await inter.response.send_message(f"{e} **{tur.name}:** {random.choice(liste)}")


@bot.tree.command(name="roast", description="Birine hafif, komik bir laf at")
async def roast(inter: discord.Interaction, kisi: discord.Member):
    if kisi.bot:
        return await inter.response.send_message("Botlarla uğraşma 😤", ephemeral=True)
    await inter.response.defer()
    cevap = await ai_sor(
        f"'{kisi.display_name}' için 2 cümlelik komik, eğlenceli, kırıcı olmayan bir laf yaz. "
        "Küfür, ağır hakaret ve kişisel konular olmasın.",
    )
    await inter.followup.send(f"🔥 {kisi.mention} {cevap}")


@bot.tree.command(name="hikaye", description="AI kısa hikaye yazar")
async def hikaye(inter: discord.Interaction, konu: str):
    await inter.response.defer()
    cevap = await ai_sor(f"'{konu}' konulu kısa, ilginç bir hikaye yaz (en fazla 1200 karakter).")
    await inter.followup.send(f"📖 **{konu}**\n\n{cevap}")


# ============================================================
#  FAYDALI / SUNUCU
# ============================================================
@bot.tree.command(name="ping", description="Gecikmeyi gösterir")
async def ping(inter: discord.Interaction):
    await inter.response.send_message(f"🏓 {round(bot.latency * 1000)} ms")


@bot.tree.command(name="avatar", description="Profil fotoğrafı")
async def avatar(inter: discord.Interaction, kisi: discord.Member = None):
    kisi = kisi or inter.user
    e = discord.Embed(title=f"{kisi.display_name}", color=discord.Color.blurple())
    e.set_image(url=kisi.display_avatar.url)
    await inter.response.send_message(embed=e)


@bot.tree.command(name="kullanici", description="Kullanıcı bilgisi")
async def kullanici(inter: discord.Interaction, kisi: discord.Member = None):
    kisi = kisi or inter.user
    e = discord.Embed(title=str(kisi), color=kisi.color)
    e.set_thumbnail(url=kisi.display_avatar.url)
    e.add_field(name="ID", value=kisi.id)
    e.add_field(name="Hesap", value=discord.utils.format_dt(kisi.created_at, "R"))
    e.add_field(name="Katılım", value=discord.utils.format_dt(kisi.joined_at, "R") if kisi.joined_at else "?")
    await inter.response.send_message(embed=e)


@bot.tree.command(name="sunucu", description="Sunucu bilgisi")
@app_commands.guild_only()
async def sunucu(inter: discord.Interaction):
    g = inter.guild
    e = discord.Embed(title=g.name, color=discord.Color.green())
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="Üye", value=g.member_count)
    e.add_field(name="Kanal", value=len(g.channels))
    e.add_field(name="Rol", value=len(g.roles))
    e.add_field(name="Kuruluş", value=discord.utils.format_dt(g.created_at, "D"))
    await inter.response.send_message(embed=e)


@bot.tree.command(name="anket", description="Evet/Hayır anketi")
async def anket(inter: discord.Interaction, soru: str):
    await inter.response.send_message(f"📊 **{soru}**")
    msg = await inter.original_response()
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


@bot.tree.command(name="hatirlat", description="Hatırlatıcı kur")
@app_commands.describe(dakika="Kaç dakika sonra", not_="Hatırlatma notu")
async def hatirlat(inter: discord.Interaction, dakika: app_commands.Range[int, 1, 1440], not_: str):
    await inter.response.send_message(f"⏰ {dakika} dk sonra hatırlatacağım.")
    await asyncio.sleep(dakika * 60)
    await inter.channel.send(f"{inter.user.mention} ⏰ Hatırlatma: **{not_}**")


@bot.tree.command(name="temizle", description="Mesaj siler (yetkili)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def temizle(inter: discord.Interaction, adet: app_commands.Range[int, 1, 100]):
    await inter.response.defer(ephemeral=True)
    silinen = await inter.channel.purge(limit=adet)
    await inter.followup.send(f"🗑️ {len(silinen)} mesaj silindi.")


@temizle.error
async def temizle_hata(inter: discord.Interaction, hata):
    if isinstance(hata, app_commands.MissingPermissions):
        await inter.response.send_message("Yetkin yok.", ephemeral=True)


@bot.tree.command(name="yardim", description="Komut listesi")
async def yardim(inter: discord.Interaction):
    e = discord.Embed(title="🤖 Komutlar", color=discord.Color.blurple())
    e.add_field(name="🧠 AI", value="`/sor` `/odev` `/acikla` `/cevir` `/ozet` `/hikaye` `/hafiza_sil`\nBeni etiketleyerek de sohbet edebilirsin.", inline=False)
    e.add_field(name="🎮 Eğlence", value="`/espri` `/ai_espri` `/yazitura` `/zar` `/8top` `/ship` `/tkm` `/sayitahmin` `/cesaret_dogruluk` `/roast`", inline=False)
    e.add_field(name="🛠️ Araçlar", value="`/hesapla` `/anket` `/hatirlat` `/avatar` `/kullanici` `/sunucu` `/ping` `/temizle`", inline=False)
    await inter.response.send_message(embed=e)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN ortam değişkeni tanımlı değil. .env veya export ile ver.")
    bot.run(TOKEN)
