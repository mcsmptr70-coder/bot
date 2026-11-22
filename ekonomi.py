import os
import json
import time
import random
import discord
from discord import app_commands
from discord.ext import commands

# ============================================================
#  EKONOMİ, COİN İLE HEDEFLEME VE GÖREV SİSTEMİ (ekonomi.py)
# ============================================================
DATA_DIR = os.getenv("DATA_DIR", ".")
EKONOMI_DOSYA = os.path.join(DATA_DIR, "ekonomi.json")

PARA_BIRIMI = "🪙"
GUNLUK_ODUL = 250
GUNLUK_SERI_BONUS = 50
GUNLUK_MAX_BONUS = 500

CALIS_BEKLEME = 300       # 5 dakika
AVLA_BEKLEME = 30         # 30 saniye
MIN_BAHIS = 10
MAX_BAHIS = 10000
MIN_GOREV_ODUL = 50
MAX_AKTIF_GOREV = 3

# Süre (saniye) -> Fiyat (coin)
HEDEF_PAKETLERI = {
    "15dk":  (15 * 60,        300,   "15 Dakika"),
    "1saat": (60 * 60,        900,   "1 Saat"),
    "6saat": (6 * 60 * 60,    3500,  "6 Saat"),
    "24saat": (24 * 60 * 60,  10000, "24 Saat"),
}

KORUNMA_PAKETLERI = {
    "1saat":  (60 * 60,       400,   "1 Saat"),
    "6saat":  (6 * 60 * 60,   1500,  "6 Saat"),
    "24saat": (24 * 60 * 60,  4500,  "24 Saat"),
}

CALIS_ISLERI = [
    "Discord sunucusunda moderatörlük yaptın",
    "Kod yazarken 47 bug çözdün",
    "Kafede gece vardiyasına kaldın",
    "Kripto grafiğine bakıp şansına kâr ettin",
    "Eski bilgisayar parçalarını sattın",
    "Sunucuda meme tasarladın",
    "Kuryelik yapıp bahşiş topladın",
    "Oyun hesabı boostladın",
]

# (ağırlık, nadirlik, emoji, isim, min_coin, max_coin)
AV_TABLOSU = [
    (45, "Sıradan",   "🐭", "Fare",        20,  45),
    (30, "Sıradan",   "🐇", "Tavşan",      35,  65),
    (14, "Nadir",     "🦊", "Tilki",       75,  130),
    (7,  "Destansı",  "🐺", "Kurt",        150, 240),
    (3,  "Efsanevi",  "🦁", "Aslan",       280, 420),
    (1,  "Mitik",     "🐉", "Ejderha",     500, 750),
]

SLOT_SEMBOLLER = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
SLOT_AGIRLIKLAR = [35, 30, 20, 10, 5]


def _sure_formatla(saniye: float) -> str:
    saniye = max(0, int(saniye))
    saat, kalan = divmod(saniye, 3600)
    dakika, sn = divmod(kalan, 60)
    parcalar = []
    if saat:
        parcalar.append(f"{saat} sa")
    if dakika:
        parcalar.append(f"{dakika} dk")
    if sn or not parcalar:
        parcalar.append(f"{sn} sn")
    return " ".join(parcalar)


class Ekonomi(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.veri: dict = {
            "bakiyeler": {},
            "seri": {},
            "beklemeler": {},
            "korunmalar": {},
            "gecici_hedefler": {},
            "gorevler": {},
            "son_gorev_id": 0,
        }
        self._yukle()
        self._hedefleri_senkronize_et()

    # ------------------------------------------------------------
    #  KALICI VERİ YÖNETİMİ
    # ------------------------------------------------------------
    def _yukle(self):
        try:
            with open(EKONOMI_DOSYA, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in self.veri:
                    if k in d:
                        self.veri[k] = d[k]
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[EKONOMI] Yüklenemedi: {e!r}")

    def _kaydet(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            gecici = EKONOMI_DOSYA + ".tmp"
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(self.veri, f, ensure_ascii=False)
            os.replace(gecici, EKONOMI_DOSYA)
        except Exception as e:
            print(f"[EKONOMI] Kaydedilemedi: {e!r}")

    def _hedefleri_senkronize_et(self):
        """Kayıtlı geçici hedefleri bot.gecici_hedefler sözlüğüne aktarır."""
        simdi = time.time()
        gecici_hedefler = getattr(self.bot, "gecici_hedefler", None)
        if gecici_hedefler is None:
            gecici_hedefler = {}
            self.bot.gecici_hedefler = gecici_hedefler
        temiz = {}
        for uid_str, bitis in list(self.veri["gecici_hedefler"].items()):
            if bitis > simdi:
                temiz[uid_str] = bitis
                gecici_hedefler[int(uid_str)] = bitis
        self.veri["gecici_hedefler"] = temiz

    def _bakiye_al(self, user_id: int) -> int:
        return int(self.veri["bakiyeler"].get(str(user_id), 0))

    def _bakiye_ekle(self, user_id: int, miktar: int) -> int:
        anahtar = str(user_id)
        yeni = max(0, self._bakiye_al(user_id) + miktar)
        self.veri["bakiyeler"][anahtar] = yeni
        self._kaydet()
        return yeni

    def _korunma_kalan(self, user_id: int) -> float:
        anahtar = str(user_id)
        bitis = float(self.veri["korunmalar"].get(anahtar, 0))
        kalan = bitis - time.time()
        if kalan <= 0:
            self.veri["korunmalar"].pop(anahtar, None)
            return 0.0
        return kalan

    def _hedef_kalan(self, user_id: int) -> float:
        gecici_hedefler = getattr(self.bot, "gecici_hedefler", {})
        bitis = float(gecici_hedefler.get(user_id, 0))
        kalan = bitis - time.time()
        if kalan <= 0:
            gecici_hedefler.pop(user_id, None)
            self.veri["gecici_hedefler"].pop(str(user_id), None)
            return 0.0
        return kalan

    # ============================================================
    #  💰 EKONOMİ KOMUTLARI (OwO Tarzı)
    # ============================================================
    @app_commands.command(name="bakiye", description="Coin bakiyeni ve durumunu gör")
    @app_commands.describe(kisi="Bakiyesine bakılacak kişi (boşsa kendin)")
    async def bakiye(self, inter: discord.Interaction, kisi: discord.Member = None):
        hedef = kisi or inter.user
        coin = self._bakiye_al(hedef.id)
        seri_bilgi = self.veri["seri"].get(str(hedef.id), {})
        seri = seri_bilgi.get("gun", 0)
        korunma = self._korunma_kalan(hedef.id)
        hedef_sure = self._hedef_kalan(hedef.id)

        e = discord.Embed(title=f"{PARA_BIRIMI} {hedef.display_name} — Cüzdan", color=discord.Color.gold())
        e.set_thumbnail(url=hedef.display_avatar.url)
        e.add_field(name="Bakiye", value=f"**{coin:,}** {PARA_BIRIMI}", inline=True)
        e.add_field(name="Günlük Seri", value=f"🔥 **{seri}** gün", inline=True)
        durumlar = []
        if korunma > 0:
            durumlar.append(f"🛡️ Korunma aktif (`{_sure_formatla(korunma)}`)")
        if hedef_sure > 0:
            durumlar.append(f"🎯 Hedefte (`{_sure_formatla(hedef_sure)}`)")
        if durumlar:
            e.add_field(name="Durum", value="\n".join(durumlar), inline=False)
        await inter.response.send_message(embed=e)

    @app_commands.command(name="gunluk", description="Günlük coin ödülünü al (seri bonusu kazan)")
    async def gunluk(self, inter: discord.Interaction):
        uid = str(inter.user.id)
        simdi = time.time()
        bilgi = self.veri["seri"].get(uid, {"gun": 0, "son": 0.0})
        gecen = simdi - float(bilgi.get("son", 0.0))

        if gecen < 24 * 3600:
            kalan = 24 * 3600 - gecen
            return await inter.response.send_message(
                f"⏳ Günlük ödülünü zaten aldın! Tekrar almak için **{_sure_formatla(kalan)}** bekle.",
                ephemeral=True,
            )

        # 48 saat içinde alınırsa seri devam eder, yoksa sıfırlanır
        gun = int(bilgi.get("gun", 0)) + 1 if gecen < 48 * 3600 else 1
        bonus = min((gun - 1) * GUNLUK_SERI_BONUS, GUNLUK_MAX_BONUS)
        toplam = GUNLUK_ODUL + bonus
        self.veri["seri"][uid] = {"gun": gun, "son": simdi}
        yeni_bakiye = self._bakiye_ekle(inter.user.id, toplam)

        await inter.response.send_message(
            f"🎁 Günlük ödülünü aldın: **+{toplam:,}** {PARA_BIRIMI} "
            f"(Temel: `{GUNLUK_ODUL}` + Seri bonusu: `{bonus}`)\n"
            f"🔥 Seri: **{gun} gün** | Yeni bakiye: **{yeni_bakiye:,}** {PARA_BIRIMI}"
        )

    @app_commands.command(name="calis", description="Çalışarak coin kazan (5 dk bekleme)")
    async def calis(self, inter: discord.Interaction):
        uid = str(inter.user.id)
        simdi = time.time()
        bekleme = self.veri["beklemeler"].setdefault(uid, {})
        kalan = CALIS_BEKLEME - (simdi - float(bekleme.get("calis", 0.0)))
        if kalan > 0:
            return await inter.response.send_message(
                f"⏳ Biraz dinlen! Tekrar çalışmak için **{_sure_formatla(kalan)}** bekle.",
                ephemeral=True,
            )

        bekleme["calis"] = simdi
        kazanc = random.randint(60, 150)
        yeni = self._bakiye_ekle(inter.user.id, kazanc)
        is_metni = random.choice(CALIS_ISLERI)
        await inter.response.send_message(
            f"💼 {is_metni} ve **+{kazanc:,}** {PARA_BIRIMI} kazandın! (Bakiye: **{yeni:,}** {PARA_BIRIMI})"
        )

    @app_commands.command(name="avla", description="OwO tarzı avlanarak nadir hayvanlar yakala ve coin kazan")
    async def avla(self, inter: discord.Interaction):
        uid = str(inter.user.id)
        simdi = time.time()
        bekleme = self.veri["beklemeler"].setdefault(uid, {})
        kalan = AVLA_BEKLEME - (simdi - float(bekleme.get("avla", 0.0)))
        if kalan > 0:
            return await inter.response.send_message(
                f"🏹 Tekrar avlanmak için **{_sure_formatla(kalan)}** bekle.",
                ephemeral=True,
            )

        bekleme["avla"] = simdi
        agirliklar = [satir[0] for satir in AV_TABLOSU]
        _, nadirlik, emoji, isim, mn, mx = random.choices(AV_TABLOSU, weights=agirliklar, k=1)[0]
        kazanc = random.randint(mn, mx)
        yeni = self._bakiye_ekle(inter.user.id, kazanc)
        await inter.response.send_message(
            f"🏹 Avlandın ve **[{nadirlik}] {emoji} {isim}** yakaladın!\n"
            f"Satış kazancı: **+{kazanc:,}** {PARA_BIRIMI} | Bakiye: **{yeni:,}** {PARA_BIRIMI}"
        )

    @app_commands.command(name="bahis", description="Yazı-tura bahsi oyna (2x kazan ya da kaybet)")
    @app_commands.describe(miktar="Bahis miktarı", secim="Yazı mı Tura mı?")
    @app_commands.choices(secim=[
        app_commands.Choice(name="Yazı", value="yazi"),
        app_commands.Choice(name="Tura", value="tura"),
    ])
    async def bahis(
        self,
        inter: discord.Interaction,
        miktar: app_commands.Range[int, MIN_BAHIS, MAX_BAHIS],
        secim: app_commands.Choice[str] = None,
    ):
        bakiye = self._bakiye_al(inter.user.id)
        if bakiye < miktar:
            return await inter.response.send_message(
                f"❌ Yetersiz bakiye! Bakiyen: **{bakiye:,}** {PARA_BIRIMI}",
                ephemeral=True,
            )

        oyuncu_secim = secim.value if secim else "yazi"
        sonuc = random.choice(["yazi", "tura"])
        isim = {"yazi": "Yazı", "tura": "Tura"}

        if oyuncu_secim == sonuc:
            yeni = self._bakiye_ekle(inter.user.id, miktar)
            await inter.response.send_message(
                f"🪙 **{isim[sonuc]}** geldi! Kazandın: **+{miktar:,}** {PARA_BIRIMI} "
                f"(Bakiye: **{yeni:,}** {PARA_BIRIMI})"
            )
        else:
            yeni = self._bakiye_ekle(inter.user.id, -miktar)
            await inter.response.send_message(
                f"🪙 **{isim[sonuc]}** geldi... Kaybettin: **-{miktar:,}** {PARA_BIRIMI} "
                f"(Bakiye: **{yeni:,}** {PARA_BIRIMI})"
            )

    @app_commands.command(name="slot", description="Slot makinesi çevir ve şansını dene")
    @app_commands.describe(miktar="Bahis miktarı")
    async def slot(self, inter: discord.Interaction, miktar: app_commands.Range[int, MIN_BAHIS, MAX_BAHIS]):
        bakiye = self._bakiye_al(inter.user.id)
        if bakiye < miktar:
            return await inter.response.send_message(
                f"❌ Yetersiz bakiye! Bakiyen: **{bakiye:,}** {PARA_BIRIMI}",
                ephemeral=True,
            )

        a, b, c = random.choices(SLOT_SEMBOLLER, weights=SLOT_AGIRLIKLAR, k=3)
        ekran = f"🎰 | {a} | {b} | {c} |"

        if a == b == c:
            carpan = 10 if a == "7️⃣" else (6 if a == "💎" else 3)
            net = miktar * (carpan - 1)
            yeni = self._bakiye_ekle(inter.user.id, net)
            mesaj = f"🎉 **JACKPOT ({carpan}x)!** **+{net:,}** {PARA_BIRIMI} kazandın!"
        elif a == b or b == c or a == c:
            net = max(1, miktar // 2)
            yeni = self._bakiye_ekle(inter.user.id, net)
            mesaj = f"✨ İkili tuttu! **+{net:,}** {PARA_BIRIMI} kazandın!"
        else:
            yeni = self._bakiye_ekle(inter.user.id, -miktar)
            mesaj = f"💀 Tutmadı... **-{miktar:,}** {PARA_BIRIMI} kaybettin."

        await inter.response.send_message(f"{ekran}\n{mesaj} (Bakiye: **{yeni:,}** {PARA_BIRIMI})")

    @app_commands.command(name="ver", description="Başka bir üyeye coin gönder")
    @app_commands.describe(kisi="Coin gönderilecek kişi", miktar="Gönderilecek miktar")
    async def ver(self, inter: discord.Interaction, kisi: discord.Member, miktar: app_commands.Range[int, 1, 1000000]):
        if kisi.bot or kisi.id == inter.user.id:
            return await inter.response.send_message("Kendine veya botlara coin gönderemezsin.", ephemeral=True)
        bakiye = self._bakiye_al(inter.user.id)
        if bakiye < miktar:
            return await inter.response.send_message(
                f"❌ Yetersiz bakiye! Bakiyen: **{bakiye:,}** {PARA_BIRIMI}",
                ephemeral=True,
            )
        yeni_gonderen = self._bakiye_ekle(inter.user.id, -miktar)
        self._bakiye_ekle(kisi.id, miktar)
        await inter.response.send_message(
            f"💸 {inter.user.mention} → {kisi.mention}: **{miktar:,}** {PARA_BIRIMI} gönderildi!\n"
            f"Kalan bakiyen: **{yeni_gonderen:,}** {PARA_BIRIMI}"
        )

    @app_commands.command(name="liderlik", description="En zengin kullanıcıları listele")
    @app_commands.guild_only()
    async def liderlik(self, inter: discord.Interaction):
        sirali = sorted(self.veri["bakiyeler"].items(), key=lambda x: int(x[1]), reverse=True)
        satirlar = []
        sira = 0
        for uid_str, coin in sirali:
            if int(coin) <= 0:
                continue
            m = inter.guild.get_member(int(uid_str))
            if m is None:
                continue
            sira += 1
            madalya = {1: "🥇", 2: "🥈", 3: "🥉"}.get(sira, f"`#{sira}`")
            satirlar.append(f"{madalya} **{m.display_name}** — `{int(coin):,}` {PARA_BIRIMI}")
            if sira >= 10:
                break
        if not satirlar:
            return await inter.response.send_message("Henüz kimsenin coini yok. `/gunluk` veya `/avla` ile başla!")
        e = discord.Embed(title=f"🏆 {inter.guild.name} — Zenginler Listesi", description="\n".join(satirlar), color=discord.Color.gold())
        await inter.response.send_message(embed=e)

    # ============================================================
    #  🎯 COİN İLE HEDEFLEME
    # ============================================================
    @app_commands.command(name="hedefle", description="Coin ödeyerek birini geçici hedefe al (bot ona ters mod açar)")
    @app_commands.guild_only()
    @app_commands.describe(kisi="Hedeflenecek kişi", paket="Süre ve ücret paketi")
    @app_commands.choices(paket=[
        app_commands.Choice(name="15 Dakika — 300 Coin", value="15dk"),
        app_commands.Choice(name="1 Saat — 900 Coin", value="1saat"),
        app_commands.Choice(name="6 Saat — 3.500 Coin", value="6saat"),
        app_commands.Choice(name="24 Saat — 10.000 Coin", value="24saat"),
    ])
    async def hedefle(self, inter: discord.Interaction, kisi: discord.Member, paket: app_commands.Choice[str]):
        if kisi.bot:
            return await inter.response.send_message("Botları hedefleyemezsin.", ephemeral=True)
        if kisi.id == inter.user.id:
            return await inter.response.send_message("Kendini hedeflemeye gerek yok 😅", ephemeral=True)

        korunma_kalan = self._korunma_kalan(kisi.id)
        if korunma_kalan > 0:
            return await inter.response.send_message(
                f"🛡️ **{kisi.display_name}** şu an korunma kalkanı altında! "
                f"Kalan süre: **{_sure_formatla(korunma_kalan)}**",
                ephemeral=True,
            )

        sure_sn, ucret, etiket = HEDEF_PAKETLERI[paket.value]
        bakiye = self._bakiye_al(inter.user.id)
        if bakiye < ucret:
            return await inter.response.send_message(
                f"❌ Yetersiz bakiye! Bu paket için **{ucret:,}** {PARA_BIRIMI} lazım, sende **{bakiye:,}** {PARA_BIRIMI} var.",
                ephemeral=True,
            )

        yeni_bakiye = self._bakiye_ekle(inter.user.id, -ucret)
        simdi = time.time()
        gecici_hedefler = getattr(self.bot, "gecici_hedefler", {})
        mevcut_bitis = max(simdi, float(gecici_hedefler.get(kisi.id, 0)))
        yeni_bitis = mevcut_bitis + sure_sn

        gecici_hedefler[kisi.id] = yeni_bitis
        self.bot.gecici_hedefler = gecici_hedefler
        self.veri["gecici_hedefler"][str(kisi.id)] = yeni_bitis
        self._kaydet()

        await inter.response.send_message(
            f"🎯 **{inter.user.display_name}**, **{ucret:,}** {PARA_BIRIMI} ödeyerek {kisi.mention} kullanıcısını "
            f"**{etiket}** boyunca hedefe aldı!\n"
            f"Toplam kalan hedef süresi: **{_sure_formatla(yeni_bitis - simdi)}** | "
            f"Kalan bakiyen: **{yeni_bakiye:,}** {PARA_BIRIMI}"
        )

    @app_commands.command(name="aktif_hedefler", description="Coin ile hedeflenmiş aktif kişileri listele")
    @app_commands.guild_only()
    async def aktif_hedefler(self, inter: discord.Interaction):
        simdi = time.time()
        gecici_hedefler = getattr(self.bot, "gecici_hedefler", {})
        satirlar = []
        for uid, bitis in list(gecici_hedefler.items()):
            if bitis <= simdi:
                gecici_hedefler.pop(uid, None)
                self.veri["gecici_hedefler"].pop(str(uid), None)
                continue
            m = inter.guild.get_member(uid)
            ad = m.display_name if m else str(uid)
            satirlar.append(f"• **{ad}** — kalan süre: `{_sure_formatla(bitis - simdi)}`")
        if not satirlar:
            return await inter.response.send_message("🎯 Şu an coin ile hedeflenmiş kimse yok.")
        await inter.response.send_message("🎯 **Coin ile Aktif Hedefler:**\n" + "\n".join(satirlar))

    @app_commands.command(name="korunma", description="Coin ödeyerek hedeflenmeye karşı kalkan satın al")
    @app_commands.guild_only()
    @app_commands.describe(paket="Korunma süresi")
    @app_commands.choices(paket=[
        app_commands.Choice(name="1 Saat — 400 Coin", value="1saat"),
        app_commands.Choice(name="6 Saat — 1.500 Coin", value="6saat"),
        app_commands.Choice(name="24 Saat — 4.500 Coin", value="24saat"),
    ])
    async def korunma(self, inter: discord.Interaction, paket: app_commands.Choice[str]):
        sure_sn, ucret, etiket = KORUNMA_PAKETLERI[paket.value]
        bakiye = self._bakiye_al(inter.user.id)
        if bakiye < ucret:
            return await inter.response.send_message(
                f"❌ Yetersiz bakiye! **{ucret:,}** {PARA_BIRIMI} lazım, sende **{bakiye:,}** {PARA_BIRIMI} var.",
                ephemeral=True,
            )

        yeni_bakiye = self._bakiye_ekle(inter.user.id, -ucret)
        simdi = time.time()
        mevcut = max(simdi, float(self.veri["korunmalar"].get(str(inter.user.id), 0)))
        yeni_bitis = mevcut + sure_sn
        self.veri["korunmalar"][str(inter.user.id)] = yeni_bitis

        # Üzerindeki coin hedefi varsa temizle
        gecici_hedefler = getattr(self.bot, "gecici_hedefler", {})
        gecici_hedefler.pop(inter.user.id, None)
        self.veri["gecici_hedefler"].pop(str(inter.user.id), None)
        self._kaydet()

        await inter.response.send_message(
            f"🛡️ **{etiket}** korunma kalkanı aktif edildi! (Toplam kalan: **{_sure_formatla(yeni_bitis - simdi)}**)\n"
            f"Kalan bakiyen: **{yeni_bakiye:,}** {PARA_BIRIMI}"
        )

    # ============================================================
    #  📋 GÖREV SİSTEMİ (Kullanıcıdan Kullanıcıya Ödüllü Görevler)
    # ============================================================
    @app_commands.command(name="gorev_ver", description="Ödüllü görev oluştur (ödül bakiyenden kilitlenir)")
    @app_commands.guild_only()
    @app_commands.describe(
        odul="Görevi tamamlayana verilecek coin miktarı",
        baslik="Kısa görev başlığı",
        aciklama="Görev detayları (opsiyonel)",
    )
    async def gorev_ver(
        self,
        inter: discord.Interaction,
        odul: app_commands.Range[int, MIN_GOREV_ODUL, 1000000],
        baslik: str,
        aciklama: str = "",
    ):
        bakiye = self._bakiye_al(inter.user.id)
        if bakiye < odul:
            return await inter.response.send_message(
                f"❌ Yetersiz bakiye! Görev ödülü için **{odul:,}** {PARA_BIRIMI} lazım, sende **{bakiye:,}** {PARA_BIRIMI} var.",
                ephemeral=True,
            )

        self._bakiye_ekle(inter.user.id, -odul)
        self.veri["son_gorev_id"] = int(self.veri.get("son_gorev_id", 0)) + 1
        gid = self.veri["son_gorev_id"]
        self.veri["gorevler"][str(gid)] = {
            "id": gid,
            "sunucu_id": inter.guild_id,
            "veren_id": inter.user.id,
            "baslik": baslik.strip()[:100],
            "aciklama": aciklama.strip()[:400],
            "odul": odul,
            "durum": "acik",
            "alan_id": None,
            "kanit": None,
            "olusturma": time.time(),
        }
        self._kaydet()

        e = discord.Embed(title=f"📋 Yeni Görev #{gid}: {baslik[:100]}", color=discord.Color.blue())
        if aciklama.strip():
            e.description = aciklama.strip()[:400]
        e.add_field(name="Ödül", value=f"**{odul:,}** {PARA_BIRIMI} (kilitli)", inline=True)
        e.add_field(name="Veren", value=inter.user.mention, inline=True)
        e.set_footer(text=f"Üstlenmek için: /gorev_al gorev_id:{gid}")
        await inter.response.send_message(embed=e)

    @app_commands.command(name="gorevler", description="Sunucudaki açık ve devam eden görevleri listele")
    @app_commands.guild_only()
    async def gorevler(self, inter: discord.Interaction):
        aktifler = [
            g for g in self.veri["gorevler"].values()
            if g.get("sunucu_id") == inter.guild_id and g.get("durum") in ("acik", "alindi", "teslim")
        ]
        if not aktifler:
            return await inter.response.send_message("📋 Şu an açık görev yok. `/gorev_ver` ile ilk görevi sen aç!")

        durum_etiket = {
            "acik": "🟢 Açık",
            "alindi": "🟡 Üstlenildi",
            "teslim": "🟣 Onay Bekliyor",
        }
        satirlar = []
        for g in sorted(aktifler, key=lambda x: x["id"], reverse=True)[:15]:
            veren = inter.guild.get_member(g["veren_id"])
            veren_ad = veren.display_name if veren else str(g["veren_id"])
            detay = f" — {g['aciklama'][:60]}" if g.get("aciklama") else ""
            satirlar.append(
                f"**#{g['id']}** [{durum_etiket[g['durum']]}] **{g['baslik']}**{detay}\n"
                f"↳ Ödül: `{g['odul']:,}` {PARA_BIRIMI} | Veren: `{veren_ad}`"
            )
        e = discord.Embed(title="📋 Görev Panosu", description="\n\n".join(satirlar), color=discord.Color.blue())
        await inter.response.send_message(embed=e)

    @app_commands.command(name="gorevlerim", description="Verdiğin ve üstlendiğin aktif görevleri gör")
    @app_commands.guild_only()
    async def gorevlerim(self, inter: discord.Interaction):
        uid = inter.user.id
        verdiklerim = []
        aldiklarim = []
        for g in self.veri["gorevler"].values():
            if g.get("sunucu_id") != inter.guild_id or g.get("durum") not in ("acik", "alindi", "teslim"):
                continue
            satir = f"**#{g['id']}** `{g['durum']}` — **{g['baslik']}** (`{g['odul']:,}` {PARA_BIRIMI})"
            if g["veren_id"] == uid:
                verdiklerim.append(satir)
            if g["alan_id"] == uid:
                aldiklarim.append(satir)

        e = discord.Embed(title=f"📋 {inter.user.display_name} — Görevlerim", color=discord.Color.blurple())
        e.add_field(name="📤 Verdiğim Görevler", value="\n".join(verdiklerim) or "Yok", inline=False)
        e.add_field(name="📥 Üstlendiğim Görevler", value="\n".join(aldiklarim) or "Yok", inline=False)
        await inter.response.send_message(embed=e)

    @app_commands.command(name="gorev_al", description="Açık bir görevi üstlen")
    @app_commands.guild_only()
    @app_commands.describe(gorev_id="Üstleneceğin görevin numarası")
    async def gorev_al(self, inter: discord.Interaction, gorev_id: int):
        g = self.veri["gorevler"].get(str(gorev_id))
        if not g or g.get("sunucu_id") != inter.guild_id:
            return await inter.response.send_message("❌ Böyle bir görev bulunamadı.", ephemeral=True)
        if g["durum"] != "acik":
            return await inter.response.send_message("❌ Bu görev şu an alınamaz (başkası almış veya kapanmış).", ephemeral=True)
        if g["veren_id"] == inter.user.id:
            return await inter.response.send_message("❌ Kendi verdiğin görevi üstlenemezsin.", ephemeral=True)

        aktif_sayisi = sum(
            1 for x in self.veri["gorevler"].values()
            if x.get("alan_id") == inter.user.id and x.get("durum") in ("alindi", "teslim")
        )
        if aktif_sayisi >= MAX_AKTIF_GOREV:
            return await inter.response.send_message(
                f"❌ Aynı anda en fazla **{MAX_AKTIF_GOREV}** aktif görev üstlenebilirsin.",
                ephemeral=True,
            )

        g["durum"] = "alindi"
        g["alan_id"] = inter.user.id
        self._kaydet()
        await inter.response.send_message(
            f"✅ {inter.user.mention}, **#{gorev_id} ({g['baslik']})** görevini üstlendi!\n"
            f"Bitirince `/gorev_teslim gorev_id:{gorev_id} kanit:...` ile teslim et."
        )

    @app_commands.command(name="gorev_teslim", description="Üstlendiğin görevi kanıt/not ile teslim et")
    @app_commands.guild_only()
    @app_commands.describe(gorev_id="Görev numarası", kanit="Görevi yaptığına dair kısa not veya link")
    async def gorev_teslim(self, inter: discord.Interaction, gorev_id: int, kanit: str):
        g = self.veri["gorevler"].get(str(gorev_id))
        if not g or g.get("sunucu_id") != inter.guild_id:
            return await inter.response.send_message("❌ Böyle bir görev bulunamadı.", ephemeral=True)
        if g["alan_id"] != inter.user.id:
            return await inter.response.send_message("❌ Bu görevi sen üstlenmemişsin.", ephemeral=True)
        if g["durum"] != "alindi":
            return await inter.response.send_message("❌ Bu görev şu an teslim edilebilir durumda değil.", ephemeral=True)

        g["durum"] = "teslim"
        g["kanit"] = kanit.strip()[:400]
        self._kaydet()
        await inter.response.send_message(
            f"📨 <@{g['veren_id']}> — {inter.user.mention} **#{gorev_id} ({g['baslik']})** görevini teslim etti!\n"
            f"**Kanıt:** {g['kanit']}\n"
            f"Onaylamak için `/gorev_onayla gorev_id:{gorev_id}`, reddetmek için `/gorev_reddet gorev_id:{gorev_id}` kullan."
        )

    @app_commands.command(name="gorev_onayla", description="Teslim edilen görevini onayla ve coin ödülünü aktar")
    @app_commands.guild_only()
    @app_commands.describe(gorev_id="Onaylanacak görev numarası")
    async def gorev_onayla(self, inter: discord.Interaction, gorev_id: int):
        g = self.veri["gorevler"].get(str(gorev_id))
        if not g or g.get("sunucu_id") != inter.guild_id:
            return await inter.response.send_message("❌ Böyle bir görev bulunamadı.", ephemeral=True)
        if g["veren_id"] != inter.user.id:
            return await inter.response.send_message("❌ Sadece görevi veren kişi onaylayabilir.", ephemeral=True)
        if g["durum"] not in ("alindi", "teslim") or not g["alan_id"]:
            return await inter.response.send_message("❌ Bu görev onaylanabilir durumda değil.", ephemeral=True)

        g["durum"] = "tamamlandi"
        odul = int(g["odul"])
        alan_id = int(g["alan_id"])
        yeni_bakiye = self._bakiye_ekle(alan_id, odul)
        self._kaydet()

        await inter.response.send_message(
            f"🎉 **Görev #{gorev_id} onaylandı!**\n"
            f"<@{alan_id}> hesabına **+{odul:,}** {PARA_BIRIMI} aktarıldı! (Yeni bakiye: **{yeni_bakiye:,}** {PARA_BIRIMI})"
        )

    @app_commands.command(name="gorev_reddet", description="Teslim edilen görevi reddet (yapan kişi tekrar deneyebilir)")
    @app_commands.guild_only()
    @app_commands.describe(gorev_id="Görev numarası", sebep="Reddetme sebebi (opsiyonel)")
    async def gorev_reddet(self, inter: discord.Interaction, gorev_id: int, sebep: str = ""):
        g = self.veri["gorevler"].get(str(gorev_id))
        if not g or g.get("sunucu_id") != inter.guild_id:
            return await inter.response.send_message("❌ Böyle bir görev bulunamadı.", ephemeral=True)
        if g["veren_id"] != inter.user.id:
            return await inter.response.send_message("❌ Sadece görevi veren kişi reddedebilir.", ephemeral=True)
        if g["durum"] != "teslim":
            return await inter.response.send_message("❌ Bu görev şu an onay beklemiyor.", ephemeral=True)

        g["durum"] = "alindi"
        g["kanit"] = None
        self._kaydet()
        ek = f"\n**Sebep:** {sebep[:200]}" if sebep.strip() else ""
        await inter.response.send_message(
            f"❌ <@{g['alan_id']}>, **#{gorev_id} ({g['baslik']})** teslimin reddedildi.{ek}\n"
            f"Düzeltip tekrar `/gorev_teslim` yapabilir veya `/gorev_birak` ile görevi bırakabilirsin."
        )

    @app_commands.command(name="gorev_birak", description="Üstlendiğin görevi bırak (başkası alabilsin)")
    @app_commands.guild_only()
    @app_commands.describe(gorev_id="Bırakacağın görev numarası")
    async def gorev_birak(self, inter: discord.Interaction, gorev_id: int):
        g = self.veri["gorevler"].get(str(gorev_id))
        if not g or g.get("sunucu_id") != inter.guild_id:
            return await inter.response.send_message("❌ Böyle bir görev bulunamadı.", ephemeral=True)
        if g["alan_id"] != inter.user.id or g["durum"] not in ("alindi", "teslim"):
            return await inter.response.send_message("❌ Bu görev sende değil.", ephemeral=True)

        g["durum"] = "acik"
        g["alan_id"] = None
        g["kanit"] = None
        self._kaydet()
        await inter.response.send_message(f"🔄 **#{gorev_id} ({g['baslik']})** görevini bıraktın, tekrar herkese açıldı.")

    @app_commands.command(name="gorev_iptal", description="Henüz kimsenin almadığı görevini iptal et ve coinini geri al")
    @app_commands.guild_only()
    @app_commands.describe(gorev_id="İptal edilecek görev numarası")
    async def gorev_iptal(self, inter: discord.Interaction, gorev_id: int):
        g = self.veri["gorevler"].get(str(gorev_id))
        if not g or g.get("sunucu_id") != inter.guild_id:
            return await inter.response.send_message("❌ Böyle bir görev bulunamadı.", ephemeral=True)
        yetkili = inter.user.guild_permissions.manage_guild
        if g["veren_id"] != inter.user.id and not yetkili:
            return await inter.response.send_message("❌ Sadece görevi veren kişi veya sunucu yetkilisi iptal edebilir.", ephemeral=True)
        if g["durum"] not in ("acik", "alindi", "teslim"):
            return await inter.response.send_message("❌ Bu görev zaten kapanmış.", ephemeral=True)
        if g["durum"] != "acik" and not yetkili:
            return await inter.response.send_message(
                "❌ Biri bu görevi üstlenmiş durumda! Sadece sunucu yetkilisi zorla iptal edebilir.",
                ephemeral=True,
            )

        g["durum"] = "iptal"
        odul = int(g["odul"])
        yeni_bakiye = self._bakiye_ekle(g["veren_id"], odul)
        self._kaydet()
        await inter.response.send_message(
            f"🗑️ **#{gorev_id} ({g['baslik']})** iptal edildi. "
            f"**{odul:,}** {PARA_BIRIMI} <@{g['veren_id']}> hesabına iade edildi! (Bakiye: **{yeni_bakiye:,}** {PARA_BIRIMI})"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Ekonomi(bot))
