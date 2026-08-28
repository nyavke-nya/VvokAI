/* Russian for the interface.
 *
 * A translation pass over the finished DOM rather than a string table threaded
 * through every template. app.js builds its views by writing innerHTML in about
 * thirty places; wiring a lookup into each would touch all of them and go stale
 * the moment any is edited.
 *
 * Two things the first version got wrong, both fixed here:
 *
 *   Coverage. It carried 38 strings against 236 in the interface - 16%. The
 *   dictionary below is the whole thing. Anything genuinely untranslatable
 *   (placeholders, product names, units) is listed in KEEP so it is obviously
 *   deliberate rather than forgotten.
 *
 *   Switching back. It relied on reloading the page, because a pass can turn
 *   English into Russian but not the reverse - the original is gone by then.
 *   Every translated node now keeps its English text, so switching either way
 *   is instant and complete.
 */

(() => {
    "use strict";

    // Left in English on purpose: names, placeholders, units, fragments that
    // are only half a sentence in the markup.
    const KEEP = new Set([
        "VvokAI", "Brawl Stars", "Pyla", "PylaAI", "Discord", "Telegram",
        "#PLAYER", "eyJ0eXAiOiJKV1Qi...", "you@example.com", "min",
        "Win%", "Loss%", "OCR", "ADB", "IP", "FPS", "MP4", "API",
    ]);

    const RU = {
        // ---- navigation and chrome
        "Dashboard": "Главная", "Brawlers": "Бойцы", "Playstyles": "Стили игры",
        "History": "История", "Settings": "Настройки", "Library": "Библиотека",
        "Recent": "Недавние", "Selected": "Выбрано", "All": "Все", "Name": "Имя",
        "Use": "Взять", "Matches": "Матчи", "Wins": "Победы", "Losses": "Поражения",
        "Win Rate": "Процент побед", "Universal": "Универсальный",
        "Preparing local session...": "Подготовка локального сеанса...",

        // ---- status pills
        "Idle": "Простой", "Running": "Работает", "Paused": "Пауза",
        "Stopping": "Остановка", "Pausing": "Встаёт на паузу",
        "Offline": "Не в сети", "Local mode": "Локальный режим",
        "Live": "Вживую", "Ready": "Готов", "Manual mode": "Ручной режим",

        // ---- runtime panel
        "Runtime": "Запуск", "Start": "Старт", "Pause": "Пауза", "Stop": "Стоп",
        "Go to Brawlers": "Перейти к бойцам",
        "VvokAI is currently running": "VvokAI сейчас работает",
        "VvokAI is paused": "VvokAI на паузе",
        "VvokAI is pausing": "VvokAI встаёт на паузу",
        "Queue is ready. Start VvokAI from here.": "Очередь готова, можно запускать.",
        "Resolve runtime state before starting.": "Сначала разберись с состоянием запуска.",
        "Add at least one brawler to the queue before starting.":
            "Добавь в очередь хотя бы одного бойца.",
        "Pyla is paused in the lobby. Press Start to resume.":
            "Бот на паузе в лобби. Нажми «Старт», чтобы продолжить.",
        "Pyla is shutting down. This should only take a few seconds.":
            "Бот завершает работу, это займёт пару секунд.",
        "Pause requested. Pyla will stop in the lobby.":
            "Пауза запрошена, бот остановится в лобби.",
        "Pyla runtime started.": "Бот запущен.",
        "Pyla runtime resumed.": "Бот продолжил работу.",

        // ---- playstyles
        "Active Playstyle": "Активный стиль", "Browse": "Выбрать",
        "No playstyle selected": "Стиль не выбран",
        "No playstyle data available.": "Нет данных о стиле.",
        "No playstyles match the current search or filter.":
            "Ни один стиль не подходит под поиск или фильтр.",
        "Most used playstyles": "Чаще всего используемые стили",
        "Playstyle activated.": "Стиль игры включён.",
        "No metadata": "Без описания",
        "Select a playstyle to surface its brawlers and gamemodes here.":
            "Выбери стиль, и здесь появятся его бойцы и режимы.",

        // ---- queue and brawlers
        "Queue": "Очередь", "Brawler Queue": "Очередь бойцов",
        "Open Brawlers": "Открыть бойцов", "Search Brawlers": "Поиск бойцов",
        "Selected Brawler": "Выбранный боец",
        "Current Trophies": "Текущие кубки", "Target Trophies": "Цель по кубкам",
        "Target Amount": "Целевое значение", "Target Wins": "Цель по победам",
        "Current Wins": "Текущие победы", "Current Win Streak": "Текущая серия побед",
        "Automatically pick this brawler": "Выбирать этого бойца автоматически",
        "Choose a brawler to configure it.": "Выбери бойца, чтобы настроить его.",
        "No brawlers match the current search.": "Никто не подходит под поиск.",
        "Build a queue from the Brawlers tab to see it here.":
            "Собери очередь на вкладке «Бойцы», и она появится здесь.",
        "Select a brawler and add it to the run order":
            "Выбери бойца и добавь его в порядок прохождения",
        "Queue cleared.": "Очередь очищена.",
        "Queue reordered.": "Порядок очереди изменён.",

        // ---- history
        "Match History": "История матчей", "Recent results": "Последние результаты",
        "Recently played": "Недавно сыграно", "Trophy Curve": "Кривая кубков",
        "No match history has been recorded yet.": "История матчей пока пуста.",
        "No recent match rows available.": "Свежих матчей нет.",
        "Not enough trophy data to draw a curve yet.":
            "Пока мало данных по кубкам, чтобы построить кривую.",
        "Click to see more info": "Нажми, чтобы увидеть подробности",

        // ---- settings sections
        "General": "Общее", "Runtime and environment": "Запуск и окружение",
        "Bot": "Бот", "Behavior": "Поведение", "Debug": "Отладка",
        "Diagnostics": "Диагностика", "Timers": "Таймеры",
        "Timing controls": "Управление таймингами", "Webhook": "Вебхук",
        "Integrations": "Интеграции", "Combat and recovery": "Бой и восстановление",
        "Reset Settings": "Сбросить настройки", "Reset to defaults": "Вернуть по умолчанию",

        // ---- general settings
        "Player Tag": "Тег игрока",
        "Used to autofill live trophies and win streaks inside the brawler editor. Use your Brawl Stars player tag, not your Supercell ID.":
            "Подставляет живые кубки и серии побед в редакторе бойцов. Нужен тег игрока Brawl Stars, а не Supercell ID.",
        "Brawl Stars API Token": "Токен Brawl Stars API",
        "Free from developer.brawlstars.com. Log in, open My Account, Create New Key. The key is tied to the one IP address you create it from and stops working when your provider changes it - ranges like 0.0.0.0/0 are refused, whatever you may have read. Fill in the two fields below and the bot will reissue the key by itself when that happens. Paste the whole key here. (Win streaks are not published by the API, so those stay as you set them.)":
            "Бесплатно на developer.brawlstars.com: войти, My Account, Create New Key. Ключ привязан к тому единственному адресу, с которого создан, и перестаёт работать при его смене — диапазоны вроде 0.0.0.0/0 портал отклоняет, что бы про них ни писали. Заполни два поля ниже, и бот будет перевыпускать ключ сам. Вставь ключ целиком. (Серии побед API не отдаёт, они останутся как ты их задал.)",
        "Every key is tied to one IP address, so this is how trophy sync survives your provider changing it: the bot logs in, reissues the key for the new address and carries on. Without it, sync stops working the next time your address moves.":
            "Любой ключ привязан к одному адресу, и это единственный способ пережить его смену: бот зайдёт в портал, перевыпустит ключ под новый адрес и продолжит. Без этого синхронизация кубков умрёт при первой же смене адреса.",
        "The same password you use on developer.brawlstars.com. Stored in cfg/general_config.toml on this machine, never shown back here and never written to logs - but it is a password in a plain file. Leave both fields empty if that bothers you; trophies then stop syncing whenever your address changes, and nothing else breaks.":
            "Тот же пароль, что на developer.brawlstars.com. Хранится в cfg/general_config.toml на этой машине, обратно сюда не показывается и в логи не пишется — но это пароль в текстовом файле. Если смущает, оставь оба поля пустыми: тогда кубки перестанут синхронизироваться при смене адреса, и больше ничего не сломается.",
        "Developer Portal Email": "Почта портала разработчика",
        "Developer Portal Password": "Пароль портала разработчика",
        "Leave empty if your key uses 0.0.0.0/0": "Оставь пустым, если ключ с 0.0.0.0/0",
        "Default Trophy Target": "Цель по кубкам по умолчанию",
        "Default trophy target used when adding a new brawler to the queue.":
            "Какая цель по кубкам ставится новому бойцу в очереди.",
        "Run Time": "Время работы",
        "How long Pyla runs before cooldown logic takes over.":
            "Сколько бот работает, прежде чем уйти на перерыв.",
        "Max IPS": "Предел IPS",
        "Processing cap. Use auto if you want Pyla to manage it.":
            "Ограничение обработки. auto — пусть бот решает сам.",
        "Threads": "Потоки",
        "Worker thread count. Auto keeps the current behavior.":
            "Число рабочих потоков. auto оставляет как есть.",
        "OCR Scale": "Масштаб OCR",
        "Scale factor used before OCR work.": "Во сколько раз уменьшать кадр перед распознаванием текста.",
        "Trophies Multiplier": "Множитель кубков",
        "Useful for custom arenas or multiplier-based modes.":
            "Пригодится для своих арен и режимов с множителем.",
        "Emulator Port": "Порт эмулятора",
        "ADB port used for the emulator instance.": "Порт ADB для эмулятора.",
        "Package Name": "Имя пакета",
        "Android package used when restarting Brawl Stars.":
            "Пакет Android, который перезапускается вместе с игрой.",
        "Load Queue On Startup": "Загружать очередь при старте",
        "Load the latest saved queue when the web UI starts.":
            "Подтягивать последнюю сохранённую очередь при открытии интерфейса.",
        "Play Order": "Порядок игры", "In Order": "По порядку",
        "Lowest to Highest": "От меньших к большим",
        "Highest to Lowest": "От больших к меньшим",

        // ---- bot settings
        "Perceived Tile Size": "Размер клетки",
        "Map tile size in pixels used by playstyle movement and wall-aware targeting.":
            "Размер клетки карты в пикселях: по нему считается движение и обход стен.",
        "Attack Range Multiplier": "Множитель дальности атаки",
        "Webhook and alerts": "Вебхуки и уведомления",
        "Loading settings...": "Загрузка настроек...",
        // ---- playstyles page
        "Search by playstyle, brawler, or gamemode":
            "Поиск по стилю, бойцу или режиму",
        "Import": "Импорт",
        "Active playstyle": "Активный стиль",
        "No playstyles found": "Стилей не найдено",

        // The descriptions come from the JSON header of each .pyla file. They
        // are translated here rather than in the files so the files stay
        // readable to everyone who is not Russian - the playstyle format is
        // shared with upstream.
        "All four official playstyles merged into one archetype-aware brain, plus projectile dodging. Glides smoothly by default and snaps hard only while evading.":
            "Все четыре официальных стиля, объединённые в один мозг с пониманием архетипов, плюс уворот от снарядов. По умолчанию движется плавно и рвёт с места только в уворот.",
        "The unified brain tuned to press rather than trade. Takes fights the cautious style declines, chases further, and holds ground while outnumbered - with dodging still on, because that is what keeps an aggressive bot alive.":
            "Тот же мозг, настроенный давить, а не разменивать. Берёт бои, от которых осторожный стиль отказывается, преследует дальше и держит позицию в меньшинстве — уворот при этом включён, потому что именно он и оставляет агрессивного бота в живых.",
        "Same tactics as Unified + Dodge - archetype spacing, regrouping, health awareness, smooth movement - with the projectile stack removed. The tracker thread never starts, so it costs no CPU.":
            "Та же тактика, что у Unified + Dodge — дистанция по архетипу, сбор к союзнику, учёт здоровья, плавное движение — но без слежения за снарядами. Поток трекера не запускается вовсе, так что процессор он не ест.",

        "Automatic Updates": "Автообновление",
        "Check for a newer version on startup and install it. Turn this off to freeze the setup you have - an update cannot then change anything under you.":
            "Проверять обновления при запуске и ставить их. Выключи, чтобы заморозить текущую сборку — тогда обновление ничего не поменяет без спроса.",
        "Telegram bot token used for notifications and for remote control. Send /help in your chat with the bot to see the commands. Only one copy of the bot can use a token at a time.":
            "Токен телеграм-бота для уведомлений и для управления. Отправь боту /help в личку, чтобы увидеть список команд. Один токен — одна запущенная копия бота.",
        "Telegram chat ID that receives notifications. Commands are only accepted from this chat; anything sent from anywhere else is ignored.":
            "ID чата, куда приходят уведомления. Команды принимаются только из него, всё остальное игнорируется.",
        "Scales every brawler's attack and super range. The built-in table is measured short, so the bot used to open fire at about half of its real reach; 1.35 puts it at about three quarters. Raise it to shoot from further out, lower it if shots start missing. 1.0 is the old behaviour.":
            "Множит дальность атаки и супера у всех бойцов. Встроенная таблица занижена, из-за неё бот открывал огонь примерно с половины своей настоящей дистанции; 1.35 поднимает это до трёх четвертей. Больше — стреляет издалека, меньше — если промахивается. 1.0 — как было раньше.",
        "Minimum Movement Delay": "Минимальная пауза движения",
        "Lower bound between movement actions.": "Нижний предел между командами движения.",
        "Unstuck Delay": "Задержка разблокировки",
        "Delay before the unstuck routine fires.": "Через сколько включается выход из застревания.",
        "Unstuck Hold Time": "Удержание разблокировки",
        "How long the unstuck move is held.": "Сколько держится движение на выход.",
        "Wall Confidence": "Порог стен",
        "Confidence threshold for wall detection.": "Порог уверенности для распознавания стен.",
        "Entity Confidence": "Порог бойцов",
        "Confidence threshold for player and enemy detections.":
            "Порог уверенности для распознавания игрока и врагов.",
        "Decline Team Invites": "Отклонять приглашения в команду",
        "Turn down team invites and mute the sender for ten minutes, so a stream of invitations cannot interrupt the farm. Checked every couple of seconds while out of a match.":
            "Отклонять приглашения в команду и мутить отправителя на десять минут, чтобы поток приглашений не мешал фарму. Проверяется раз в пару секунд вне матча.",
        "Invite Green Pixels": "Пиксели приглашения",
        "How much of the ACCEPT button's green has to be on screen before the bot reads the dialog. Raise it if invites are detected where there are none, lower it if real ones are missed.":
            "Сколько зелёного с кнопки ACCEPT должно быть на экране, чтобы бот стал читать диалог. Повысить, если приглашения находятся там, где их нет; понизить, если настоящие пропускаются.",
        "Centered Wall Detection": "Стены только вокруг игрока",
        "Use the close wall model on a 640x640 crop centered near the player.":
            "Искать стены в квадрате 640×640 вокруг игрока вместо всего экрана.",
        "Gadget Pixels": "Пиксели гаджета",
        "Green pixel threshold for gadget readiness.": "Сколько зелёных пикселей значит, что гаджет готов.",
        "Hypercharge Pixels": "Пиксели гиперзаряда",
        "Purple pixel threshold for hypercharge readiness.":
            "Сколько фиолетовых пикселей значит, что гиперзаряд готов.",
        "Super Pixels": "Пиксели супера",
        "Yellow pixel threshold for super readiness.": "Сколько жёлтых пикселей значит, что супер готов.",
        "Idle Pixel Threshold": "Порог простоя",
        "Amount of gray needed to consider the game idle.":
            "Сколько серого на экране считать простоем.",
        "Post-Max Hold Attack": "Удержание атаки после максимума",
        "Extra hold time after maxing hold-attack brawlers.":
            "Сколько ещё держать атаку у бойцов с зарядкой.",
        "Play Again On Win": "Играть снова после победы",
        "Chain another match immediately after a win.": "Сразу начинать следующий матч после победы.",

        // ---- debug settings
        "Verbose Debug": "Подробная отладка",
        "Enable extra runtime debugging output.": "Больше отладочного вывода во время работы.",
        "State Finder Debug": "Отладка распознавания экрана",
        "Enable state finder logging output.": "Логировать определение состояния экрана.",
        "Re-apply Movement": "Повторять движение",
        "Keep sending joystick movement even when the target position has not changed.":
            "Слать команду джойстику даже когда направление не менялось.",
        "Debug View": "Отладочное окно",
        "Show the latest bot frame in a separate low-latency window.":
            "Показывать последний кадр бота в отдельном окне.",
        "Debug View FPS": "Кадров в отладочном окне",
        "Maximum FPS for the debug window. Lower this if it costs too much performance.":
            "Предел кадров в отладочном окне. Снизь, если оно ест производительность.",
        "Advanced Debug Visuals": "Расширенная отрисовка",
        "Show hit circles, line-of-sight links, and joystick path sectors in the debug window.":
            "Рисовать хитбоксы, линии видимости и секторы джойстика.",
        "Record Debug Preview As Clips": "Записывать отладку роликами",
        "Save MP4 clips of the debug preview when the player is tracked and then lost.":
            "Сохранять MP4, когда игрок был найден и затем потерян.",

        // ---- timers
        "Gadget Delay": "Проверка гаджета",
        "How often Pyla checks gadgets.": "Как часто проверять гаджет.",
        "Hypercharge Delay": "Проверка гиперзаряда",
        "How often Pyla checks if hypercharge is available.": "Как часто проверять гиперзаряд.",
        "Super Delay": "Проверка супера",
        "How often Pyla checks if super is available.": "Как часто проверять супер.",
        "State Check": "Проверка состояния",
        "How often Pyla checks the game state.": "Как часто определять, что на экране.",
        "Idle Check": "Проверка простоя",
        "How often idle detection runs.": "Как часто проверять простой.",
        "Wall Detection": "Поиск стен",
        "Wall scan cadence.": "Как часто пересканировать стены.",
        "Proceed Delay": "Задержка продолжения",
        "Delay before pressing proceed when no detections are found.":
            "Через сколько жать «продолжить», если ничего не распознано.",
        "Crash Check": "Проверка падений",
        "How often crash recovery checks run.": "Как часто проверять, не упала ли игра.",

        // ---- webhook
        "Webhook URL": "Адрес вебхука",
        "Discord webhook endpoint used for notifications.": "Адрес вебхука Discord для уведомлений.",
        "Discord ID": "ID в Discord",
        "Your discord user ID. Required to use a discord bot or be pinged in webhooks.":
            "Твой ID пользователя Discord. Нужен для бота и упоминаний в вебхуках.",
        "Ping When Stuck": "Пинг при застревании",
        "Send a ping when Pyla gets stuck.": "Слать уведомление, если бот застрял.",
        "Ping On Target": "Пинг при достижении цели",
        "Send a ping when a target finishes.": "Слать уведомление, когда цель достигнута.",
        "Ping Every X Matches": "Пинг каждые N матчей",
        "0 disables periodic match pings.": "0 отключает уведомления по матчам.",
        "Ping Every X Minutes": "Пинг каждые N минут",
        "0 disables periodic minute pings.": "0 отключает уведомления по времени.",
        "Telegram Bot Token": "Токен бота Telegram",
        "Telegram bot token used for notifications.": "Токен телеграм-бота для уведомлений.",
        "Telegram Chat ID": "ID чата Telegram",
        "Telegram chat ID that should receive notifications.": "ID чата, куда слать уведомления.",
        "Discord Bot Token": "Токен бота Discord",
        "Discord bot token used for remote control commands. Requires full restart to apply.":
            "Токен бота Discord для команд управления. Применяется после полного перезапуска.",
        "Discord Guild ID": "ID сервера Discord",
        "Discord server ID where slash commands should be synced.":
            "ID сервера, куда синхронизировать команды.",

        // ---- player / API messages
        "API Token Required": "Нужен токен API",
        "Add a free Brawl Stars API token in Settings to sync live stats.":
            "Добавь бесплатный токен Brawl Stars API в настройках для живой статистики.",
        "Needs API token": "Нужен токен API",
        "Player tag is incorrect": "Неверный тег игрока",
        "Brawl Stars API problem": "Проблема с Brawl Stars API",
        "Use your Brawl Stars player tag, not your Supercell ID.":
            "Нужен тег игрока Brawl Stars, а не Supercell ID.",
        "Syncing player data...": "Синхронизация данных игрока...",
        "Checking player tag with the Brawl Stars API.": "Проверяем тег через Brawl Stars API.",
        "Enter a player tag to pull live trophies and streaks.":
            "Укажи тег игрока, чтобы подтягивать кубки и серии.",
        "IP address changed": "Адрес IP изменился",

        // ---- auth
        "Authentication Required": "Требуется вход", "API Key": "Ключ API",
        "Unlock UI": "Разблокировать", "Login successful.": "Вход выполнен.",
        "API key required": "Нужен ключ API", "API key not found": "Ключ API не найден",
        "Auth request expired": "Срок запроса истёк",
        "Auth server unreachable": "Сервер авторизации недоступен",
        "Auth server returned an invalid response": "Сервер авторизации ответил неверно",
        "Login failed locally": "Локальный вход не удался",
        "Login request failed": "Запрос входа не удался",
        "Saved key check failed": "Не удалось проверить сохранённый ключ",
        "Device ID missing": "Не передан ID устройства",
        "Device mismatch": "Устройство не совпадает",
        "Build signature missing": "Нет подписи сборки",
        "Build timestamp missing": "Нет отметки времени сборки",
        "Build timestamp invalid": "Неверная отметка времени сборки",
        "App build could not be verified": "Не удалось проверить сборку",
        "Version is too new for this key": "Версия новее, чем позволяет ключ",
        "Check your internet connection and try again.": "Проверь соединение и попробуй снова.",
        "Check that your system clock is correct, then try again.":
            "Проверь системные часы и попробуй снова.",
        "Try again. If it keeps happening, check the Python logs for the auth status code.":
            "Попробуй снова. Если повторяется — посмотри код ответа в логах Python.",
        "This usually means the local build and auth server secrets do not match.":
            "Обычно это значит, что секреты сборки и сервера не совпадают.",
        "Refresh your API key in Discord from this device.":
            "Обнови ключ API в Discord с этого устройства.",
        "Refresh your API key in Discord so it can bind to your current IP.":
            "Обнови ключ API в Discord, чтобы он привязался к текущему адресу.",
        "Refresh your API key in Discord or use a version allowed by this key.":
            "Обнови ключ API в Discord или возьми версию, разрешённую этим ключом.",
        "Early Access": "Ранний доступ", "Early Access Feature": "Функция раннего доступа",
        "Get Early Access": "Получить ранний доступ", "Maybe Later": "Позже",
        "Unlock Premium Features": "Открыть платные функции",
        "Community": "Сообщество",

        // ---- profile
        "Profile": "Профиль",
        "No matches recorded yet": "Матчей пока нет",
        "Everything here is worked out from the match history, so it fills in as the bot plays.":
            "Всё здесь считается из истории матчей и заполняется по мере игры.",
        "Win rate": "Побед",
        "Trophies": "Кубки",
        "Per match": "За матч",
        "Time played": "Наиграно",
        "Today": "Сегодня",
        "Right now": "Сейчас",
        "Per day": "В день",
        "Best brawler": "Лучший боец",
        "Most played": "Больше всего сыграно",
        "Recent form": "Последние матчи",
        "Newest first": "Свежие первыми",
        "Time of day": "Время суток",
        "When it plays, and how it goes": "Когда играет и с каким результатом",
        "Day of week": "День недели",
        "Across the week": "По дням недели",
        "Height is matches played, colour is win rate":
            "Высота — число матчей, цвет — процент побед",
        "Gamemodes": "Режимы",
        "brawlers": "бойцов",
        "playstyles": "стилей",
        "gamemodes": "режимов",

        // ---- play schedule, on the runtime panel
        "Schedule": "Расписание",
        "Pause at this time": "Ставить на паузу в",
        "Start again at": "Продолжать в",
        "Time of day, 24 hour": "Время суток, 24 часа",
        "Leave empty to stay paused": "Пусто — останется на паузе",
        "Close Brawl Stars when it stops": "Закрывать Brawl Stars при остановке",
        "Shut down the computer afterwards": "Выключать компьютер после этого",
        "The computer will power off 60 seconds after the bot finishes. Run 'shutdown /a' to cancel.":
            "Компьютер выключится через 60 секунд после завершения. Отменить — командой shutdown /a.",
        "Runs until you stop it": "Работает, пока не остановишь",
        "It finishes the current match first and then pauses, so the queue and your progress are kept. The overnight window may cross midnight - 23:30 to 08:00 works. Leave everything empty and it runs until you stop it yourself.":
            "Сначала доигрывает текущий матч и только потом встаёт на паузу, очередь и прогресс сохраняются. Ночное окно может переходить через полночь — 23:30 до 08:00 работает. Оставь всё пустым, и бот будет работать, пока не остановишь сам.",
        "Schedule saved. It applies the next time VvokAI starts.":
            "Расписание сохранено, начнёт действовать при следующем запуске.",
        "Schedule could not be saved.": "Не удалось сохранить расписание.",
        "Times are 24 hour. The window may cross midnight, so 23:30 to 08:00 works. It finishes the current match and pauses - the queue is kept. Leave everything empty to run until stopped.":
            "Время в 24-часовом формате. Окно может переходить через полночь, поэтому 23:30 — 08:00 работает. Бот доигрывает текущий матч и встаёт на паузу, очередь сохраняется. Оставь всё пустым, чтобы работал до остановки.",
        "Enter your Pyla API key": "Введи ключ Pyla API",
        "Pyla Early Access": "Ранний доступ Pyla",
        "Generate one in Discord with /generate_key using PylaBot.":
            "Сгенерируй его в Discord командой /generate_key у PylaBot.",
        "Generate a fresh key with /generate_key using PylaBot, then paste the full key here.":
            "Сгенерируй новый ключ командой /generate_key у PylaBot и вставь его целиком.",
        "The saved key could not be checked. Try again or generate a fresh key with /generate_key using PylaBot.":
            "Сохранённый ключ проверить не удалось. Попробуй снова или сгенерируй новый через /generate_key.",
        "The browser could not reach the local VvokAI web UI login endpoint.":
            "Браузер не достучался до локальной точки входа VvokAI.",
        "The local web UI hit an error while validating the key. Check the Python logs for the traceback.":
            "Локальный интерфейс упал при проверке ключа. Трассировка — в логах Python.",
        "The app could not build a complete auth request. Restart VvokAI and check the Python logs if it repeats.":
            "Не удалось собрать запрос авторизации. Перезапусти VvokAI, а если повторится — смотри логи Python.",
        "The app could not send this device ID. Restart VvokAI and check the Python logs if it repeats.":
            "Не удалось отправить ID устройства. Перезапусти VvokAI, а если повторится — смотри логи Python.",
        "The app could not sign the auth request. Restart VvokAI and check the Python logs if it repeats.":
            "Не удалось подписать запрос авторизации. Перезапусти VvokAI, а если повторится — смотри логи Python.",
    };

    const KEY = "vvok-lang";
    let lang = localStorage.getItem(KEY) || "en";

    // English original for every node touched, so switching back is exact
    // rather than a reload-and-hope.
    const originals = new WeakMap();
    let busy = false;

    // Lines that carry a number cannot be looked up whole - "1543 matches
    // played" is a different string every match. A handful of patterns covers
    // them without turning this into a template engine, and each one still
    // round-trips because the English original is kept on the node.
    const PATTERNS = [
        [/^(\d+) matches played$/, "Сыграно матчей: $1"],
        [/^Last (\d+) matches$/, "Последние $1 матчей"],
        [/^(\d+) brawlers$/, "Бойцов: $1"],
        [/^(\d+) playstyles$/, "Стилей игры: $1"],
        [/^(\d+) gamemodes$/, "Режимов: $1"],
        [/^(\d+) sessions \| (.+) at the controls$/, "Сессий: $1 | за игрой $2"],
        [/^(.+) to (.+) \| (\d+) active days \| (\d+) sessions$/,
         "$1 — $2 | дней активности: $3 | сессий: $4"],
        [/^(\d+) matches$/, "Матчей: $1"],
        [/^Stops at (.+), starts itself again at (.+)$/, "Остановка в $1, сам запустится в $2"],
        [/^Stops at (.+) and stays stopped until you start it$/,
         "Остановка в $1, дальше ждёт запуска вручную"],
        [/^(\d+)W \/ (\d+)L \/ (\d+)D$/, "$1 побед / $2 поражений / $3 ничьих"],
    ];

    function targetFor(text) {
        // Collapsed for matching only: markup indentation puts newlines and
        // runs of spaces inside otherwise ordinary sentences.
        const trimmed = text.trim().replace(/\s+/g, " ");
        if (!trimmed || KEEP.has(trimmed)) return null;

        const hit = RU[trimmed];
        if (hit) return text.replace(text.trim(), hit);

        for (const [pattern, replacement] of PATTERNS) {
            if (pattern.test(trimmed)) {
                return text.replace(text.trim(), trimmed.replace(pattern, replacement));
            }
        }
        return null;
    }

    function applyToNode(node) {
        if (node.parentElement && node.parentElement.closest("input, textarea, script, style")) {
            return;
        }
        if (lang === "ru") {
            if (originals.has(node)) return;      // already Russian
            const next = targetFor(node.nodeValue);
            if (next === null) return;
            originals.set(node, node.nodeValue);
            node.nodeValue = next;
        } else if (originals.has(node)) {
            node.nodeValue = originals.get(node);
            originals.delete(node);
        }
    }

    function apply(root) {
        if (busy) return;
        busy = true;
        try {
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            const nodes = [];
            for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);
            nodes.forEach(applyToNode);

            if (root.querySelectorAll) {
                root.querySelectorAll("[placeholder]").forEach((element) => {
                    if (lang === "ru") {
                        if (element.dataset.i18nEn !== undefined) return;
                        const next = targetFor(element.getAttribute("placeholder"));
                        if (next === null) return;
                        element.dataset.i18nEn = element.getAttribute("placeholder");
                        element.setAttribute("placeholder", next);
                    } else if (element.dataset.i18nEn !== undefined) {
                        element.setAttribute("placeholder", element.dataset.i18nEn);
                        delete element.dataset.i18nEn;
                    }
                });
            }
        } finally {
            busy = false;
        }
    }

    function addToggle() {
        const bar = document.querySelector(".header-actions");
        if (!bar || document.getElementById("langToggle")) return;
        const pill = document.createElement("span");
        pill.id = "langToggle";
        pill.className = "badge badge-outline";
        pill.style.cursor = "pointer";
        pill.textContent = lang === "ru" ? "RU" : "EN";
        pill.title = "Language / Язык";
        pill.addEventListener("click", () => {
            lang = lang === "ru" ? "en" : "ru";
            localStorage.setItem(KEY, lang);
            pill.textContent = lang === "ru" ? "RU" : "EN";
            apply(document.body);
        });
        bar.appendChild(pill);
    }

    function start() {
        addToggle();
        apply(document.body);
        // app.js replaces whole views with innerHTML and also rewrites single
        // labels in place, so both kinds of change have to be watched.
        new MutationObserver((records) => {
            if (busy) return;
            for (const record of records) {
                if (record.type === "characterData") {
                    applyToNode(record.target);
                    continue;
                }
                record.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) apply(node);
                    else if (node.nodeType === 3) applyToNode(node);
                });
            }
            addToggle();
        }).observe(document.body, { childList: true, subtree: true, characterData: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
