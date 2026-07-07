# Yeah HQ Bot v3.0 — @YeahHQ_Bot

## Configurare (Railway → Variables)

Toate setările sensibile acum se citesc din variabilele de mediu, nu mai sunt scrise în cod.
În Railway: Project → Service-ul botului → tab **Variables** → **New Variable**, adaugă fiecare din următoarele:

| Variabilă | Obligatoriu | Exemplu | Descriere |
|---|---|---|---|
| `BOT_TOKEN` | ✅ da | `123456:AAExxxxxxx` | Token-ul de la @BotFather |
| `BOT_OWNER_INITIAL_IDS` | ✅ da | `8510342956` sau `111,222` | ID-ul (ID-urile) tău Telegram numeric, separate prin virgulă dacă sunt mai multe |
| `BOT_OWNER_USERNAME` | opțional | `Stefanwt` | Username-ul tău (fără @), pentru afișare/potrivire |
| `BOT_USERNAME` | opțional (implicit `YeahHQ_Bot`) | `YeahHQ_Bot` | Username-ul botului (fără @) |
| `MINI_APP_URL` | opțional | `https://yeahhq-production.up.railway.app/miniapp` | URL-ul mini-aplicației, vezi mai jos |
| `WEB_PORT` | opțional (implicit `8080`) | `8080` | Portul serverului mini-app |

**De ce ai avut problema:** ai schimbat contul de Telegram, deci ai un `user_id` nou. Botul recunoștea drept
proprietar doar ID-ul vechi, scris direct în `config.py`. Acum e suficient să pui noul tău ID în
`BOT_OWNER_INITIAL_IDS` din Railway și să dai redeploy — nu mai trebuie să atingi codul niciodată când
schimbi contul sau token-ul. Poți afla ID-ul tău Telegram scriind `/whoami` botului (după ce e pornit) sau
întrebând @userinfobot.

Dacă lipsește `BOT_TOKEN` sau `BOT_OWNER_INITIAL_IDS`, botul se oprește imediat la pornire cu un mesaj
clar în loguri, ca să nu descoperi problema abia când încerci să-l folosești.

## Mini App URL

Domeniul se vede în Railway → proiectul tău → Settings → Domains. Pune-l în variabila
`MINI_APP_URL` de mai sus (nu mai e nevoie să editezi `config.py`).

## Структура файлов

```
bot.py          — основной код бота
config.py       — токен, настройки
database.py     — база данных SQLite
miniapp.html    — мини-приложение (раздаётся автоматически на порту 8080)
requirements.txt
start.sh
```

## Как работает мини-приложение

- При запуске бота автоматически стартует веб-сервер на порту, заданном в `WEB_PORT` (по умолчанию **8080**)
- `miniapp.html` доступен по адресу: `https://ваш-домен.up.railway.app/miniapp`
- После того как узнаете домен — впишите его в переменную `MINI_APP_URL` в Railway и сделайте redeploy

## Команды бота

### Модерация
| Команда | Описание |
|---------|----------|
| /mute @user [мин] | Замолчать |
| /unmute @user | Снять мут |
| /kick @user | Кикнуть |
| /ban @user [дней] | Забанить |
| /unban @user | Разбанить |
| /warn @user [причина] | Предупреждение (платно) |
| /unwarn @user | Снять варны (платно) |
| /pin | Закрепить сообщение (платно) |
| /report [причина] (реплай) | Пожаловаться администраторам чата (платно) |

### Должности
| Команда | Описание |
|---------|----------|
| /promote @user [роль] | Повысить |
| /demote @user | Понизить |
| /roles | Список ролей |
| /whoami | Ваша должность |

### Развлечения (платно)
| Команда | Описание |
|---------|----------|
| /duel @user | Интерактивная дуэль |
| /luck | Рулетка 1-100 |
| /casino | Казино |
| /marry @user | Предложение брака |
| /divorce | Развод |
| /marriages | Список пар |

### Настройки (платно)
| Команда | Описание |
|---------|----------|
| /welcome Текст | Приветствие новых |
| /rules [Текст] | Правила чата |
| /antiflood N | Антифлуд |
| /poll Вопрос|Вар1|Вар2 | Голосование |
| /note add/get/del/list | Заметки |
| /filter ключ ответ | Автофильтры |

### Профиль
| Команда | Описание |
|---------|----------|
| /profile [@user] | Профиль |
| /achievements | Достижения |
| /top | Топ активности |
| /chatstats | Статистика чата |

### Только владелец
| Команда | Описание |
|---------|----------|
| /botstats | Полная статистика + заработок |
| /allgroups | Все группы бота |
| /botowners | Список владельцев |
| /addowner @user | Добавить совладельца |
| /removeowner @user | Убрать совладельца |
| /grantfree @user feature | Бесплатный доступ |
| /revokefree @user feature | Отозвать доступ |
| /botstats | Статистика + заработок |
| /divorceforce @user | Расторгнуть любой брак |
| /disablechat | Отключить бота в чате |
| /enablechat | Включить бота в чате |
| /gban @user [причина] | Глобальный бан во всех группах бота |
| /ungban @user | Снять глобальный бан |
| /gbanlist | Список всех глобальных банов |

## Альтернативные команды через !
!мут, !размут, !кик, !бан, !разбан, !варн, !повысить, !разжаловать,
!роли, !профиль, !+брак, !развод, !браки, !топ, !достижения,
!магазин, !помощь, !команды, !правила, !казино, !рулетка

## Команды для @BotFather (вставить как список команд)
```
start - Запустить бота
help - Команды по разделам
shop - Магазин функций
profile - Мой профиль
top - Топ активности
chatstats - Статистика чата
miniapp - Открыть мини-приложение
mute - Замутить пользователя
unmute - Снять мут
kick - Кикнуть пользователя
ban - Забанить пользователя
unban - Разбанить пользователя
warn - Предупреждение
promote - Повысить в должности
demote - Понизить в должности
roles - Список ролей
duel - Вызов на дуэль
luck - Рулетка
casino - Казино
marry - Предложение брака
divorce - Развод
note - Заметки чата
filter - Автофильтры
```
