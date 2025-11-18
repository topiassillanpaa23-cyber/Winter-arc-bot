import discord
import os
from discord.ext import commands, tasks
import json
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # aikavyöhyke Suomea varten

TOKEN = os.environ["DISCORD_TOKEN"]


# KANAVA-ID:t
TODAY_PLAN_CHANNEL_ID = 1440185625100943451  # #today-plan
WEEK_VISION_CHANNEL_ID = 1440185692796751942  # #week-vision
LEADERBOARD_CHANNEL_ID = 1440429546175463534  # #leaderboard
FIN_TZ = ZoneInfo("Europe/Helsinki")  # Suomen aikavyöhyke

intents = discord.Intents.default()
intents.message_content = True  # tärkeä, että komennot toimivat
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = Path("winter_arc_data.json")

# --------- POINT SYSTEM: TASKS ---------
TASKS = {
    # Päivittäinen rutiini
    "wake": 3,             # Herätys 05:30
    "morning_workout": 2,  # Aamujumppa
    "protein": 1,          # Proteiinitavoite
    "water": 1,            # 2–3L vettä
    "vitamins": 1,
    "stretch": 1,          # Iltavenyttely
    "tidy": 1,             # Päivän 10–15min siivous
    "sleep_early": 2,      # Nukkumaan ennen 21:30
    "no_phone": 3,         # Ei puhelinta klo 20 jälkeen

    # Treenit (päiväkohtaiset)
    "gym_push": 4,         # Maanantai
    "gym_pull": 4,         # Keskiviikko
    "gym_legs": 4,         # Perjantai
    "light_activity": 2,   # Ti / To (kävely yms.)

    # Kotihommat
    "groceries": 2,        # Ma & Pe
    "dishes": 2,           # Ti & La
    "laundry": 2,          # Ke & Su
    "clean_quick": 3,      # To pikasiivous
    "big_clean": 5,        # Su isompi siivous
}

# --------- REWARDS ---------
REWARDS = {
    "tiktok10": 5,
    "tiktok20": 8,
    "gaming30": 10,
    "gaming60": 20,
    "movie": 35,
}

# --------- WEEK STRUCTURE (Winter Arc) ---------
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DAY_PLAN = {
    "Monday": {
        "label": "PUSH DAY + Groceries",
        "core_tasks": ["gym_push", "groceries"],
    },
    "Tuesday": {
        "label": "RECOVERY + Dishes",
        "core_tasks": ["light_activity", "dishes"],
    },
    "Wednesday": {
        "label": "PULL DAY + Laundry",
        "core_tasks": ["gym_pull", "laundry"],
    },
    "Thursday": {
        "label": "LIGHT DAY + Quick clean",
        "core_tasks": ["light_activity", "clean_quick"],
    },
    "Friday": {
        "label": "LEG DAY + Groceries",
        "core_tasks": ["gym_legs", "groceries"],
    },
    "Saturday": {
        "label": "Optional training + Dishes",
        "core_tasks": ["dishes"],
    },
    "Sunday": {
        "label": "FULL RESET + Laundry + Big clean",
        "core_tasks": ["laundry", "big_clean"],
    },
}

# Päivittäiset rutiinitehtävät streakin laskentaa varten
DAILY_ROUTINE_TASKS = [
    "wake",
    "morning_workout",
    "protein",
    "water",
    "vitamins",
    "stretch",
    "tidy",
    "sleep_early",
    "no_phone",
]
MIN_TASKS_FOR_STREAK = 5  # vähintään näin monta rutiinia / päivä -> onnistunut päivä

# --------- DATA HELPERS ---------
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_user(data, user_id):
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {
            "points": 0,
            "today": {},
            "last_date": None,
            "streak": 0,
            "best_streak": 0,
            "history": {},    # menneiden päivien tehtävät
            "reminders": {},  # päivämäärä -> lista muistutuksista
        }
    else:
        # varmistetaan että kentät löytyy myös vanhoille käyttäjille
        data[user_id].setdefault("streak", 0)
        data[user_id].setdefault("best_streak", 0)
        data[user_id].setdefault("today", {})
        data[user_id].setdefault("last_date", None)
        data[user_id].setdefault("history", {})
        data[user_id].setdefault("reminders", {})
    return data[user_id]

def get_today_name():
    # Käytetään Suomen aikavyöhykettä
    today = datetime.now(FIN_TZ)
    weekday_index = today.weekday()  # 0 = Monday
    return DAY_NAMES[weekday_index]

def reset_if_new_day(user_data):
    """Tarkistaa onko uusi päivä. Jos on, tallentaa eilisen historiaan
    ja päivittää streakin ennen 'today'-tietojen nollausta."""
    today = datetime.now(FIN_TZ)
    today_str = today.strftime("%Y-%m-%d")
    last_date = user_data.get("last_date")

    if last_date != today_str:
        # Arvioidaan edellinen päivä, jos sellainen on
        if last_date is not None:
            # Tallenna eilisen tehtävät historiaan
            history = user_data.setdefault("history", {})
            done_tasks = [t for t, done in user_data["today"].items() if done]
            history[last_date] = done_tasks

            # Streak-logiikka eilisen perusteella
            done_count = sum(
                1 for t in DAILY_ROUTINE_TASKS if user_data["today"].get(t)
            )
            success = done_count >= MIN_TASKS_FOR_STREAK

            if success:
                user_data["streak"] = user_data.get("streak", 0) + 1
                user_data["best_streak"] = max(
                    user_data.get("best_streak", 0),
                    user_data["streak"],
                )
            else:
                user_data["streak"] = 0

        # Nollataan tämän päivän suoritukset ja päivitetään päivämäärä
        user_data["today"] = {}
        user_data["last_date"] = today_str

# --------- HELPER-TEXTERI TODAYPLAN / WEEKPLAN ---------
def build_todayplan_message():
    day_name = get_today_name()
    plan = DAY_PLAN.get(day_name)
    if not plan:
        return "Ei suunnitelmaa tälle päivälle (outoa)."

    label = plan["label"]
    core_tasks = plan["core_tasks"]
    daily_routine = DAILY_ROUTINE_TASKS

    def format_task(t):
        pts = TASKS.get(t, "?")
        return f"- **{t}** ({pts} pts)"

    text = f"**{day_name} — {label}**\n\n"
    text += "__Päivittäinen rutiini:__\n" + "\n".join(format_task(t) for t in daily_routine) + "\n\n"
    text += "__Tämän päivän ydintehtävät:__\n" + "\n".join(format_task(t) for t in core_tasks)
    return text

def build_weekplan_message():
    lines = []
    for day in DAY_NAMES:
        plan = DAY_PLAN[day]
        label = plan["label"]
        tlist = ", ".join(plan["core_tasks"])
        lines.append(f"**{day}** — {label} → {tlist}")
    return "**Winter Arc -viikkosuunnitelma:**\n" + "\n".join(lines)

# --------- LEADERBOARD HELPERS ---------
def build_leaderboard_embed():
    """Rakentaa leaderboard-embedin data-tiedoston perusteella."""
    data = load_data()

    if not data:
        embed = discord.Embed(
            title="Winter Arc – Pistetaulukko",
            description="Kukaan ei ole vielä kerännyt pisteitä. Aloita komennolla `!done wake`.",
            colour=discord.Colour.blue()
        )
        return embed

    entries = []
    for user_id_str, user_data in data.items():
        pts = user_data.get("points", 0)
        try:
            user_id = int(user_id_str)
        except ValueError:
            continue
        entries.append((user_id, pts))

    entries.sort(key=lambda x: x[1], reverse=True)
    top = entries[:10]

    lines = []
    rank = 1
    for uid, pts in top:
        mention = f"<@{uid}>"
        lines.append(f"**{rank}.** {mention} — **{pts}** pts")
        rank += 1

    description = "\n".join(lines) if lines else "Ei vielä pisteitä."

    embed = discord.Embed(
        title="Winter Arc – Pistetaulukko",
        description=description,
        colour=discord.Colour.blue()
    )
    embed.set_footer(text="Pisteitä: !done wake, !done gym_push, !done groceries, jne.")

    return embed

# --------- BOT EVENTS & COMMANDS ---------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not update_daily_leaderboard.is_running():
        update_daily_leaderboard.start()
    if not send_daily_todayplan.is_running():
        send_daily_todayplan.start()
    if not send_week_vision.is_running():
        send_week_vision.start()
    if not send_daily_report.is_running():
        send_daily_report.start()
    if not send_evening_todo.is_running():
        send_evening_todo.start()
    if not send_day_completion_check.is_running():
        send_day_completion_check.start()
    if not send_weekly_summary.is_running():
        send_weekly_summary.start()

@bot.command(name="tomorrowplan")
async def tomorrowplan_cmd(ctx):
    """Näytä huomisen suunnitelma."""
    now = datetime.now(FIN_TZ)
    tomorrow = now + timedelta(days=1)
    day_index = tomorrow.weekday()  # 0 = Monday
    day_name = DAY_NAMES[day_index]

    plan = DAY_PLAN.get(day_name)
    if not plan:
        await ctx.send("Huomiselle ei löytynyt suunnitelmaa (outoa).")
        return

    label = plan["label"]
    core_tasks = plan["core_tasks"]

    def format_task(t):
        pts = TASKS.get(t, "?")
        return f"- **{t}** ({pts} pts)"

    daily_routine = DAILY_ROUTINE_TASKS

    msg = f"📅 **Huominen — {day_name}: {label}**\n\n"
    msg += "__Päivittäinen rutiini:__\n"
    msg += "\n".join(format_task(t) for t in daily_routine)
    msg += "\n\n__Ydintehtävät:__\n"
    msg += "\n".join(format_task(t) for t in core_tasks)

    await ctx.send(msg)


# ------------------------- DAYPLAN CUSTOM DAY -----------------------------
DAY_ALIASES = {
    "monday": "Monday", "mon": "Monday", "ma": "Monday", "maanantai": "Monday",
    "tuesday": "Tuesday", "tue": "Tuesday", "ti": "Tuesday", "tiistai": "Tuesday",
    "wednesday": "Wednesday", "wed": "Wednesday", "ke": "Wednesday", "keskiviikko": "Wednesday",
    "thursday": "Thursday", "thu": "Thursday", "to": "Thursday", "torstai": "Thursday",
    "friday": "Friday", "fri": "Friday", "pe": "Friday", "perjantai": "Friday",
    "saturday": "Saturday", "sat": "Saturday", "la": "Saturday", "lauantai": "Saturday",
    "sunday": "Sunday", "sun": "Sunday", "su": "Sunday", "sunnuntai": "Sunday",
}


@bot.command(name="dayplan")
async def dayplan_cmd(ctx, *, day: str):
    """Näytä minkä tahansa päivän suunnitelma. Esim: !dayplan Monday / !dayplan su / !dayplan torstai"""
    day_lower = day.lower()

    if day_lower not in DAY_ALIASES:
        await ctx.send("Päivää ei tunnistettu. Kokeile esim.:\n`!dayplan monday`, `!dayplan ke`, `!dayplan sunnuntai`")
        return

    day_name = DAY_ALIASES[day_lower]

    plan = DAY_PLAN.get(day_name)
    if not plan:
        await ctx.send("Tälle päivälle ei löytynyt suunnitelmaa.")
        return

    label = plan["label"]
    core_tasks = plan["core_tasks"]

    def format_task(t):
        pts = TASKS.get(t, "?")
        return f"- **{t}** ({pts} pts)"

    daily_routine = DAILY_ROUTINE_TASKS

    msg = f"📅 **{day_name} — {label}**\n\n"
    msg += "__Päivittäinen rutiini:__\n"
    msg += "\n".join(format_task(t) for t in daily_routine)
    msg += "\n\n__Ydintehtävät:__\n"
    msg += "\n".join(format_task(t) for t in core_tasks)

    await ctx.send(msg)


@bot.command(name="tasks")
async def tasks_cmd(ctx):
    """Näytä kaikki tehtävät ja niiden pisteet."""
    lines = [f"**{name}** → {pts} pts" for name, pts in TASKS.items()]
    text = "**Tehtävälista (Winter Arc):**\n" + "\n".join(lines)
    await ctx.send(text)

@bot.command(name="todayplan")
async def todayplan_cmd(ctx):
    """Näytä tämän päivän suunnitelma Winter Arc -ohjelman mukaan."""
    text = build_todayplan_message()
    await ctx.send(text)

@bot.command(name="weekplan")
async def weekplan_cmd(ctx):
    """Näytä koko viikon Winter Arc -rakennetta."""
    text = build_weekplan_message()
    await ctx.send(text)

@bot.command(name="done")
async def done_cmd(ctx, task_name: str):
    """Merkitse tehtävä tehdyksi: !done wake, !done gym_push, jne."""
    task_name = task_name.lower()
    if task_name not in TASKS:
        await ctx.send("Tuntematon tehtävä. Käytä `!tasks` nähdäksesi listan.")
        return

    data = load_data()
    user_data = get_user(data, ctx.author.id)
    reset_if_new_day(user_data)

    today_name = get_today_name()
    today_plan = DAY_PLAN.get(today_name, {})
    core_tasks = today_plan.get("core_tasks", [])

    if user_data["today"].get(task_name, False):
        await ctx.send(f"Olet jo merkinnyt **{task_name}** tehdyksi tänään. Ei lisäpisteitä.")
    else:
        pts = TASKS[task_name]
        user_data["points"] += pts
        user_data["today"][task_name] = True
        save_data(data)

        msg = f"✅ **{task_name}** tehty! +{pts} pts. Yhteensä: **{user_data['points']}** pts."
        if task_name in ["gym_push", "gym_pull", "gym_legs", "light_activity", "groceries", "dishes", "laundry", "clean_quick", "big_clean"]:
            if task_name not in core_tasks:
                msg += f"\n⚠ Huom: **{task_name}** ei normaalisti kuulu **{today_name}**-päivään, mutta sait silti pisteet."
        await ctx.send(msg)

@bot.command(name="points")
async def points_cmd(ctx):
    """Näytä nykyiset pisteet."""
    data = load_data()
    user_data = get_user(data, ctx.author.id)
    reset_if_new_day(user_data)
    save_data(data)
    await ctx.send(f"⭐ {ctx.author.display_name}, sinulla on **{user_data['points']}** pistettä.")

@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard_cmd(ctx):
    """Näytä pistetaulukko (top 10 grindaaajaa)."""
    embed = build_leaderboard_embed()
    await ctx.send(embed=embed)

@bot.command(name="rewards")
async def rewards_cmd(ctx):
    """Näytä palkintokaupan sisältö."""
    lines = [f"**{name}** → {cost} pts" for name, cost in REWARDS.items()]
    await ctx.send("**Reward shop:**\n" + "\n".join(lines))

@bot.command(name="buy")
async def buy_cmd(ctx, reward_name: str):
    """Käytä pisteitä palkintoon: !buy gaming60"""
    reward_name = reward_name.lower()
    if reward_name not in REWARDS:
        await ctx.send("Tuntematon palkinto. Käytä `!rewards` nähdäksesi listan.")
        return

    cost = REWARDS[reward_name]
    data = load_data()
    user_data = get_user(data, ctx.author.id)
    reset_if_new_day(user_data)

    if user_data["points"] < cost:
        await ctx.send(f"Ei tarpeeksi pisteitä. Tarvitset {cost}, sinulla on {user_data['points']}.")
        return

    user_data["points"] -= cost
    save_data(data)
    await ctx.send(f"🎁 Ostit **{reward_name}** {cost} pisteellä! Pisteitä jäljellä: **{user_data['points']}**.")

@bot.command(name="streak")
async def streak_cmd(ctx):
    """Näytä nykyinen streak ja paras streak."""
    data = load_data()
    user_data = get_user(data, ctx.author.id)
    reset_if_new_day(user_data)
    save_data(data)

    streak = user_data.get("streak", 0)
    best = user_data.get("best_streak", 0)

    msg = (
        f"🔥 {ctx.author.display_name}, sinulla on nyt **{streak} päivän** putki.\n"
        f"🏆 Paras putkesi on **{best} päivää**.\n"
        f"(Päivä lasketaan onnistuneeksi, kun saat vähintään {MIN_TASKS_FOR_STREAK} "
        f"päivittäistä rutiinitehtävää tehtyä saman vuorokauden aikana.)"
    )
    await ctx.send(msg)

@bot.command(name="resetday")
async def resetday_cmd(ctx):
    """Nollaa tämän päivän tehtävät (pisteet säilyvät)."""
    data = load_data()
    user_data = get_user(data, ctx.author.id)
    reset_if_new_day(user_data)
    user_data["today"] = {}
    save_data(data)
    await ctx.send("🔄 Tämän päivän tehtävät nollattu. Uusi yritys tälle päivälle.")

@bot.command(name="stats")
async def stats_cmd(ctx, task_name: str):
    """Näytä montako päivänä olet tehnyt tietyn tehtävän: !stats wake"""
    task_name = task_name.lower()
    if task_name not in TASKS:
        await ctx.send("Tuntematon tehtävä. Käytä `!tasks` nähdäksesi kaikki tehtävät.")
        return

    data = load_data()
    user_data = get_user(data, ctx.author.id)
    history = user_data.get("history", {})

    days = 0
    for date_str, tasks in history.items():
        if task_name in tasks:
            days += 1

    await ctx.send(f"📈 **{ctx.author.display_name}**, olet tehnyt tehtävän **`{task_name}`** yhteensä **{days} päivänä**.")

@bot.command(name="monthstats")
async def monthstats_cmd(ctx):
    """Näytä kuluvan kuukauden habit-yhteenveto."""
    data = load_data()
    user_data = get_user(data, ctx.author.id)
    history = user_data.get("history", {})

    now = datetime.now(FIN_TZ)
    year = now.year
    month = now.month

    days_with_any = 0
    successful_days = 0  # päivät, joissa streak-raja täyttyi
    total_tasks = 0

    for date_str, tasks in history.items():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if d.year == year and d.month == month:
            days_with_any += 1
            unique_tasks = set(tasks)
            total_tasks += len(unique_tasks)

            routines_done = sum(1 for t in DAILY_ROUTINE_TASKS if t in unique_tasks)
            if routines_done >= MIN_TASKS_FOR_STREAK:
                successful_days += 1

    msg_lines = [
        f"📆 **Kuukauden habit-tilasto ({now.strftime('%Y-%m')})**",
        "",
        f"• Päiviä, jolloin teit jotain: **{days_with_any}**",
        f"• Päiviä, joissa streak-raja ({MIN_TASKS_FOR_STREAK} rutiinia) täyttyi: **{successful_days}**",
        f"• Suoritettuja eri tehtäviä yhteensä (päivistä laskettuna): **{total_tasks}**",
    ]

    await ctx.send("\n".join(msg_lines))

@bot.command(name="todo")
async def todo_cmd(ctx):
    """Näytä tämän päivän tekemättömät tehtävät."""
    data = load_data()
    user_data = get_user(data, ctx.author.id)
    reset_if_new_day(user_data)

    today_name = get_today_name()
    plan = DAY_PLAN.get(today_name, {})
    core_tasks = plan.get("core_tasks", [])

    daily_routines = DAILY_ROUTINE_TASKS
    today_done = user_data.get("today", {})

    missing_routines = [t for t in daily_routines if not today_done.get(t)]
    missing_core = [t for t in core_tasks if not today_done.get(t)]

    def fmt(t):
        return f"- **{t}** ({TASKS.get(t, '?')} pts)"

    msg = f"📝 **Päivän tekemättömät tehtävät – {today_name}**\n\n"

    msg += "__Päivittäiset rutiinit, tekemättä:__\n"
    if missing_routines:
        msg += "\n".join(fmt(t) for t in missing_routines)
    else:
        msg += "✔ Kaikki rutiinit tehty!"
    msg += "\n\n"

    msg += "__Päivän ydintehtävät, tekemättä:__\n"
    if missing_core:
        msg += "\n".join(fmt(t) for t in missing_core)
    else:
        msg += "✔ Kaikki ydintehtävät tehty!"

    await ctx.send(msg)

@bot.command(name="remind")
async def remind_cmd(ctx, day: str, *, text: str):
    """
    Lisää muistutus tietyllä päivälle.
    Esim:
    !remind tomorrow maksa laskut
    !remind today siivoa keittiö
    !remind 2025-01-30 hammaslääkäri klo 12
    """
    data = load_data()
    user_data = get_user(data, ctx.author.id)

    day_lower = day.lower()
    today = datetime.now(FIN_TZ).date()

    if day_lower in ("today", "tänään"):
        date_obj = today
    elif day_lower in ("tomorrow", "huomenna"):
        date_obj = today + timedelta(days=1)
    else:
        try:
            date_obj = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            await ctx.send("Päivä ei kelpaa. Käytä `today`, `tomorrow` tai muotoa YYYY-MM-DD (esim. 2025-01-30).")
            return

    date_str = date_obj.strftime("%Y-%m-%d")
    reminders = user_data.setdefault("reminders", {})
    reminders.setdefault(date_str, []).append(text)
    save_data(data)

    await ctx.send(f"📌 Lisätty muistutus päivälle **{date_str}**: _{text}_")

# --------- AUTOMAATTINEN LEADERBOARD KLO 06:00 (MUOKKAA SAMA VIesti) ---------
@tasks.loop(minutes=1)
async def update_daily_leaderboard():
    """Päivittää leaderboardin joka aamu klo 06:00 Suomen aikaa muokkaamalla samaa viestiä."""
    now = datetime.now(FIN_TZ)
    if now.hour == 6 and now.minute == 0:
        channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
        if channel is None:
            return

        data = load_data()
        meta = data.get("_meta", {})
        msg_id = meta.get("leaderboard_message_id")

        embed = build_leaderboard_embed()

        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass

        new_msg = await channel.send(embed=embed)
        meta["leaderboard_message_id"] = new_msg.id
        data["_meta"] = meta
        save_data(data)

# --------- AUTOMAATTINEN TODAYPLAN JOKA PÄIVÄ KLO 05:30 ---------
@tasks.loop(minutes=1)
async def send_daily_todayplan():
    """Lähettää joka päivä klo 05:30 päivän todayplanin TODAY_PLAN_CHANNEL_ID-kanavalle."""
    now = datetime.now(FIN_TZ)
    if now.hour == 5 and now.minute == 30:
        channel = bot.get_channel(TODAY_PLAN_CHANNEL_ID)
        if channel is None:
            return
        text = build_todayplan_message()
        await channel.send(text)

# --------- AUTOMAATTINEN ENSI VIIKON OHJELMA SUNNUNTAISIN KLO 18:00 ---------
@tasks.loop(minutes=1)
async def send_week_vision():
    """Lähettää joka sunnuntai klo 18:00 ensi viikon ohjelman WEEK_VISION_CHANNEL_ID-kanavalle."""
    now = datetime.now(FIN_TZ)
    # weekday(): Monday=0 ... Sunday=6
    if now.weekday() == 6 and now.hour == 18 and now.minute == 0:
        channel = bot.get_channel(WEEK_VISION_CHANNEL_ID)
        if channel is None:
            return
        text = build_weekplan_message()
        await channel.send(text)

# --------- AUTOMAATTINEN PÄIVÄRAPORTTI KLO 21:00 (DM) ---------
@tasks.loop(minutes=1)
async def send_daily_report():
    """Lähettää joka ilta klo 21:00 käyttäjälle raportin päivän suorituksista (DM)."""
    now = datetime.now(FIN_TZ)
    if now.hour == 21 and now.minute == 0:
        data = load_data()

        for user_id_str, user_data in data.items():
            # ohitetaan meta-data (esim. "_meta")
            if not user_id_str.isdigit():
                continue

            today_tasks = [t for t, done in user_data.get("today", {}).items() if done]
            routines_done = sum(1 for t in DAILY_ROUTINE_TASKS if user_data["today"].get(t))
            today_points = sum(TASKS.get(t, 0) for t in today_tasks)
            streak_today = routines_done >= MIN_TASKS_FOR_STREAK
            current_streak = user_data.get("streak", 0)

            try:
                user = await bot.fetch_user(int(user_id_str))
            except Exception:
                continue

            msg_lines = [
                f"📊 **Päivän raportti ({now.strftime('%Y-%m-%d')})**",
                "",
                f"• Tehtyjä rutiinitehtäviä tänään: **{routines_done}**",
                f"• Päivän pisteet (tehtävistä): **{today_points}**",
                f"• Nykyinen streak (eiliseen asti): **{current_streak}** päivää",
                f"• Tämä päivä täyttää streakin rajan ({MIN_TASKS_FOR_STREAK} rutiinia): **{'KYLLÄ' if streak_today else 'EI'}**",
            ]
            try:
                await user.send("\n".join(msg_lines))
            except Exception:
                continue  # esim. DM estetty

# --------- AUTOMAATTINEN 18:00 TODO + MUISTUTUKSET (DM) ---------
@tasks.loop(minutes=1)
async def send_evening_todo():
    """Lähettää klo 18:00 DM-muistutuksen: mitkä tehtävät tehty/tekemättä + päivän muistutukset."""
    now = datetime.now(FIN_TZ)
    if now.hour == 18 and now.minute == 0:
        data = load_data()
        today_name = get_today_name()
        today_str = now.strftime("%Y-%m-%d")

        for user_id_str, user_data in data.items():
            if not user_id_str.isdigit():
                continue

            today_done = user_data.get("today", {})
            plan = DAY_PLAN.get(today_name, {})
            core_tasks = plan.get("core_tasks", [])
            routines = DAILY_ROUTINE_TASKS

            def line_for(t):
                done = today_done.get(t, False)
                emoji = "✅" if done else "⬜"
                return f"{emoji} {t} ({TASKS.get(t, '?')} pts)"

            routine_lines = [line_for(t) for t in routines]
            core_lines = [line_for(t) for t in core_tasks]

            reminders = user_data.get("reminders", {})
            todays_reminders = reminders.get(today_str, [])

            msg_lines = [
                f"⏰ **18:00 muistutus – {today_name}**",
                "",
                "__Päivittäiset rutiinit:__",
            ]
            msg_lines += routine_lines or ["(Ei rutiineja määritelty.)"]
            msg_lines += [
                "",
                "__Päivän ydintehtävät:__",
            ]
            msg_lines += core_lines or ["(Ei ydintehtäviä tälle päivälle.)"]
            msg_lines += [
                "",
                "__📌 Muistutukset tälle päivälle:__",
            ]
            if todays_reminders:
                for txt in todays_reminders:
                    msg_lines.append(f"• {txt}")
                # poistetaan tämän päivän muistutukset, etteivät toistu
                del reminders[today_str]
                user_data["reminders"] = reminders
                save_data(data)
            else:
                msg_lines.append("Ei erillisiä muistutuksia tälle päivälle.")

            try:
                user = await bot.fetch_user(int(user_id_str))
                await user.send("\n".join(msg_lines))
            except Exception:
                continue

# --------- AUTOMAATTINEN 21:30 – TARKISTUS: TEITKÖ KAIKEN? (DM) ---------
@tasks.loop(minutes=1)
async def send_day_completion_check():
    """Lähettää klo 21:30 DM-viestin, jossa kerrotaan onko päivän kaikki tehtävät tehty."""
    now = datetime.now(FIN_TZ)
    if now.hour == 21 and now.minute == 30:
        data = load_data()
        today_name = get_today_name()

        plan = DAY_PLAN.get(today_name, {})
        core_tasks = plan.get("core_tasks", [])
        routines = DAILY_ROUTINE_TASKS

        for user_id_str, user_data in data.items():
            if not user_id_str.isdigit():
                continue

            today_done = user_data.get("today", {})

            def line_for(t):
                done = today_done.get(t, False)
                emoji = "✅" if done else "❌"
                return f"{emoji} {t}"

            routine_lines = [line_for(t) for t in routines]
            core_lines = [line_for(t) for t in core_tasks]

            all_routines_done = all(today_done.get(t, False) for t in routines)
            all_core_done = all(today_done.get(t, False) for t in core_tasks)
            everything_done = all_routines_done and all_core_done

            if everything_done:
                status_msg = (
                    "🎉 **Täydellinen päivä!**\n"
                    "Olet tehnyt **kaikki** tämän päivän tehtävät.\n"
                    "🔥 Todella kova suoritus!"
                )
            else:
                status_msg = (
                    "⚠️ **Et saanut kaikkea tehtyä tänään.**\n"
                    "Ei haittaa — huomenna uusi mahdollisuus! 💪"
                )

            message = [
                f"🌙 **21:30 päivän tarkistus – {today_name}**",
                "",
                status_msg,
                "",
                "__Päivittäiset rutiinit:__",
            ]
            message += routine_lines
            message += [
                "",
                "__Päivän ydintehtävät:__",
            ]
            message += core_lines

            try:
                user = await bot.fetch_user(int(user_id_str))
                await user.send("\n".join(message))
            except Exception:
                continue

# --------- AUTOMAATTINEN VIIKKORAPORTTI SUNNUNTAISIN KLO 20:00 (DM) ---------
@tasks.loop(minutes=1)
async def send_weekly_summary():
    """Lähettää sunnuntaisin klo 20:00 viikkoraportin viimeisestä 7 päivästä (DM)."""
    now = datetime.now(FIN_TZ)
    # Sunday = 6
    if now.weekday() == 6 and now.hour == 20 and now.minute == 0:
        data = load_data()
        today_date = now.date()

        for user_id_str, user_data in data.items():
            if not user_id_str.isdigit():
                continue

            history = user_data.get("history", {})
            # kerätään viimeiset 7 päivää (mukana tänään)
            dates = [today_date - timedelta(days=i) for i in range(7)]
            dates.reverse()  # vanhimmasta uusimpaan

            total_points = 0
            days_with_any = 0
            success_days = 0  # päivät, joissa streak-raja täyttyi
            per_day_lines = []

            for d in dates:
                date_str = d.strftime("%Y-%m-%d")
                # historiassa edelliset päivät, tänään -> yhdistelmä today-sanakirjasta
                if date_str == user_data.get("last_date"):
                    tasks_done = [t for t, done in user_data.get("today", {}).items() if done]
                else:
                    tasks_done = history.get(date_str, [])

                if not tasks_done:
                    per_day_lines.append(f"{date_str}: (ei tehtäviä)")
                    continue

                days_with_any += 1
                unique_tasks = set(tasks_done)
                day_points = sum(TASKS.get(t, 0) for t in unique_tasks)
                total_points += day_points

                routines_done = sum(1 for t in DAILY_ROUTINE_TASKS if t in unique_tasks)
                if routines_done >= MIN_TASKS_FOR_STREAK:
                    success_days += 1
                    flag = "✅"
                else:
                    flag = "⚠️"

                per_day_lines.append(
                    f"{date_str}: {flag} {len(unique_tasks)} tehtävää, {day_points} pts (rutiineja: {routines_done})"
                )

            if days_with_any == 0:
                # ei lähe viestiä jos koko viikko tyhjä
                continue

            best_streak = user_data.get("best_streak", 0)

            msg_lines = [
                "📈 **Viikkoraportti (viimeiset 7 päivää)**",
                "",
                f"• Päiviä, jolloin teit jotain: **{days_with_any}/7**",
                f"• Päiviä, joissa streak-raja ({MIN_TASKS_FOR_STREAK} rutiinia) täyttyi: **{success_days}**",
                f"• Pisteitä yhteensä: **{total_points}**",
                f"• Paras streak tähän mennessä: **{best_streak}** päivää",
                "",
                "__Päiväkohtaiset rivit:__",
            ]
            msg_lines += per_day_lines

            try:
                user = await bot.fetch_user(int(user_id_str))
                await user.send("\n".join(msg_lines))
            except Exception:
                continue

bot.run(TOKEN)


