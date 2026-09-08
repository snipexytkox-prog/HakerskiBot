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

# Globalny magazyn aktywnych kodów zniżkowych oraz statystyk zamówień
AKTYWNE_KODY = {
    "40-osob": 5
}
STATYSTYKI_SKLEPU = {
    "zamowienia_zrealizowane": 0
}

# ==============================================================================
# KONFIGURACJA POWIADOMIEŃ YOUTUBE
# ==============================================================================
KANAL_FILMY_ID = 1532729202007216140
YOUTUBE_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCCelA7w6rz4fDhrPG2DbY1A"
ostatnio_wyslany_id = None


# ==============================================================================
# 1. WERYFIKACJA (CAPTCHA)
# ==============================================================================
class CaptchaView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="ZWERYFIKUJ SIĘ (CAPTCHA)", style=discord.ButtonStyle.green, custom_id="btn_captcha_hakerolandia", emoji="🛡️")
    async def verify(self, interaction: discord.Interaction, button: ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="✅ • Zweryfikowany")
        if not role:
            await interaction.response.send_message("❌ Błąd: Brak roli '✅ • Zweryfikowany' na serwerze.", ephemeral=True)
            return
        
        try:
            if role in interaction.user.roles:
                await interaction.response.send_message("ℹ️ Twoje konto jest już zweryfikowane!", ephemeral=True)
                return

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ Pomyślnie zweryfikowano konto! Witaj na serwerze Hakerolandia.", ephemeral=True)
        except Exception as e:
            logger.error(f"Błąd nadawania roli: {e}")
            await interaction.response.send_message("❌ Brak uprawnień do nadania roli.", ephemeral=True)


# ==============================================================================
# 1B. ZAAWANSOWANY SYSTEM TICKETÓW
# ==============================================================================
class TicketCloseView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔒 Zamknij Ticket", style=discord.ButtonStyle.danger, custom_id="btn_zamknij_ticket_hakerolandia", emoji="🗑️")
    async def zamknij_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_channels:
            if not any(interaction.channel.name.startswith(p) for p in ["ticket-", "zamówienie-", "pomoc-", "pytania-"]):
                await interaction.response.send_message("❌ Nie masz uprawnień do zamknięcia tego ticketa.", ephemeral=True)
                return

        embed = discord.Embed(title="🎫 TICKET ZAMKNIĘTY", description=f"Ten ticket został zamknięty przez {interaction.user.mention}.\nKanał zostanie usunięty za 5 sekund...", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception as e:
            logger.error(f"Błąd usuwania kanału ticketa: {e}")


class TicketSelect(ui.Select):
    def __init__(self, rola_supportu_name: str):
        self.rola_supportu_name = rola_supportu_name
        options = [
            discord.SelectOption(
                label="Pomoc & Wsparcie", 
                description="Problemy techniczne, zgłoszenia i pomoc", 
                emoji="🛠️", 
                value="pomoc"
            ),
            discord.SelectOption(
                label="Pytania Ogólne", 
                description="Ogólne pytania i informacje o serwerze", 
                emoji="❓", 
                value="pytania"
            ),
        ]
        super().__init__(placeholder="Wybierz temat zgłoszenia...", custom_id="select_hakerolandia_ticket_temat", options=options)

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        wybor = self.values[0]

        if wybor == "pomoc":
            tytul_tematu = "Pomoc & Wsparcie"
            opis_tematu = "Problemy techniczne, zgłoszenia i pomoc"
            prefix = "pomoc"
            emoji = "🛠️"
        else:
            tytul_tematu = "Pytania Ogólne"
            opis_tematu = "Ogólne pytania i informacje o serwerze"
            prefix = "pytania"
            emoji = "❓"

        rola_supportu = discord.utils.get(guild.roles, name=self.rola_supportu_name)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }

        if rola_supportu:
            overwrites[rola_supportu] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel_name = f"{prefix}-{user.name}"
        
        try:
            ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        except Exception as e:
            logger.error(f"Błąd tworzenia kanału ticketa: {e}")
            await interaction.response.send_message("❌ Nie udało się utworzyć kanału ticketa.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{emoji} Ticket Zgłoszeniowy — {tytul_tematu}",
            description=f"Witaj {user.mention}!\n\n"
                        f"📌 **Temat zgłoszenia:** {tytul_tematu}\n"
                        f"📝 **Opis:** {opis_tematu}\n\n"
                        f"Opisz dokładnie swój problem lub pytanie. Administracja wkrótce Ci odpowie.",
            color=discord.Color.blurple()
        )
        
        view = TicketCloseView()
        await ticket_channel.send(content=f"{user.mention} {rola_supportu.mention if rola_supportu else ''}", embed=embed, view=view)
        await interaction.response.send_message(f"✅ Utworzono dla Ciebie ticket: {ticket_channel.mention}", ephemeral=True)


class TicketPanelView(ui.View):
    def __init__(self, rola_supportu_name: str):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(rola_supportu_name))


# ==============================================================================
# 2. FORMULARZ OPINII (MODAL)
# ==============================================================================
class OpiniaModal(ui.Modal, title="HAKEROLANDIA — WYSTAW OPINIĘ"):
    def __init__(self):
        super().__init__()

    ocena = ui.TextInput(label="OCENA (np. ⭐⭐⭐⭐⭐ / 5/5):", placeholder="Wpisz ocenę gwiazdkową lub cyfrową", required=True, max_length=20)
    tresc = ui.TextInput(label="TREŚĆ OPINII:", placeholder="Napisz, co sądzisz o realizacji zamówienia...", style=discord.TextStyle.paragraph, required=True, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⭐ NOWA OPINIA O HAKEROLANDIA", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Autor", value=interaction.user.mention, inline=True)
        embed.add_field(name="Ocena", value=self.ocena.value, inline=True)
        embed.add_field(name="Treść", value=self.tresc.value, inline=False)
        embed.set_footer(text="Dziękujemy za opinię! ❤️")

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Twoja opinia została pomyślnie opublikowana! Dziękujemy!", ephemeral=True)


class OpiniePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Wystaw Opinię", style=discord.ButtonStyle.green, custom_id="btn_hakerolandia_wystaw_opinie", emoji="⭐")
    async def wystaw_opinie_btn(self, interaction: discord.Interaction, button: ui.Button):
        rola_klient = discord.utils.get(interaction.guild.roles, name="⭐ • Klient")
        if not rola_klient or rola_klient not in interaction.user.roles:
            await interaction.response.send_message("❌ **Brak uprawnień!** Nie posiadasz wymaganej rangi **⭐ • Klient**.", ephemeral=True)
            return
        await interaction.response.send_modal(OpiniaModal())


# ==============================================================================
# 3. FORMULARZ ZAMÓWIENIA (MODAL)
# ==============================================================================
class ZamowienieModal(ui.Modal, title="HAKEROLANDIA — FORMULARZ ZAMÓWIENIA"):
    def __init__(self, produkt: str, cena_jednostkowa: float, ilosc: int):
        super().__init__()
        self.produkt = produkt
        self.cena_jednostkowa = cena_jednostkowa
        self.ilosc = ilosc

    discord_nick = ui.TextInput(label="JAKI JEST TWÓJ DISCORD NICK:", placeholder="np. HakerPro", required=True, max_length=100)
    platnosc = ui.TextInput(label="METODA PŁATNOŚCI:", placeholder="BLIK / Revolut", required=True, max_length=50)
    uwagi = ui.TextInput(label="UWAGI DO ZAMÓWIENIA:", placeholder="Dodatkowe wytyczne", style=discord.TextStyle.paragraph, required=False, max_length=500)
    kod_rabatowy = ui.TextInput(label="KOD ZNIŻKOWY:", placeholder="np. 40-osob", required=False, max_length=50)
    kod_polecajacy = ui.TextInput(label="KOD POLECAJĄCY:", placeholder="Zostaw puste, jeśli nie masz", required=False, max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        cena_bazowa = self.cena_jednostkowa * self.ilosc
        rabat_tekst = self.kod_rabatowy.value.strip() if self.kod_rabatowy.value else ""
        znizka_procent = 0

        if rabat_tekst:
            dopasowany_kod = next((k for k in AKTYWNE_KODY if k.lower() == rabat_tekst.lower()), None)
            if dopasowany_kod:
                znizka_procent = AKTYWNE_KODY[dopasowany_kod]
                rabat_tekst = dopasowany_kod

        rabat_kwotowo = (cena_bazowa * znizka_procent) / 100
        cena_calkowita = cena_bazowa - rabat_kwotowo
        informacja_o_rabacie = f"-{znizka_procent}% ({rabat_tekst})" if znizka_procent > 0 else "Brak"

        view = PodsumowanieZakupuView(
            produkt=self.produkt, ilosc=self.ilosc, cena=cena_calkowita,
            nick=self.discord_nick.value, platnosc=self.platnosc.value,
            uwagi=self.uwagi.value or "Brak uwag.", rabat=informacja_o_rabacie, polecajacy=self.kod_polecajacy.value or "Nie podano."
        )

        tekst = (
            f"🛒 **HAKEROLANDIA — PODSUMOWANIE ZAMÓWIENIA**\n\n"
            f"• **{self.ilosc}x {self.produkt}** — **{cena_bazowa:.2f} PLN**\n"
            f"• Zniżka: **{informacja_o_rabacie}**\n"
            f"• Cena końcowa do zapłaty: **{cena_calkowita:.2f} PLN**\n\n"
            f"Kliknij przycisk poniżej, aby przejść do płatności."
        )
        await interaction.response.send_message(tekst, view=view, ephemeral=True)


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

        self.add_item(ui.Button(label="Płatność przez Tipply", style=discord.ButtonStyle.link, url="https://tipply.pl/@hakerroblox", emoji="💲"))

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

        STATYSTYKI_SKLEPU["zamowienia_zrealizowane"] += 1

        embed = discord.Embed(title="HAKEROLANDIA — PŁATNOŚĆ I REALIZACJA", color=discord.Color.green())
        embed.add_field(name="Wybrany Pakiet", value=f"{self.ilosc}x {self.produkt} ({self.cena:.2f} PLN)", inline=False)
        embed.add_field(name="Twój Nick", value=self.nick, inline=False)
        embed.add_field(name="Metoda Płatności", value=self.platnosc, inline=False)
        embed.add_field(name="Uwagi", value=self.uwagi, inline=False)
        embed.add_field(name="Kod zniżkowy", value=self.rabat, inline=False)

        close_view = TicketCloseView()
        await ticket_channel.send(content=f"🔔 **Witaj {user.mention}!** Zamówienie zarejestrowane.", embed=embed, view=close_view)
        await interaction.response.edit_message(content=f"✅ Utworzono dla Ciebie prywatny ticket zamówienia: {ticket_channel.mention}.", view=None)


class WyborIlosciSelectView(ui.View):
    def __init__(self, produkt, cena):
        super().__init__(timeout=None)
        self.produkt = produkt
        self.cena = cena

    @ui.select(placeholder="Wybierz ilość sztuk...", custom_id="select_hakerolandia_ilosc", options=[
        discord.SelectOption(label="1 szt.", value="1"),
        discord.SelectOption(label="2 szt.", value="2"),
    ])
    async def select_ilosc(self, interaction: discord.Interaction, select: ui.Select):
        ilosc = int(select.values[0])
        await interaction.response.send_modal(ZamowienieModal(produkt=self.produkt, cena_jednostkowa=self.cena, ilosc=ilosc))


class WyborProduktuSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(placeholder="Wybierz pakiet z listy...", custom_id="select_hakerolandia_produkt", options=[
        discord.SelectOption(label="🟢 START", description="Cena: 19,99 PLN", value="START|19.99"),
        discord.SelectOption(label="🔵 BASIC", description="Cena: 39,99 PLN", value="BASIC|39.99"),
        discord.SelectOption(label="🟣 PREMIUM", description="Cena: 69,99 PLN", value="PREMIUM|69.99"),
        discord.SelectOption(label="🤖 BOT NA ZAMÓWIENIE", description="Cena: 35,99 PLN", value="BOT NA ZAMÓWIENIE|35.99"),
    ])
    async def select_produkt(self, interaction: discord.Interaction, select: ui.Select):
        dane = select.values[0].split("|")
        await interaction.response.send_message(f"🛒 Wybrałeś pakiet: **{dane[0]}** ({dane[1]} PLN/szt.). Wybierz ilość:", view=WyborIlosciSelectView(produkt=dane[0], cena=float(dane[1])), ephemeral=True)


class PanelGlownyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="ZŁÓŻ ZAMÓWIENIE", style=discord.ButtonStyle.green, custom_id="btn_hakerolandia_zlozo_zamowienie", emoji="🛒")
    async def zlozo_zamowienie_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(f"🛒 Wybierz interesujący Cię pakiet:", view=WyborProduktuSelectView(), ephemeral=True)


class YouTubeButtonView(ui.View):
    def __init__(self, link: str):
        super().__init__(timeout=None)
        self.add_item(ui.Button(label="OGLĄDAJ FILM", style=discord.ButtonStyle.link, url=link, emoji="🎬"))

class StronaButtonView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ui.Button(label="Odwiedź hakerroblox.gamer.gd", style=discord.ButtonStyle.link, url="https://hakerroblox.gamer.gd", emoji="🌐"))


# ==============================================================================
# 4. GŁÓWNA KLASA BOTA (WRAZ Z LICZNIKAMI STATYSTYK)
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
        
        self.sprawdz_youtube.start()
        self.aktualizuj_liczniki_loop.start()
        
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
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="HAKEROLANDIA | SKLEP"))

    @tasks.loop(minutes=10)
    async def aktualizuj_liczniki_loop(self):
        for guild in self.guilds:
            try:
                # Liczba widzów (tylko użytkownicy bez botów) oraz botów
                widzowie_count = len([m for m in guild.members if not m.bot])
                boty_count = len([m for m in guild.members if m.bot])
                
                ban_count = 0
                try:
                    bans = [b async for b in guild.fetch_bans()]
                    ban_count = len(bans)
                except Exception:
                    pass

                nowy_user = "Brak"
                try:
                    valid_members = [m for m in guild.members if m.joined_at]
                    if valid_members:
                        najnowszy = max(valid_members, key=lambda m: m.joined_at)
                        nowy_user = najnowszy.name
                except Exception:
                    pass

                for channel in guild.channels:
                    name_lower = channel.name.lower()
                    if "widzowie" in name_lower:
                        nowa_nazwa = f"Widzowie 🧑🏼 : {widzowie_count}"
                        if channel.name != nowa_nazwa:
                            await channel.edit(name=nowa_nazwa)
                    elif "boty" in name_lower:
                        nowa_nazwa = f"Boty 🤖 : {boty_count}"
                        if channel.name != nowa_nazwa:
                            await channel.edit(name=nowa_nazwa)
                    elif "bany" in name_lower:
                        nowa_nazwa = f"Bany : {ban_count}"
                        if channel.name != nowa_nazwa:
                            await channel.edit(name=nowa_nazwa)
                    elif "nowy" in name_lower:
                        nowa_nazwa = f"Nowy : {nowy_user}"
                        if channel.name != nowa_nazwa:
                            await channel.edit(name=nowa_nazwa)
            except Exception as e:
                logger.error(f"Błąd w pętli liczników dla gildii {guild.name}: {e}")

    @aktualizuj_liczniki_loop.before_loop
    async def before_aktualizuj_liczniki(self):
        await self.wait_until_ready()

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
                    embed = discord.Embed(title="🎬 NOWY FILM NA YOUTUBE!", color=0xef4444)
                    embed.add_field(name="Tytuł:", value=tytul, inline=False)
                    embed.add_field(name="Link:", value=link, inline=False)
                    view = YouTubeButtonView(link)
                    await kanal.send("@everyone", embed=embed, view=view)
        except Exception as e:
            logger.error(f"Błąd YouTube RSS: {e}")

    @sprawdz_youtube.before_loop
    async def before_sprawdz_youtube(self):
        await self.wait_until_ready()


bot = HakerolandiaBot()


# ==============================================================================
# 5. KOMENDY SLASH
# ==============================================================================
@bot.tree.command(name="staty-setup", description="Automatycznie tworzy kategorię i kanały statystyk z emotkami (Tylko Admin)")
async def staty_setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return

    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)
    }

    try:
        # Tworzenie dedykowanej kategorii ze statystykami
        kategoria = await guild.create_category(name="📈 | ----statystyki----")

        # Tworzenie kanałów głosowych wewnątrz kategorii
        c1 = await guild.create_voice_channel(name="Widzowie 🧑🏼 : 0", category=kategoria, overwrites=overwrites)
        c2 = await guild.create_voice_channel(name="Boty 🤖 : 0", category=kategoria, overwrites=overwrites)
        c3 = await guild.create_voice_channel(name="Bany : 0", category=kategoria, overwrites=overwrites)
        c4 = await guild.create_voice_channel(name="Nowy : Brak", category=kategoria, overwrites=overwrites)

        await interaction.response.send_message(
            f"✅ Pomyślnie utworzono kategorię **📈 | ----statystyki----** i kanały:\n"
            f"• {c1.mention}\n• {c2.mention}\n• {c3.mention}\n• {c4.mention}\n\n"
            f"*Bot zaktualizuje ich liczbę w ciągu kilku minut.*",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Wystąpił błąd podczas tworzenia statystyk: {e}", ephemeral=True)


@bot.tree.command(name="ticket", description="Wysyła panel systemowy ticketów z wybranymi opcjami (Tylko Admin)")
@discord.app_commands.describe(rola="Wybierz rolę administracyjną do obsługi ticketów")
async def ticket_setup(interaction: discord.Interaction, rola: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień administratora!", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎫 HAKEROLANDIA — POMOC I TICKET",
        description="Wybierz odpowiednią kategorię z menu poniżej, aby otworzyć prywatny ticket.",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Obsługująca rola: {rola.name}")

    view = TicketPanelView(rola_supportu_name=rola.name)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ Wysłano panel ticketów z opcjami (rola: **{rola.name}**).", ephemeral=True)


@bot.tree.command(name="statystyki", description="Wyświetla statystyki sklepu Hakerolandia oraz bota")
async def statystyki(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title="📊 STATYSTYKI HAKEROLANDIA", color=discord.Color.dark_green())
    embed.add_field(name="🛒 Obsłużone zamówienia", value=str(STATYSTYKI_SKLEPU["zamowienia_zrealizowane"]), inline=True)
    embed.add_field(name="🎟️ Aktywne kody zniżkowe", value=str(len(AKTYWNE_KODY)), inline=True)
    embed.add_field(name="👥 Użytkownicy serwera", value=str(guild.member_count if guild else "B/D"), inline=True)
    embed.add_field(name="⚡ Ping bota", value=f"{round(bot.latency * 1000)} ms", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="wyslij-panel", description="Wysyła główny panel składania zamówień")
async def wyslij_panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień!", ephemeral=True)
        return
    embed = discord.Embed(title="HAKEROLANDIA — ZŁÓŻ ZAMÓWIENIE", description="Wybierz pakiet:", color=discord.Color.dark_purple())
    await interaction.channel.send(embed=embed, view=PanelGlownyView())
    await interaction.response.send_message("✅ Wysłano panel sklepu!", ephemeral=True)


@bot.tree.command(name="kod-losuj", description="Losuje zniżkę i rejestruje aktywny kod (Tylko Admin)")
async def kod_losuj(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień!", ephemeral=True)
        return
    znizka = random.randint(5, 20)
    kod = "PROMO-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    AKTYWNE_KODY[kod] = znizka
    await interaction.response.send_message(f"🎲 Wylosowano kod: `{kod}` | Zniżka: **-{znizka}%**", ephemeral=True)


@bot.tree.command(name="cennik", description="Wyświetla oficjalny cennik Hakerolandia")
async def cennik(interaction: discord.Interaction):
    embed = discord.Embed(title="📑 CENNIK HAKEROLANDIA", description="🟢 START (19.99zł)\n🔵 BASIC (39.99zł)\n🟣 PREMIUM (69.99zł)\n🤖 BOT NA ZAMÓWIENIE (35.99zł)", color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="wyslij-opinie", description="Wysyła panel wystawiania opinii (Tylko Admin)")
async def wyslij_opinie(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień!", ephemeral=True)
        return
    embed = discord.Embed(title="HAKEROLANDIA — OPINIE", description="Kliknij, aby wystawić opinię.", color=discord.Color.blurple())
    await interaction.channel.send(embed=embed, view=OpiniePanelView())
    await interaction.response.send_message("✅ Wysłano panel opinii!", ephemeral=True)


@bot.tree.command(name="opinie", description="Otwiera panel wystawiania opinii")
async def opinie(interaction: discord.Interaction):
    rola_klient = discord.utils.get(interaction.guild.roles, name="⭐ • Klient")
    if not rola_klient or rola_klient not in interaction.user.roles:
        await interaction.response.send_message("❌ Brak rangi **⭐ • Klient**.", ephemeral=True)
        return
    await interaction.response.send_modal(OpiniaModal())


@bot.tree.command(name="wyslij-weryfikacje", description="Wysyła weryfikację CAPTCHA (Tylko Admin)")
async def wyslij_weryfikacje(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Brak uprawnień!", ephemeral=True)
        return
    embed = discord.Embed(title="🛡️ Weryfikacja", description="Kliknij przycisk, aby się zweryfikować.", color=discord.Color.gold())
    await interaction.channel.send(embed=embed, view=CaptchaView())
    await interaction.response.send_message("✅ Wysłano weryfikację!", ephemeral=True)


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("KRYTYCZNY BŁĄD: Brak zmiennej DISCORD_TOKEN!")
        return
    bot.run(token)

if __name__ == "__main__":
    main()
