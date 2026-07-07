import os

# ═══════════════════════════════════════════════════════════════════
#  Toate valorile de mai jos se citesc din variabilele de mediu.
#  In Railway: Project -> Service -> Variables -> adaugi fiecare cheie.
#  Nu mai e nevoie sa editezi acest fisier sau sa faci commit la token.
# ═══════════════════════════════════════════════════════════════════

# Token-ul botului, de la @BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Username-ul (fara @) al proprietarului principal al botului
BOT_OWNER_USERNAME = os.environ.get("BOT_OWNER_USERNAME", "")

# ID-urile numerice Telegram ale proprietarilor initiali.
# Poti pune mai multe, separate prin virgula: "123456,987654"
_owner_ids_raw = os.environ.get("BOT_OWNER_INITIAL_IDS", "")
BOT_OWNER_INITIAL_IDS = [
    int(x.strip()) for x in _owner_ids_raw.split(",") if x.strip().isdigit()
]

# Username-ul botului (fara @)
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YeahHQ_Bot")

# URL-ul Mini App-ului
MINI_APP_URL = os.environ.get("MINI_APP_URL", "")

# Portul pentru serverul web al mini-aplicatiei
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))

# Validare rapida la pornire - opreste botul cu un mesaj clar
# in loc sa esueze silentios daca lipseste token-ul.
if not BOT_TOKEN:
    raise RuntimeError(
        "Lipseste variabila de mediu BOT_TOKEN. "
        "Adauge-o in Railway -> Variables -> BOT_TOKEN."
    )
if not BOT_OWNER_INITIAL_IDS:
    raise RuntimeError(
        "Lipseste variabila de mediu BOT_OWNER_INITIAL_IDS. "
        "Pune propriul tau Telegram user ID acolo (poti afla ID-ul scriind /whoami botului, "
        "sau intrebnd @userinfobot)."
    )

PRICES = {
    "warns":        50,
    "welcome":      30,
    "rules":        20,
    "antiflood":    40,
    "luck":         25,
    "duel":         60,
    "poll":         35,
    "marry":        15,
    "casino":       45,
    "achievements": 30,
    "pin":          20,
    "notes":        25,
    "report":       30,
    "gban":         50,
    "filters":      35,
}
