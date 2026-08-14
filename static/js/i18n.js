/* Russian for the interface chrome.
 *
 * Deliberately a translation pass over the finished DOM rather than a string
 * table threaded through every template. app.js builds its views by writing
 * innerHTML in about thirty places; wiring a lookup into each one would touch
 * all of them and go stale the moment any is edited. This walks text nodes and
 * placeholders, swaps the ones it recognises, and leaves everything else
 * exactly as written - so an untranslated string shows in English instead of
 * showing as a missing key.
 *
 * The choice is remembered in localStorage and applied again after every
 * re-render, which the observer below catches.
 */

(() => {
    "use strict";

    const RU = {
        // navigation
        "Dashboard": "Главная", "Brawlers": "Бойцы", "Playstyles": "Стили игры",
        "History": "История", "Settings": "Настройки",
        // status
        "Idle": "Простой", "Running": "Работает", "Paused": "Пауза",
        "Stopping": "Остановка", "Pausing": "Пауза...", "Offline": "Не в сети",
        "Local mode": "Локальный режим", "Live": "Вживую", "Ready": "Готов",
        // runtime
        "Runtime": "Запуск", "Start": "Старт", "Pause": "Пауза", "Stop": "Стоп",
        "Go to Brawlers": "Перейти к бойцам",
        "VvokAI is currently running": "VvokAI сейчас работает",
        "VvokAI is paused": "VvokAI на паузе",
        "VvokAI is pausing": "VvokAI встаёт на паузу",
        "Queue is ready. Start VvokAI from here.": "Очередь готова. Запускай отсюда.",
        "Resolve runtime state before starting.": "Сначала разберись с состоянием запуска.",
        "Add at least one brawler to the queue before starting.":
            "Добавь хотя бы одного бойца в очередь.",
        "Pyla is paused in the lobby. Press Start to resume.":
            "Бот на паузе в лобби. Нажми «Старт», чтобы продолжить.",
        // playstyle / community
        "Active Playstyle": "Активный стиль", "Browse": "Выбрать",
        "Community": "Сообщество", "No playstyle selected": "Стиль не выбран",
        // queue
        "Queue": "Очередь", "Open Brawlers": "Открыть бойцов",
        "Current Trophies": "Текущие кубки", "Target Trophies": "Цель по кубкам",
        // settings sections
        "General": "Общее", "Runtime and environment": "Запуск и окружение",
        "Bot": "Бот", "Debug": "Отладка", "Timers": "Таймеры",
        "Webhook": "Вебхук", "Reset to defaults": "Сбросить",
        // settings fields most people touch
        "Player Tag": "Тег игрока",
        "Brawl Stars API Token": "Токен Brawl Stars API",
        "Developer Portal Email": "Почта портала разработчика",
        "Developer Portal Password": "Пароль портала разработчика",
        "Default Trophy Target": "Цель по кубкам по умолчанию",
        "Run Time": "Время работы", "Max IPS": "Предел IPS",
        "Threads": "Потоки", "Emulator Port": "Порт эмулятора",
        "Package Name": "Имя пакета", "Debug View": "Отладочное окно",
        "Play Order": "Порядок игры",
        "Load Queue On Startup": "Загружать очередь при старте",
        // errors people actually see
        "Player tag is incorrect": "Неверный тег игрока",
        "Brawl Stars API problem": "Проблема с Brawl Stars API",
        "Manual mode": "Ручной режим",
        "Syncing player data...": "Синхронизация данных игрока...",
    };

    const KEY = "vvok-lang";
    let lang = localStorage.getItem(KEY) || "en";

    function swap(text) {
        const trimmed = text.trim();
        if (!trimmed) return null;
        const hit = RU[trimmed];
        if (!hit) return null;
        // Keep whatever spacing the markup had around it.
        return text.replace(trimmed, hit);
    }

    function translate(root) {
        if (lang !== "ru") return;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const edits = [];
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            // Never rewrite what the user is typing.
            if (node.parentElement && node.parentElement.closest("input, textarea")) continue;
            const next = swap(node.nodeValue);
            if (next !== null) edits.push([node, next]);
        }
        edits.forEach(([node, value]) => { node.nodeValue = value; });

        root.querySelectorAll("[placeholder]").forEach((element) => {
            const next = swap(element.getAttribute("placeholder"));
            if (next !== null) element.setAttribute("placeholder", next);
        });
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
            // Re-render from scratch: a pass can turn English into Russian but
            // not back, since the English original is gone by then.
            location.reload();
        });
        bar.appendChild(pill);
    }

    function start() {
        addToggle();
        translate(document.body);
        // app.js rebuilds whole views with innerHTML, so retranslate whenever
        // anything is replaced.
        new MutationObserver((records) => {
            for (const record of records) {
                record.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) translate(node);
                    else if (node.nodeType === 3) {
                        const next = swap(node.nodeValue);
                        if (next !== null) node.nodeValue = next;
                    }
                });
            }
            addToggle();
        }).observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
