import os
import logging
import asyncio
import random
import string
import discord
from discord.ext import commands, tasks
from discord import ui
import feedparser

# ==============================================================================
# KONFIGURACJA LOGOWANIA
# ==============================================================================
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s | %(levelname)s | %(name)s -> %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("HakerolandiaShop")

# Globalny magazyn aktywnych kodów zniżkowych
AKTYWNE_KODY = {
    "40-osob": 5
}

# ==============================================================================
# KONFIGURACJA POWIADOMIEŃ YOUTUBE (W STYLU KOYA)
# ==============================================================================
# ID Twojego kanału na Discordzie (nowe filmy)
KANAL_FILMY_ID = 1532729202007216140

# Twój adres RSS z Twoim ID YouTube
YOUTUBE_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCCelA7w6rz4fDhrPG2DbY1A"

# Zmienna pomocnicza do śledzenia ostatnio wysłanego filmu
ostatnio_wyslany_id = None


# ==============================================================================
# 1. WERYFIKACJA (CAPTCHA)
# ==============================================================================
class CaptchaView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="ZWERYFIKUJ SIĘ (CAPTCHA)", style=discord.ButtonStyle.green, custom_id="btn_captcha_hakerolandia", emoji="🛡️")
    async def verify(self, interaction: discord.Interaction, button: ui.Button):
        # Szukamy dokładnie roli o nazwie "✅ • Zweryfikowany"
        role = discord.utils.get(interaction.guild.roles, name="✅ • Zweryfikowany")
        if not role:
            await interaction.response.send_message("❌ Błąd: Brak roli '✅ • Zweryfikowany' na serwerze. Utwórz ją w ustawieniach.", ephemeral=True)
            return
        
        try:
            if role in interaction.user.roles:
                await interaction.response.send_message("ℹ️ Twoje konto jest już zweryfikowane!", ephemeral=True)
                return

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ Pomyślnie zweryfikowano konto! Witaj na serwerze Hakerolandia.", ephemeral=True)
        except Exception as e:
            logger.error(f"Błąd nadawania roli: {e}")
            await interaction.response.send_message("❌ Brak uprawnień do nadania roli (upewnij się, że bot ma wyższą rangę niż rola '✅ • Zweryfikowany' w zakładce Role).", ephemeral=True)


# ==============================================================================
# 2. FORMULARZ OPINII (MODAL)
# ==============================================================================
class OpiniaModal(ui.Modal, title="HAKEROLANDIA — WYSTAW OPINIĘ"):
    def __init__(self):
        super().__init__()

    ocena = ui.TextInput(
        label="OCENA (np. ⭐⭐⭐⭐⭐ / 5/5):",
        placeholder="Wpisz ocenę gwiazdkową lub cyfrową",
        required=True,
        max_length=20
    )
    
    tresc = ui.TextInput(
        label="TREŚĆ OPINII:",
        placeholder="Napisz, co sądzisz o realizacji zamówienia...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⭐ NOWA OPINIA O HAKEROLANDIA",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Autor", value=interaction.user.mention, inline=True)
        embed.add_field(name="Ocena", value=self.ocena.value, inline=True)
        embed.add_field(name="Treść", value=self.tresc.value, inline=False)
        embed.set_footer(text="Dziękujemy za opinię! ❤️")

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Twoja opinia została pomyślnie opublikowana! Dziękujemy!", ephemeral=True)


# ==============================================================================
# 3. WIDOK PANELU OPINII (PRZYCISK)
# ==============================================================================
class OpiniePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Wystaw Opinię", style=discord.ButtonStyle.green, custom_id="btn_hakerolandia_wystaw_opinie", emoji="⭐")
    async def wystaw_opinie_btn(self, interaction: discord.Interaction, button: ui.Button):
        rola_klient = discord.utils.get(interaction.guild.roles, name="⭐ • Klient")
        
        if not rola_klient or rola_klient not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ **Brak uprawnień!** Nie posiadasz wymaganej rangi **⭐ • Klient**, aby móc wystawić opinię.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(OpiniaModal())


# ==============================================================================
# 4. FORMULARZ ZAMÓWIENIA (MODAL) Z UWAGAMI I KODEM RABATOWYM
# ==============================================================================
class ZamowienieModal(ui.Modal, title="HAKEROLANDIA — FORMULARZ ZAMÓWIENIA"):
    def __init__(self, produkt: str, cena_jednostkowa: float, ilosc: int):
        super().__init__()
        self.produkt = produkt
        self.cena_jednostkowa = cena_jednostkowa
        self.ilosc = ilosc

    discord_nick = ui.TextInput(
        label="JAKI JEST TWÓJ DISCORD NICK:",
        placeholder="np. HakerPro",
        required=True,
        max_length=100
    )
    
    platnosc = ui.TextInput(
        label="JAKĄ METODĄ PŁATNOŚCI CHCESZ ZAPŁACIć:",
        placeholder="BLIK / Revolut",
        required=True,
        max_length=50
    )
    
    uwagi = ui.TextInput(
        label="UWAGI DO ZAMÓWIENIA:",
        placeholder="Dodatkowe wytyczne, szczegóły lub opcjonalne info",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )
    
    kod_rabatowy = ui.TextInput(
        label="CZY POSIADASZ KOD ZNIŻKOWY:",
        placeholder="Wpisz np. 40-osob lub inny kod rabatowy.",
        required=False,
        max_length=50
    )
    
    kod_polecajacy = ui.TextInput(
        label="CZY POSIADASZ KOD POLECAJĄCY:",
        placeholder="Jeżeli nie posiadasz, zostaw to pole puste.",
        required=False,
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        cena_bazowa = self.cena_jednostkowa * self.ilosc
        rabat_tekst = self.kod_rabatowy.value.strip() if self.kod_rabatowy.value else ""
        znizka_procent = 0

        if rabat_tekst:
            dopasowany_kod = None
            for k in AKTYWNE_KODY:
                if k.lower() == rabat_tekst.lower():
                    dopasowany_kod = k
                    break

            if dopasowany_kod:
                znizka_procent = AKTYWNE_KODY[dopasowany_kod]
                rabat_tekst = dopasowany_kod
            else:
                await interaction.response.send_message(
                    f"⚠️ Podany kod rabatowy **`{rabat_tekst}`** jest nieprawidłowy lub wygasł. Zamówienie zostanie zrealizowane bez zniżki.",
                    ephemeral=True
                )

        rabat_kwotowo = (cena_bazowa * znizka_procent) / 100
        cena_calkowita = cena_bazowa - rabat_kwotowo

        polecajacy = self.kod_polecajacy.value if self.kod_polecajacy.value else "Nie podano."
        tekst_uwag = self.uwagi.value if self.uwagi.value else "Brak uwag."
        informacja_o_rabacie = f"-{znizka_procent}% ({rabat_tekst})" if znizka_procent > 0 else "Brak"

        view = PodsumowanieZakupuView(
            produkt=self.produkt,
            ilosc=self.ilosc,
            cena=cena_calkowita,
            nick=self.discord_nick.value,
            platnosc=self.platnosc.value,
            uwagi=tekst_uwag,
            rabat=informacja_o_rabacie,
            polecajacy=polecajacy
        )

        tekst = (
            f"🛒 **HAKEROLANDIA — PODSUMOWANIE ZAMÓWIENIA**\n"
            f"Poniżej dostępne jest kompletne podsumowanie zamówienia wg. podanych przez Ciebie informacji.\n\n"
            f"• **{self.ilosc}x {self.produkt}** — **{cena_bazowa:.2f} PLN** [{self.cena_jednostkowa:.2f} PLN/szt.]\n"
            f"• Zniżka: **{informacja_o_rabacie}**\n\n"
            f"Uwagi: {tekst_uwag}\n"
            f"Cena końcowa do zapłaty: **{cena_calkowita:.2f} PLN**\n\n"
            f"**Wszystko się zgadza?** — Użyj przycisku poniżej i dokonaj płatności."
        )
        await interaction.response.send_message(tekst, view=view, ephemeral=True)


# ==============================================================================
# 5. WIDOK PODSUMOWANIA (Z PRZYCISKIEM DO PŁATNOŚCI)
# ==============================================================================
class PodsumowanieZakupuView(ui.View):
    def __init__(self, produkt, ilosc, cena, nick, platnosc, uwagi, rabat, polecajacy):
        super().__init__(timeout=300)
        self.produkt = produkt
        self.ilosc = ilosc
        self.cena = cena
        self.nick = nick
        self.platnosc = platnosc
        self.uwagi = uwagi
        self.rabat = rabat
        self.polecajacy = polecajacy

        self.add_item(ui.Button(
            label="Dokonaj płatności przez Tipply", 
            style=discord.ButtonStyle.link, 
            url="https://tipply.pl/@hakerroblox", 
            emoji="💲"
        ))

    @ui.button(label="✅ Opłaciłem - Utwórz ticket", style=discord.ButtonStyle.green, custom_id="btn_hakerolandia_ticket", row=1)
    async def finalizuj_zamowienie(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        user = interaction.user
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        channel_name = f"zamówienie-{user.name}"
        try:
            ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        except Exception as e:
            logger.error(f"Nie udało się utworzyć ticketa: {e}")
            await interaction.response.send_message("❌ Brak uprawnień do utworzenia kanału ticketa.", ephemeral=True)
            return

        embed = discord.Embed(title="HAKEROLANDIA — PŁATNOŚĆ I REALIZACJA", color=discord.Color.green())
        embed.add_field(name="Wybrany Pakiet", value=f"{self.ilosc}x {self.produkt} ({self.cena:.2f} PLN)", inline=False)
        embed.add_field(name="Twój Nick", value=self.nick, inline=False)
        embed.add_field(name="Metoda Płatności", value=self.platnosc, inline=False)
        embed.add_field(name="Uwagi do zamówienia", value=self.uwagi, inline=False)
        embed.add_field(name="Kod zniżkowy", value=self.rabat, inline=False)
        embed.add_field(name="Kod polecający", value=self.polecajacy, inline=False)

        await ticket_channel.send(
            content=f"🔔 **Witaj {user.mention}!**\n"
                    f"Zamówienia realizujemy **po kolei** — zgodnie z kolejnością wpłat. ❤️\n"
                    f"Pozostało Ci tylko **dokonać płatności**, jeżeli przebiegnie ona pomyślnie, zostanie utworzony kanał, na którym administrator przekaże Ci produkt.\n"
                    f"⏱️ Realizacja do 48h. *(Gdy skończycie, administrator może użyć `/zakoncz`)*",
            embed=embed
        )

        await interaction.response.edit_message(
            content=f"✅ Utworzono dla Ciebie prywatny ticket: {ticket_channel.mention}. Możesz odrzucić tę wiadomość.",
            view=None
        )


# ==============================================================================
# 6. WYBÓR ILOŚCI SZTUK
# ==============================================================================
class WyborIlosciSelectView(ui.View):
    def __init__(self, produkt, cena):
        super().__init__(timeout=None)
        self.produkt = produkt
        self.cena = cena

    @ui.select(
        placeholder="Wybierz ilość sztuk...",
        custom_id="select_hakerolandia_ilosc",
        options=[
            discord.SelectOption(label="1 szt.", value="1"),
            discord.SelectOption(label="2 szt.", value="2"),
        ]
    )
    async def select_ilosc(self, interaction: discord.Interaction, select: ui.Select):
        ilosc = int(select.values[0])
        await interaction.response.send_modal(
            ZamowienieModal(produkt=self.produkt, cena_jednostkowa=self.cena, ilosc=ilosc)
        )


# ==============================================================================
# 7. WYBÓR PAKIETU (PRODUKTU) Z MENU ROZWIJANEGO
# ==============================================================================
class WyborProduktuSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(
        placeholder="Wybierz pakiet z listy...",
        custom_id="select_hakerolandia_produkt",
        options=[
            discord.SelectOption(label="🟢 START", description="Cena: 19,99 PLN - Max 10 kategorii / 30 kanałów", value="START|19.99"),
            discord.SelectOption(label="🔵 BASIC", description="Cena: 35,99 PLN - Max 20 kategorii / 50 kanałów", value="BASIC|35.99"),
            discord.SelectOption(label="🟣 PREMIUM", description="Cena: 69,99 PLN - Nielimitowane kategorie i kanały", value="PREMIUM|69.99"),
            discord.SelectOption(label="🤖 BOTY DISCORD", description="Cena: 35,99 PLN - Boty discord na zamówienie", value="BOTY DISCORD|35.99"),
        ]
    )
    async def select_produkt(self, interaction: discord.Interaction, select: ui.Select):
        dane = select.values[0].split("|")
        produkt = dane[0]
        cena = float(dane[1])

        await interaction.response.send_message(
            f"🛒 **HAKEROLANDIA — WYBIERZ ILOŚĆ**\n"
            f"Wybrałeś pakiet: **{produkt}** ({cena} PLN/szt.). Wybierz ilość sztuk z menu poniżej.",
            view=WyborIlosciSelectView(produkt=produkt, cena=cena),
            ephemeral=True
        )


# ==============================================================================
# 8. PANEL GŁÓWNY (PRZYCISK "ZŁÓŻ ZAMÓWIENIE")
# ==============================================================================
class PanelGlownyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="ZŁÓŻ ZAMÓWIENIE", style=discord.ButtonStyle.green, custom_id="btn_hakerolandia_zlozo_zamowienie", emoji="🛒")
    async def zlozo_zamowienie_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            f"🛒 **HAKEROLANDIA — WYBIERZ PAKIET**\n"
            f"Wybierz interesujący Cię pakiet z menu poniżej:",
            view=WyborProduktuSelectView(),
            ephemeral=True
        )


# ==============================================================================
# 9. WIDOK PRZYCISKÓW (YOUTUBE ORAZ STRONA WWW)
# ==============================================================================
class YouTubeButtonView(ui.View):
    def __init__(self, link: str):
        super().__init__(timeout=None)
        self.add_item(ui.Button(
            label="OGLĄDAJ FILM", 
            style=discord.ButtonStyle.link, 
            url=link, 
            emoji="🎬"
        ))

class StronaButtonView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ui.Button(
            label="Odwiedź hakerroblox.gamer.gd", 
            style=discord.ButtonStyle.link, 
            url="https://hakerroblox.gamer.gd", 
            emoji="🌐"
        ))


# ==============================================================================
# 10. GŁÓWNA KLASA BOTA
# ==============================================================================
class HakerolandiaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        logger.info("Ładowanie stałych widoków Hakerolandia...")
        self.add_view(PanelGlownyView())
        self.add_view(CaptchaView())
        self.add_view(OpiniePanelView())
        self.add_view(StronaButtonView())
        
        # Uruchomienie automatycznego sprawdzania YouTube w tle
        self.sprawdz_youtube.start()
        
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            try:
                MY_GUILD = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=MY_GUILD)
                await self.tree.sync(guild=MY_GUILD)
                logger.info(f"Zsynchronizowano komendy dla serwera: {guild_id}")
            except Exception as e:
                logger.error(f"Błąd synchronizacji: {e}")

    async def on_ready(self):
        logger.info(f"Zalogowano pomyślnie jako {self.user}")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="HAKEROLANDIA | SKLEP SERWEROWY"))

    # Pętla działająca w tle (sprawdza YouTube co 10 minut)
    @tasks.loop(minutes=1)
    async def sprawdz_youtube(self):
        global ostatnio_wyslany_id
        try:
            feed = feedparser.parse(YOUTUBE_RSS_URL)
            if not feed.entries:
                return

            ostatni_film = feed.entries[0]
            film_id = ostatni_film.id
            tytul = ostatni_film.title
            link = ostatni_film.link

            if ostatnio_wyslany_id is None:
                ostatnio_wyslany_id = film_id
                return

            if film_id != ostatnio_wyslany_id:
                ostatnio_wyslany_id = film_id
                
                kanal = self.get_channel(KANAL_FILMY_ID)
                if kanal:
                    embed = discord.Embed(
                        title="🎬 NOWY FILM NA YOUTUBE!",
                        color=0xef4444
                    )
                    embed.add_field(name="📌 Typ Publikacji:", value="🎥 Film YouTube", inline=False)
                    embed.add_field(name="🎬 Tytuł:", value=tytul, inline=False)
                    embed.add_field(name="🔗 Link:", value=link, inline=False)
                    embed.set_footer(text="Hakerolandia • Automatyczne powiadomienie")
                    
                    view = YouTubeButtonView(link)
                    await kanal.send("@everyone", embed=embed, view=view)
                    logger.info(f"Wysłano automatyczne powiadomienie o filmie: {tytul}")
        except Exception as e:
            logger.error(f"Błąd podczas sprawdzania kanału YouTube: {e}")

    @sprawdz_youtube.before_loop
    async def before_sprawdz_youtube(self):
        await self.wait_until_ready()


bot = HakerolandiaBot()


# ==============================================================================
# 11. KOMENDY SLASH
# ==============================================================================
@bot.tree.command(name="wyslij-panel", description="Wysyła główny panel składania zamówień Hakerolandia")
async def wyslij_panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return
        
    embed = discord.Embed(
        title="HAKEROLANDIA — ZŁÓŻ ZAMÓWIENIE",
        description="W tym miejscu możesz dokonać zamówienia, jesteśmy **największym, najbardziej zaufanym sklepem**.\n\n"
                    "Wybierz interesujący Cię pakiet, klikając przycisk poniżej.",
        color=discord.Color.dark_purple()
    )
    
    embed.add_field(name="🟢 START — 19,99 zł", value="• Oferta na kanale cennik.", inline=False)
    embed.add_field(name="🔵 BASIC — 35,99 zł", value="• Oferta na kanale cennik.", inline=False)
    embed.add_field(name="🟣 PREMIUM — 69,99 zł", value="• Oferta na kanale cennik.", inline=False)
    embed.add_field(name="🤖 BOTY DISCORD — 35,99 zł", value="• Powitania i pożegnania", inline=False)

    await interaction.channel.send(embed=embed, view=PanelGlownyView())
    await interaction.response.send_message("✅ Pomyślnie wysłano panel sklepu Hakerolandia!", ephemeral=True)


@bot.tree.command(name="wyslij-strone", description="Wysyła informację o oficjalnej stronie internetowej Hakerolandia (Tylko Admin)")
async def wyslij_strone(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return

    embed = discord.Embed(
        title="Hakerolandia.pl × OFICJALNA STRONA WWW",
        description=(
            "➡️ **Zapraszamy do odwiedzenia naszej oficjalnej strony internetowej!**\n\n"
            "➡️ Znajdziesz tam pełną ofertę naszych usług, szczegółowy cennik oraz aktualności.\n\n"
            "➡️ **Oficjalny adres:** https://hakerroblox.gamer.gd"
        ),
        color=discord.Color.blue()
    )

    await interaction.channel.send(embed=embed, view=StronaButtonView())
    await interaction.response.send_message("✅ Pomyślnie wysłano panel strony internetowej!", ephemeral=True)


@bot.tree.command(name="kod-losuj", description="Losuje zniżkę od 5% do 20% i rejestruje aktywny kod (Tylko Admin)")
async def kod_losuj(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return

    znizka = random.randint(5, 20)
    kod = "PROMO-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    AKTYWNE_KODY[kod] = znizka

    embed = discord.Embed(
        title="🎲 WYLOSOWANO NOWY KOD ZNIŻKOWY",
        description=f"Pomyślnie wygenerowano i dodano kod do systemu bota!",
        color=discord.Color.green()
    )
    embed.add_field(name="Kod rabatowy", value=f"`{kod}`", inline=True)
    embed.add_field(name="Wysokość zniżki", value=f"**-{znizka}%**", inline=True)
    embed.set_footer(text="Klient może teraz wpisać ten kod podczas składania zamówienia.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="cennik", description="Wyświetla oficjalny cennik Hakerolandia")
async def cennik(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📃 CENNIK HAKEROLANDIA",
        description=(
            "⚠️ **UWAGA!**\n"
            "Zamówienia realizujemy **PO KOLEI** — zgodnie z kolejnością wpłat. ❤️\n\n"
            "🟢 **START — 19,99 zł**\n"
            "• Max 10 kategorii / 30 kanałów\n• Podstawowe rangi\n• Lobby\n• Zabezpieczenia\n\n"
            "🔵 **BASIC — 35,99 zł**\n"
            "• Max 20 kategorii / 50 kanałów\n• Rangi + Ekonomia + sklep\n• Selfrole & Invite Logger\n\n"
            "🟣 **PREMIUM — 69,99 zł**\n"
            "• Nielimitowane kategorie i kanały\n• Zaawansowane zabezpieczenia\n• Pomoc w rozwoju serwera\n\n"
            "🤖 **BOTY DISCORD — 35,99 zł**\n"
            "• Powitania i pożegnania\n\n"
            "💳 **PŁATNOŚĆ:** BLIK • Revolut | ⏱️ Realizacja do 48h"
        ),
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="wyslij-opinie", description="Wysyła panel wystawiania opinii (Tylko Admin)")
async def wyslij_opinie(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return

    embed = discord.Embed(
        title="HAKEROLANDIA × WYSTAW NAM OPINIĘ",
        description="» Wystawiając opinię pokazujesz innym, jak przebiegła Twoja obsługa.\n» Kliknij przycisk poniżej.",
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=embed, view=OpiniePanelView())
    await interaction.response.send_message("✅ Pomyślnie wysłano panel opinii!", ephemeral=True)


@bot.tree.command(name="opinie", description="Otwiera panel wystawiania opinii")
async def opinie(interaction: discord.Interaction):
    rola_klient = discord.utils.get(interaction.guild.roles, name="⭐• Klient")
    if not rola_klient or rola_klient not in interaction.user.roles:
        await interaction.response.send_message("❌ Brak wymaganej rangi **⭐• Klient**.", ephemeral=True)
        return
    await interaction.response.send_modal(OpiniaModal())


@bot.tree.command(name="wyslij-weryfikacje", description="Wysyła panel weryfikacji CAPTCHA (Tylko Admin)")
async def wyslij_weryfikacje(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return
        
    embed = discord.Embed(
        title="🛡️ Panel Weryfikacyjny Serwera",
        description=(
            "Witaj! Aby zapobiec automatycznym botom i kontom rajdowym, wymagane jest przejście weryfikacji.\n\n"
            "**Wymagania systemowe:**\n"
            "• Wiek konta: minimum 7 dni\n"
            "• Ustawione zdjęcie profilowe (Avatar)\n"
            "• Prawidłowa weryfikacja przyciskiem\n\n"
            "Kliknij poniższy przycisk, aby zweryfikować konto i uzyskać dostęp do serwera."
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="System Bezpieczeństwa Hakerolandia")

    await interaction.channel.send(embed=embed, view=CaptchaView())
    await interaction.response.send_message("✅ Pomyślnie wysłano panel weryfikacji!", ephemeral=True)


@bot.tree.command(name="zakoncz", description="Zamyka i usuwa bieżący ticket zamówienia (Tylko Admin)")
async def zakoncz(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return
        
    embed = discord.Embed(title="HAKEROLANDIA — ZAMÓWIENIE ZREALIZOWANE", description="Dziękujemy za zakupy! Kanał zostanie usunięty za 5 sekund.", color=discord.Color.green())
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Zamykanie ticketa...", ephemeral=True)
    await asyncio.sleep(5)
    try:
        await interaction.channel.delete()
    except Exception as e:
        logger.error(f"Błąd usuwania kanału: {e}")


# ==============================================================================
# 12. START APLIKACJI
# ==============================================================================
def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("KRYTYCZNY BŁĄD: Brak zmiennej DISCORD_TOKEN w konfiguracji!")
        return
    bot.run(token)

if __name__ == "__main__":
    main()
