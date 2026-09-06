const NAV_ITEMS = {
    dashboard: { label: "Dashboard", icon: "dashboard" },
    queue: { label: "Brawlers", icon: "queue" },
    playstyles: { label: "Playstyles", icon: "playstyles" },
    history: { label: "History", icon: "history" },
    profile: { label: "Profile", icon: "history" },
    logs: { label: "Logs", icon: "settings" },
    instances: { label: "Accounts", icon: "queue", supervisorOnly: true },
    settings: { label: "Settings", icon: "settings" },
};

const GAMEMODE_LABELS = {
    all: "All Gamemodes",
    brawlball: "Brawl Ball",
    basketbrawl: "Basket Brawl",
    brawlball_5v5: "Brawl Ball 5v5",
    showdown: "Showdown",
    other: "Other",
};

const AUTH_ERROR_COPY = {
    MISSING_API_KEY: {
        title: "API key required",
        detail: "Generate one in Discord with /generate_key using VvokBot.",
    },
    MISSING_HWID: {
        title: "Device ID missing",
        detail: "The app could not send this device ID. Restart VvokAI and check the Python logs if it repeats.",
    },
    MISSING_BUILD_TIMESTAMP: {
        title: "Build timestamp missing",
        detail: "The app could not build a complete auth request. Restart VvokAI and check the Python logs if it repeats.",
    },
    INVALID_BUILD_TIMESTAMP: {
        title: "Build timestamp invalid",
        detail: "Check that your system clock is correct, then try again.",
    },
    MISSING_BUILD_SIGNATURE: {
        title: "Build signature missing",
        detail: "The app could not sign the auth request. Restart VvokAI and check the Python logs if it repeats.",
    },
    INVALID_API_KEY: {
        title: "API key not found",
        detail: "Generate a fresh key with /generate_key using VvokBot, then paste the full key here.",
    },
    IP_MISMATCH: {
        title: "IP address changed",
        detail: "Refresh your API key in Discord so it can bind to your current IP.",
    },
    HWID_MISMATCH: {
        title: "Device mismatch",
        detail: "Refresh your API key in Discord from this device.",
    },
    VERSION_TOO_NEW: {
        title: "Version is too new for this key",
        detail: "Refresh your API key in Discord or use a version allowed by this key.",
    },
    INVALID_BUILD_SIGNATURE: {
        title: "App build could not be verified",
        detail: "This usually means the local build and auth server secrets do not match.",
    },
    SIGNATURE_EXPIRED: {
        title: "Auth request expired",
        detail: "Check that your system clock is correct, then try again.",
    },
    AUTH_SERVER_UNREACHABLE: {
        title: "Auth server unreachable",
        detail: "Check your internet connection and try again.",
    },
    INVALID_AUTH_RESPONSE: {
        title: "Auth server returned an invalid response",
        detail: "Try again. If it keeps happening, check the Python logs for the auth status code.",
    },
    LOGIN_CHECK_FAILED: {
        title: "Saved key check failed",
        detail: "The saved key could not be checked. Try again or generate a fresh key with /generate_key using VvokBot.",
    },
    LOGIN_FAILED: {
        title: "Login failed locally",
        detail: "The local web UI hit an error while validating the key. Check the Python logs for the traceback.",
    },
    LOGIN_REQUEST_FAILED: {
        title: "Login request failed",
        detail: "The browser could not reach the local VvokAI web UI login endpoint.",
    },
};

const INVALID_PLAYER_TAG_MESSAGE = "Player tag is incorrect. Use your Brawl Stars player tag, not your Supercell ID.";

const state = {
    bootstrap: null,
    currentView: "dashboard",
    selectedBrawler: "",
    queueTargetType: "trophies",
    brawlerSearch: "",
    playerInfo: { ok: true, player_tag: "", player_name: "", stats: {} },
    settingsTab: "general",
    historySearch: "",
    historySort: "matches",
    historyChartRange: "recent",
    historySignature: "",
    queueRenderDeferred: false,
    lastHistoryPoll: 0,
    playstyleSearch: "",
    playstyleFilter: "all",
    pendingSaves: {},
    playerTagTimer: null,
    playerTagLoading: false,
    runtimePollTimer: null,
    authSubmitting: false,
};

const SETTINGS_META = {
    general: [
        { key: "player_tag", label: "Player Tag", type: "text", placeholder: "#PLAYER", help: "Used to autofill live trophies and win streaks inside the brawler editor. Use your Brawl Stars player tag, not your Supercell ID." },
        { key: "brawl_api_token", label: "Brawl Stars API Token", type: "text", placeholder: "eyJ0eXAiOiJKV1Qi...", help: "Free from developer.brawlstars.com. Log in, open My Account, Create New Key. The key is tied to the one IP address you create it from and stops working when your provider changes it - ranges like 0.0.0.0/0 are refused, whatever you may have read. Fill in the two fields below and the bot will reissue the key by itself when that happens. Paste the whole key here. (Win streaks are not published by the API, so those stay as you set them.)" },
        { key: "brawl_api_email", label: "Developer Portal Email", type: "text", placeholder: "you@example.com", help: "Every key is tied to one IP address, so this is how trophy sync survives your provider changing it: the bot logs in, reissues the key for the new address and carries on. Without it, sync stops working the next time your address moves." },
        { key: "brawl_api_password", label: "Developer Portal Password", type: "password", help: "The same password you use on developer.brawlstars.com. Stored in cfg/general_config.toml on this machine, never shown back here and never written to logs - but it is a password in a plain file. Leave both fields empty if that bothers you; trophies then stop syncing whenever your address changes, and nothing else breaks." },
        { key: "default_trophy_target", label: "Default Trophy Target", type: "number", help: "Default trophy target used when adding a new brawler to the queue." },
        { key: "run_for_minutes", label: "Run Time", type: "number", suffix: "min", help: "How long VvokAI runs before cooldown logic takes over." },
        { key: "max_ips", label: "Max IPS", type: "text", help: "Processing cap. Use auto if you want VvokAI to manage it." },
        { key: "used_threads", label: "Threads", type: "text", help: "Worker thread count. Auto keeps the current behavior." },
        { key: "ocr_scale_down_factor", label: "OCR Scale", type: "number", step: "0.1", help: "Scale factor used before OCR work." },
        { key: "trophies_multiplier", label: "Trophies Multiplier", type: "number", help: "Useful for custom arenas or multiplier-based modes." },
        { key: "emulator_port", label: "Emulator Port", type: "number", help: "ADB port used for the emulator instance." },
        { key: "brawl_stars_package", label: "Package Name", type: "text", help: "Android package used when restarting Brawl Stars." },
        { key: "auto_load_queue_on_startup", label: "Load Queue On Startup", type: "checkbox", help: "Load the latest saved queue when the web UI starts." },
        { key: "auto_update", label: "Automatic Updates", type: "checkbox", help: "Check for a newer version on startup and install it. Turn this off to freeze the setup you have - an update cannot then change anything under you." },
    ],
    debug: [
        { key: "verbose_debug", label: "Verbose Debug", type: "checkbox", help: "Enable extra runtime debugging output." },
        { key: "state_finder_debug", label: "State Finder Debug", type: "checkbox", help: "Enable state finder logging output." },
        { key: "re_apply_movement", label: "Re-apply Movement", type: "checkbox", help: "Keep sending joystick movement even when the target position has not changed." },
        { key: "debug_view", label: "Debug View", type: "checkbox", help: "Show the latest bot frame in a separate low-latency window." },
        { key: "debug_view_fps", label: "Debug View FPS", type: "number", help: "Maximum FPS for the debug window. Lower this if it costs too much performance." },
        { key: "advanced_debug_visuals", label: "Advanced Debug Visuals", type: "checkbox", visibleIf: { key: "debug_view", value: true }, help: "Show hit circles, line-of-sight links, and joystick path sectors in the debug window." },
        { key: "record_debug_preview_clips", label: "Record Debug Preview As Clips", type: "checkbox", visibleIf: { key: "debug_view", value: true }, help: "Save MP4 clips of the debug preview when the player is tracked and then lost." },
    ],
    bot: [
        { key: "play_again_on_win", label: "Play Again On Win", type: "checkbox", help: "Chain another match immediately after a win." },
        { key: "minimum_movement_delay", label: "Minimum Movement Delay", type: "number", step: "0.1", help: "Lower bound between movement actions." },
        { key: "unstuck_movement_delay", label: "Unstuck Delay", type: "number", step: "0.1", help: "Delay before the unstuck routine fires." },
        { key: "unstuck_movement_hold_time", label: "Unstuck Hold Time", type: "number", step: "0.1", help: "How long the unstuck move is held." },
        { key: "perceived_tile_size", label: "Perceived Tile Size", type: "number", help: "Map tile size in pixels used by playstyle movement and wall-aware targeting." },
        { key: "attack_range_multiplier", label: "Attack Range Multiplier", type: "number", step: "0.05", help: "Scales every brawler's attack and super range. The built-in table is measured short, so the bot used to open fire at about half of its real reach; 1.35 puts it at about three quarters. Raise it to shoot from further out, lower it if shots start missing. 1.0 is the old behaviour." },
        { key: "centered_wall_detection", label: "Centered Wall Detection", type: "checkbox", help: "Use the close wall model on a 640x640 crop centered near the player." },
        { key: "wall_detection_confidence", label: "Wall Confidence", type: "number", step: "0.05", help: "Confidence threshold for wall detection." },
        { key: "entity_detection_confidence", label: "Entity Confidence", type: "number", step: "0.05", help: "Confidence threshold for player and enemy detections." },
        { key: "seconds_to_hold_attack_after_reaching_max", label: "Post-Max Hold Attack", type: "number", step: "0.1", help: "Extra hold time after maxing hold-attack brawlers." },
        { key: "decline_team_invites", label: "Decline Team Invites", type: "checkbox", help: "Turn down team invites and mute the sender for ten minutes, so a stream of invitations cannot interrupt the farm. Checked every couple of seconds while out of a match." },
        { key: "team_invite_green_minimum", label: "Invite Green Pixels", type: "number", help: "How much of the ACCEPT button's green has to be on screen before the bot reads the dialog. Raise it if invites are detected where there are none, lower it if real ones are missed." },
        { key: "idle_pixels_minimum", label: "Idle Pixel Threshold", type: "number", help: "Amount of gray needed to consider the game idle." },
        { key: "super_pixels_minimum", label: "Super Pixels", type: "number", help: "Yellow pixel threshold for super readiness." },
        { key: "gadget_pixels_minimum", label: "Gadget Pixels", type: "number", help: "Green pixel threshold for gadget readiness." },
        { key: "hypercharge_pixels_minimum", label: "Hypercharge Pixels", type: "number", help: "Purple pixel threshold for hypercharge readiness." },
    ],
    timers: [
        { key: "super", label: "Super Delay", min: 0.1, max: 10, step: 0.1, help: "How often VvokAI checks if super is available." },
        { key: "hypercharge", label: "Hypercharge Delay", min: 0.1, max: 10, step: 0.1, help: "How often VvokAI checks if hypercharge is available." },
        { key: "gadget", label: "Gadget Delay", min: 0.1, max: 10, step: 0.1, help: "How often VvokAI checks gadgets." },
        { key: "wall_detection", label: "Wall Detection", min: 0.1, max: 10, step: 0.1, help: "Wall scan cadence." },
        { key: "no_detection_proceed", label: "Proceed Delay", min: 0.1, max: 10, step: 0.1, help: "Delay before pressing proceed when no detections are found." },
        { key: "state_check", label: "State Check", min: 0.1, max: 10, step: 0.1, help: "How often VvokAI checks the game state." },
        { key: "idle", label: "Idle Check", min: 0.1, max: 10, step: 0.1, help: "How often idle detection runs." },
        { key: "check_if_brawl_stars_crashed", label: "Crash Check", min: 0.1, max: 10, step: 0.1, help: "How often crash recovery checks run." },
    ],
    webhook: [
        { key: "discord_id", label: "Discord ID", type: "text", help: "Your discord user ID. Required to use a discord bot or be pinged in webhooks." },
        { key: "webhook_url", label: "Webhook URL", type: "url", help: "Discord webhook endpoint used for notifications." },
        { key: "discord_bot_token", label: "Discord Bot Token", type: "password", help: "Discord bot token used for remote control commands. Requires full restart to apply." },
        { key: "ping_when_stuck", label: "Ping When Stuck", type: "checkbox", help: "Send a ping when VvokAI gets stuck." },
        { key: "ping_when_target_is_reached", label: "Ping On Target", type: "checkbox", help: "Send a ping when a target finishes." },
        { key: "ping_every_x_match", label: "Ping Every X Matches", type: "number", help: "0 disables periodic match pings." },
        { key: "ping_every_x_minutes", label: "Ping Every X Minutes", type: "number", help: "0 disables periodic minute pings." },
        { key: "discord_guild_id", label: "Discord Guild ID", type: "text", help: "Discord server ID where slash commands should be synced." },
        { key: "telegram_token", label: "Telegram Bot Token", type: "password", help: "Telegram bot token used for notifications and for remote control. Send /help in your chat with the bot to see the commands. Only one copy of the bot can use a token at a time." },
        { key: "telegram_chat_id", label: "Telegram Chat ID", type: "text", help: "Telegram chat ID that receives notifications. Commands are only accepted from this chat; anything sent from anywhere else is ignored." },
    ],
};

document.addEventListener("DOMContentLoaded", async () => {
    renderNav();
    bindShellEvents();

    try {
        await bootstrap();
    } catch (error) {
        showToast(error.message || "Unable to load the VvokAI UI.", "error");
    }
});

function renderNav() {
    const nav = document.querySelector(".nav-menu");
    if (!nav) return;

    const supervisor = state.bootstrap?.app?.is_supervisor;
    nav.innerHTML = Object.entries(NAV_ITEMS)
        .filter(([, item]) => !item.supervisorOnly || supervisor)
        .map(([view, item]) => `
        <button class="nav-item ${view === state.currentView ? "active" : ""}" data-view="${view}" aria-current="${view === state.currentView ? "page" : "false"}">
            <span class="nav-icon">${iconMarkup(item.icon)}</span>
            <span>${escapeHtml(item.label)}</span>
        </button>
    `).join("");
}

function bindShellEvents() {
    document.addEventListener("click", (event) => {
        const navButton = event.target.closest("[data-view]");
        if (navButton) {
            setView(navButton.dataset.view);
        }

        const needsToken = event.target.closest(".needs-api-token");
        if (needsToken) {
            event.preventDefault();
            event.stopPropagation();
            goToApiTokenSetting();
        }

        const startInst = event.target.closest("[data-instance-start]");
        if (startInst) instanceAction("start", startInst.dataset.instanceStart);
        const stopInst = event.target.closest("[data-instance-stop]");
        if (stopInst) instanceAction("stop", stopInst.dataset.instanceStop);
        const removeInst = event.target.closest("[data-instance-remove]");
        if (removeInst && window.confirm(`Remove account "${removeInst.dataset.instanceRemove}"? Its config folder is left on disk.`)) {
            instanceAction("remove", removeInst.dataset.instanceRemove);
        }

        const openInst = event.target.closest("[data-instance-open]");
        if (openInst) { state.instanceViewing = openInst.dataset.instanceOpen; renderInstances(); }
        const backInst = event.target.closest("[data-instance-back]");
        if (backInst) { state.instanceViewing = null; renderInstances(); }
    });

    document.getElementById("authForm")?.addEventListener("submit", handleLogin);
    bindTooltipEvents();
}

// Placed against the thing it describes, and always outside it.
//
// It used to be pinned to the cursor at clientY + 18 and then CLAMPED to
// innerHeight - 140. Anywhere near the bottom of the window that clamp pulled
// it back up on top of the element the pointer was on - and the queue strip is
// exactly there, so hovering a row covered that row's own delete button with a
// panel repeating what the row already said. It never blocked the click (the
// panel takes no pointer events) but you could not see what you were aiming at.
//
// So: measure the panel, then put it under the target if it fits and over it
// if it does not. Never clamped into the target's own band, and never moved by
// the cursor wandering about inside it.
const TOOLTIP_GAP = 10;
const TOOLTIP_MARGIN = 8;

function placeTooltip(tooltip, target) {
    if (!target.getBoundingClientRect) return;
    const anchor = target.getBoundingClientRect();

    // Measured with the panel already visible; a hidden element measures 0.
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;

    const below = anchor.bottom + TOOLTIP_GAP;
    const above = anchor.top - TOOLTIP_GAP - height;
    // Below by preference. Above only when below would run off the window, and
    // only when above has the room for it - otherwise below is still the better
    // of the two, because that is the direction the page scrolls.
    const fitsBelow = below + height <= window.innerHeight - TOOLTIP_MARGIN;
    const top = (fitsBelow || above < TOOLTIP_MARGIN) ? below : above;

    const centred = anchor.left + anchor.width / 2 - width / 2;
    const left = Math.max(TOOLTIP_MARGIN,
                          Math.min(centred, window.innerWidth - width - TOOLTIP_MARGIN));

    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
}

function bindTooltipEvents() {
    const tooltip = document.getElementById("tooltip");
    if (!tooltip) return;
    let shownFor = null;

    document.body.addEventListener("mouseover", (event) => {
        const target = event.target.closest("[data-tooltip]");
        if (!target) {
            tooltip.classList.add("hidden");
            shownFor = null;
            return;
        }
        // Moving between the children of one row is not a new tooltip, and
        // re-placing it on each of those makes it jump.
        if (target === shownFor) return;
        shownFor = target;

        tooltip.innerHTML = target.dataset.tooltip;
        tooltip.classList.remove("hidden");
        placeTooltip(tooltip, target);
    });

    document.body.addEventListener("mouseout", (event) => {
        if (!event.target.closest("[data-tooltip]")) {
            tooltip.classList.add("hidden");
            shownFor = null;
        }
    });
}

async function bootstrap() {
    const payload = await fetchJSON("/api/bootstrap");
    state.bootstrap = payload;
    // Seed the fingerprint from what just arrived, so the first poll does not
    // rebuild a grid that is already on screen and identical.
    state.historySignature = historySignature(payload.history);
    state.selectedBrawler = state.selectedBrawler || payload.queue[0]?.brawler || payload.brawlers[0]?.name || "";
    syncQueueFormState();

    updateChrome();
    renderAll();
    toggleAuthModal();
    startRuntimePolling();
    startInstancePolling();

    const playerTag = payload.settings.general.player_tag || "";
    if (playerTag) {
        const playerInfo = await fetchJSON(`/api/player-info?tag=${encodeURIComponent(playerTag)}`, {}, true);
        if (state.bootstrap !== payload) return;
        state.playerInfo = playerInfo?.ok
            ? playerInfo
            : { ok: false, player_tag: cleanPlayerTag(playerTag), player_name: "", stats: {},
                message: playerInfo?.message || INVALID_PLAYER_TAG_MESSAGE,
                code: playerInfo?.code || "INVALID_PLAYER_TAG" };
    }

    if (payload.app?.is_supervisor) {
        const instances = await fetchJSON("/api/instances", {}, true);
        if (state.bootstrap !== payload) return;
        state.instances = instances;
    }

    if (state.currentView === "queue") renderQueue();
    if (state.currentView === "instances") renderInstances();
}

// The parts that change while nothing else does: the rate, the pill, the
// indicator, and the line saying what the bot is doing. Split out of
// updateChrome because that one also rebuilds the navigation, and running it
// on every poll would replace the rail under the pointer once a second.
//
// This is why the IPS reading sat still until the page was reloaded:
// updateChrome was called only when the runtime STATE changed, and a bot that
// is running stays running.
function updateLiveChrome() {
    const runtime = state.bootstrap?.runtime;
    if (!runtime) return;

    pushIpsSample(Number(runtime.ips));

    const status = document.getElementById("sidebarStatus");
    if (status) status.textContent = runtimeLabel(runtime);

    const pill = document.getElementById("runtimeStatusPill");
    if (pill) {
        pill.textContent = runtimeLabel(runtime);
        pill.className = `badge ${runtimeBadgeClass(runtime)}`;
    }

    const indicator = document.getElementById("sidebarIndicator");
    if (indicator) {
        indicator.className = `status-indicator ${runtime.state === "error" ? "is-danger"
            : runtime.is_running ? "is-running" : "is-idle"}`;
    }

    const doing = document.querySelector("#view-dashboard .doing");
    if (doing) doing.textContent = runtime.activity || "";

    // A stream whose bot has stopped keeps showing its last frame for ever,
    // which is worse than showing nothing: it looks live. The poll knows the
    // bot stopped, so it closes the connection and says so.
    if (!runtime.is_running && liveViewShowing()) {
        stopLiveView(LIVE_STOPPED_HINT);
    }
}


function updateChrome() {
    const { app, auth, runtime } = state.bootstrap;
    const version = `${app.name} v${app.version}`;

    document.getElementById("sidebarVersion").textContent = version;
    updateLiveChrome();
    document.getElementById("authStatusPill").textContent = auth.required ? (auth.authenticated ? "Authenticated" : "Login required") : "Local mode";
    document.getElementById("authStatusPill").className = `badge ${auth.required && !auth.authenticated ? "danger" : "badge-outline"}`;

    renderNav();
}

function runtimeLabel(runtime) {
    if (runtime.state === "running") return "Running";
    if (runtime.state === "pausing") return "Pausing";
    if (runtime.state === "paused") return "Paused";
    if (runtime.state === "stopping") return "Stopping";
    if (runtime.state === "error") return "Error";
    return "Idle";
}

function runtimeBadgeClass(runtime) {
    if (runtime.state === "error") return "danger";
    if (runtime.state === "running") return "active";
    if (runtime.state === "pausing" || runtime.state === "paused") return "warning";
    if (runtime.state === "stopping") return "danger";
    return "badge-outline";
}

function toggleAuthModal() {
    const modal = document.getElementById("authModal");
    if (!modal) return;

    const auth = state.bootstrap?.auth || {};
    const shouldShow = Boolean(auth.required && !auth.authenticated);
    modal.classList.toggle("hidden", !shouldShow);

    if (shouldShow) {
        const instructions = document.getElementById("authInstructions");
        if (instructions) {
            if (!auth.early_access) {
                instructions.innerHTML = "<h1> This screen isn't supposed to appear as an api key is included. Check the logs.</h1>";
            } else {
                instructions.innerHTML = "Use <code>/generate_key</code> if you bought via Patreon, or <code>/refresh_key</code> if you bought a temporary key, with VvokBot in #commands, then paste the key here. Your key is handled by Python only and is not rendered back into the UI.";
            }
        }
        renderAuthMessage(auth, auth.code ? "error" : "info");
    } else {
        renderAuthMessage(null);
    }
}

async function handleLogin(event) {
    event.preventDefault();

    const input = document.getElementById("apiKeyInput");
    const button = document.getElementById("authSubmitBtn");

    state.authSubmitting = true;
    if (button) {
        button.disabled = true;
        button.classList.add("is-disabled");
        button.textContent = "Checking...";
    }
    renderAuthMessage({ message: "Checking your API key with the auth server." }, "info");

    let result;
    try {
        result = await fetchJSON("/api/login/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: input.value }),
        }, true);
    } catch (error) {
        result = {
            ok: false,
            authenticated: false,
            message: error.message || "Login request failed.",
            code: "LOGIN_REQUEST_FAILED",
        };
    } finally {
        state.authSubmitting = false;
        if (button) {
            button.disabled = false;
            button.classList.remove("is-disabled");
            button.textContent = "Unlock UI";
        }
    }

    if (!result.ok) {
        state.bootstrap.auth = {
            ...(state.bootstrap.auth || {}),
            authenticated: false,
            message: result.message || "Login failed.",
            code: result.code,
            detected_version: result.detected_version,
            max_version: result.max_version,
        };
        renderAuthMessage(result, "error");
        updateChrome();
        showToast(formatAuthToast(result), "error");
        return;
    }

    input.value = "";
    renderAuthMessage(null);
    showToast("Login successful.", "success");
    await bootstrap();
}

function setView(view) {
    if (!NAV_ITEMS[view]) return;
    if (view !== "dashboard" && liveViewShowing()) stopLiveView();
    state.currentView = view;
    const renders = {dashboard:renderDashboard, queue:renderQueue, playstyles:renderPlaystyles,
        history:renderHistory, profile:renderProfile, logs:renderLogs,
        instances:renderInstances, settings:renderSettings};
    renders[view]();
    renderNav();

    document.querySelectorAll(".view").forEach((section) => {
        section.classList.toggle("active", section.id === `view-${view}`);
    });

    document.getElementById("pageTitle").textContent = NAV_ITEMS[view].label;
    const scrollArea = document.querySelector(".views-wrapper");
    if (scrollArea) scrollArea.scrollTop = 0;
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
    renderQueueDock();
}

function renderAll() {
    renderAlerts();
    setView(state.currentView);
}

function renderAlerts() {
    const alerts = document.getElementById("alertStack");
    const warnings = state.bootstrap.app.warnings || [];
    alerts.innerHTML = warnings.map((warning) => `<div class="alert">${escapeHtml(warning)}</div>`).join("");
}

function renderRuntimeSchedule() {
    // Deliberately on the Runtime panel rather than in Settings. When the bot
    // may run is decided while starting it, so it belongs next to the button
    // that starts it - buried three tabs away it would never be found, which
    // is exactly what happened the first time.
    //
    // Two times and nothing else. There was a session-length cap as well; a
    // duration and a clock time answer the same question in different units,
    // and carrying both meant explaining which one wins.
    const bot = (state.bootstrap && state.bootstrap.settings
                 && state.bootstrap.settings.bot) || {};
    const stopAt = bot.stop_at || "";
    const resumeAt = bot.resume_at || "";
    const closeGame = bot.close_game_when_scheduled !== false;
    const shutdown = bot.shutdown_when_done === true;

    let summary = "Runs until you stop it";
    if (stopAt && resumeAt) {
        summary = `Stops at ${escapeHtml(stopAt)}, starts itself again at ${escapeHtml(resumeAt)}`;
    } else if (stopAt) {
        summary = `Stops at ${escapeHtml(stopAt)} and stays stopped until you start it`;
    }

    return `
        <details class="runtime-schedule ${stopAt ? "is-set" : ""}" ${stopAt ? "open" : ""}>
            <summary>
                <span class="sched-title">Schedule</span>
                <span class="sched-summary">${summary}</span>
            </summary>
            <div class="sched-panel">
            <div class="sched-fields">
                <label>
                    <span>Pause at this time</span>
                    <input type="text" id="schedStopAt" placeholder="23:30" value="${escapeHtml(stopAt)}">
                    <small>Time of day, 24 hour</small>
                </label>
                <label>
                    <span>Start again at</span>
                    <input type="text" id="schedResumeAt" placeholder="08:00" value="${escapeHtml(resumeAt)}">
                    <small>Leave empty to stay paused</small>
                </label>
            </div>
            <label class="sched-toggle">
                <input type="checkbox" id="schedCloseGame" ${closeGame ? "checked" : ""}>
                <span>Close Brawl Stars when it stops</span>
            </label>
            <label class="sched-toggle">
                <input type="checkbox" id="schedShutdown" ${shutdown ? "checked" : ""}>
                <span>Shut down the computer afterwards</span>
            </label>
            <p class="sched-help">It finishes the current match first, then stops -
            a full stop rather than a pause, because a paused bot treats a closed
            game as a crash and reopens it. Trophies and the queue are saved. The
            window may cross midnight, so 23:30 to 08:00 works. Leave both empty
            and it runs until you stop it yourself.</p>
            <button type="button" class="btn sched-close">Done</button>
            </div>
        </details>`;
}


function renderDashboard() {
    const view = document.getElementById("view-dashboard");
    const { links, queue, runtime, auth } = state.bootstrap;
    const activePlaystyle = getActivePlaystyle();
    const canStart = queue.length > 0 && !["running", "pausing", "stopping"].includes(runtime.state) && !(auth.required && !auth.authenticated);
    const isPaused = runtime.state === "paused";
    const authBlockCopy = auth.required && !auth.authenticated
        ? formatAuthToast(auth) || auth.message || "Login required before starting."
        : "";
    const statusCopy = runtime.state === "error"
        ? (runtime.last_error || "VvokAI stopped with an error.")
        : runtime.state === "pausing"
            ? "Pause requested. VvokAI will stop in the lobby."
            : runtime.state === "stopping"
                ? "VvokAI is shutting down. This should only take a few seconds."
                : runtime.state === "running"
                    ? "Session is running. Pause takes effect in the lobby."
                : isPaused
                    ? "VvokAI is paused in the lobby. Press Start to resume."
                    : canStart
                        ? "Queue is ready. Start VvokAI from here."
                        : authBlockCopy
                            ? authBlockCopy
                            : queue.length
                                ? "Resolve runtime state before starting."
                            : "Add at least one brawler to the queue before starting.";

    let runtimePanel = `
        <button id="startRuntimeBtn" class="btn btn-primary btn-huge ${canStart ? "" : "is-disabled"}" ${canStart ? "" : "disabled"}>
            ${iconMarkup("play")}
            <span>Start</span>
        </button>
        <p class="runtime-note ${runtime.state === "error" ? "runtime-error" : ""}">${escapeHtml(statusCopy)}</p>
        ${!queue.length ? '<button data-open-brawlers class="btn" style="margin-top: 12px;">Go to Brawlers</button>' : ''}
        ${renderRuntimeSchedule()}
    `;

    if (["running", "pausing"].includes(runtime.state)) {
        runtimePanel = `
            <button id="pauseRuntimeBtn" class="btn btn-primary ${runtime.state === "pausing" ? "is-disabled" : ""}">${iconMarkup("pause")} Pause</button>
            <button id="stopRuntimeBtn" class="btn">${iconMarkup("stop")} Stop</button>
            <p class="runtime-note">${escapeHtml(statusCopy)}</p>
            ${renderRuntimeSchedule()}
        `;
    } else if (isPaused) {
        runtimePanel = `
            <button id="resumeRuntimeBtn" class="btn btn-primary">${iconMarkup("play")} Start</button>
            <button id="stopRuntimeBtn" class="btn">${iconMarkup("stop")} Stop</button>
            <p class="runtime-note">${escapeHtml(statusCopy)}</p>
            ${renderRuntimeSchedule()}
        `;
    }

    const stats = state.bootstrap.history?.summary || {};
    const measure = (label, value, live) => `
        <div class="measure">
            <div class="measure-label">${escapeHtml(label)}</div>
            <div class="measure-value${live ? " is-live" : ""}">${escapeHtml(String(value))}</div>
        </div>`;

    const current = queue[0];

    // The layout is the design: a command band across the top, a strip of
    // measurements under it, then the two things you actually read - what it
    // is playing, and what is left to play. No cards, no boxes; the rules
    // between regions do that work.
    view.innerHTML = `
        <div class="sheet">
            <div class="command-band">
                <div class="command-state">
                    <div class="session-kicker"><span>01 / SESSION</span><span class="studio-edition">PLAY. REPEAT.</span></div>
                    ${current ? `<div class="session-art" aria-hidden="true"><span class="art-orbit"></span><img src="${escapeHtml(current.icon_url)}" alt=""></div>` : ""}
                    <div class="command-title">${escapeHtml(runtimeLabel(runtime))}</div>
                    <div class="command-sub">${queue.length} ${queue.length === 1 ? "brawler" : "brawlers"} queued${runtime.activity ? ` <span class="doing">${escapeHtml(runtime.activity)}</span>` : ""}</div>
                </div>
                <div class="session-current">
                    ${current ? `<img src="${escapeHtml(current.icon_url)}" alt=""><div><span class="eyebrow">Current brawler</span><strong>${escapeHtml(current.brawler)}</strong></div><div class="session-target"><strong>${escapeHtml(String(current.type === "wins" ? current.wins || 0 : current.trophies || 0))}</strong><span>/ ${escapeHtml(String(current.push_until || 0))} ${current.type === "wins" ? "wins" : "trophies"}</span></div>` : `<p class="session-empty">Build your queue. Start your session.</p>`}
                </div>
                <div class="command-actions">${runtimePanel}</div>
            </div>

            <div class="live-view" id="liveView">
                <div class="live-head">
                    <p class="eyebrow">Live</p>
                    <button id="liveToggleBtn" class="btn btn-quiet">Watch</button>
                </div>
                <div class="live-body" id="liveBody">
                    <div class="live-standby" aria-hidden="true"><span></span><span></span><svg viewBox="0 0 64 64"><path d="M24 18L46 32 24 46Z" fill="currentColor"/></svg></div><p class="live-hint">See what the bot sees, with everything it
                    has found drawn on top. Works from anywhere the panel opens,
                    and only runs while you are looking at it.</p>
                </div>
            </div>

            <div class="measure-strip">
                ${measure("Matches", stats.total_matches ?? 0)}
                ${measure("Won", stats.wins ?? 0)}
                ${measure("Win rate", stats.win_rate != null ? stats.win_rate + "%" : "—", true)}
                ${measure("Queue", queue.length)}
            </div>

            <div class="sheet-cols">
                <section class="sheet-col sheet-col-left">
                    <p class="eyebrow">Active playstyle</p>
                    <h3 class="panel-title">${escapeHtml(activePlaystyle?.name || "No playstyle selected")}</h3>
                    <p class="meta">${escapeHtml(metaLine(activePlaystyle))}</p>
                    <p class="desc">${escapeHtml(activePlaystyle?.description || "Select a playstyle to surface its brawlers and gamemodes here.")}</p>
                    <button id="browsePlaystylesBtn" class="btn sheet-browse">Browse playstyles</button>

                    <div class="spec-list">
                        <div class="spec-row"><span>Dodging</span><span class="spec-value${runtime.state === "running" ? " is-live" : ""}">${runtime.state === "running" ? "RUNNING" : "IDLE"}</span></div>
                        <div class="spec-row"><span>Telegram</span><span class="spec-value"><a href="https://t.me/nyavke" target="_blank" rel="noreferrer">@nyavke</a></span></div>
                    </div>
                </section>

                <section class="sheet-col sheet-col-right">
                    <div class="sheet-col-head">
                        <p class="eyebrow">Queue</p>
                        <button id="goToBrawlersBtn" class="btn btn-quiet">Edit</button>
                    </div>
                    ${renderSheetQueue(queue)}
                </section>
            </div>
        </div>
    `;

    document.getElementById("liveToggleBtn")?.addEventListener("click", toggleLiveView);
    document.getElementById("browsePlaystylesBtn")?.addEventListener("click", () => setView("playstyles"));
    document.getElementById("goToBrawlersBtn")?.addEventListener("click", () => setView("queue"));
    view.querySelector("[data-open-brawlers]")?.addEventListener("click", () => setView("queue"));
    bindRuntimeButtons();
    bindScheduleDismiss();
}

let scheduleDismissBound = false;
function bindScheduleDismiss() {
    if (scheduleDismissBound) return;
    scheduleDismissBound = true;
    document.addEventListener("click", (event) => {
        const panel = document.querySelector(".runtime-schedule[open]");
        if (panel && (!panel.contains(event.target) || event.target.closest(".sched-close"))) {
            panel.removeAttribute("open");
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") document.querySelector(".runtime-schedule[open]")?.removeAttribute("open");
    });
}

function renderSheetQueue(queue) {
    if (!queue.length) {
        return `<p class="sheet-empty">Nothing queued. Add brawlers from the Brawlers tab.</p>`;
    }

    return `<div class="sheet-queue">${queue.slice(0, 9).map((item, index) => {
        const type = item.type === "wins" ? "wins" : "trophies";
        const current = Number(type === "wins" ? item.wins : item.trophies) || 0;
        const target = Number(item.push_until) || 0;
        // Progress from where it started is unknowable, so the bar shows how
        // much of the target is already banked - which is what people read it
        // as anyway.
        const done = target > 0 ? Math.max(0, Math.min(100, (current / target) * 100)) : 0;
        return `
            <div class="sq-row${index === 0 ? " is-current" : ""}">
                <span class="sq-index">${String(index + 1).padStart(2, "0")}</span>
                <img loading="lazy" decoding="async" class="sq-img" src="${escapeHtml(item.icon_url)}" alt="${escapeHtml(item.brawler)}">
                <span class="sq-name">${escapeHtml(item.brawler)}</span>
                <span class="sq-now">${current}</span>
                <span class="sq-target">${target}</span>
                <span class="sq-bar"><span style="width: ${done.toFixed(1)}%"></span></span>
            </div>`;
    }).join("")}</div>
    ${queue.length > 9 ? `<p class="sheet-more">${queue.length - 9} more</p>` : ""}`;
}

function renderSupportLink(url, title, subtitle = "", icon = "link") {
    // Inline SVG rather than an <img>. The previous version took a link OBJECT
    // and read link.url / link.icon_url from it; after the rebrand it was being
    // called with a plain URL string, so both came back undefined - a dead href
    // and a broken-image placeholder in every community row. Drawing the marks
    // here also means they inherit the theme instead of shipping as bitmaps
    // that have to be desaturated to fit.
    return `
        <a class="hero-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">
            <span class="hero-link-icon">${iconMarkup(icon)}</span>
            <div>
                <h4>${escapeHtml(title)}</h4>
                <span>${escapeHtml(subtitle)}</span>
            </div>
        </a>
    `;
}

function cleanPlayerTag(value) {
    return String(value || "").trim().replace(/^%23/i, "").replaceAll("#", "").trim();
}

// An emptied box stays empty. Both of these used to answer "#" for no input,
// and the first is bound to the field's own input event - so deleting the last
// character put the prefix straight back and the tag could not be cleared at
// all. What got saved was a lone "#", which is not falsy, so the bot went on
// believing a tag was set and asked the API about a player with no name.
function formatPlayerTagInput(value) {
    const cleanTag = cleanPlayerTag(value);
    return cleanTag ? `#${cleanTag}` : "";
}

function ensurePlayerTagPrefix(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    return text.startsWith("#") ? text : `#${cleanPlayerTag(text)}`;
}

function formatSettingValue(field, value) {
    if (field.key === "player_tag") {
        return formatPlayerTagInput(value);
    }
    return value ?? "";
}

function getPlayerPillState() {
    if (!state.bootstrap?.auth?.player_api) {
        return {
            className: "early-access-locked",
            title: "API Token Required",
            detail: "Add a free Brawl Stars API token in Settings to sync live stats.",
        };
    }

    if (state.playerTagLoading) {
        return {
            className: "is-loading",
            title: "Syncing player data...",
            detail: "Checking player tag with the Brawl Stars API.",
        };
    }

    const cleanTag = cleanPlayerTag(state.playerInfo.player_tag || state.bootstrap.settings.general.player_tag || "");
    if (state.playerInfo.ok === false && cleanTag) {
        // The server says what actually failed. Showing a fixed "wrong tag"
        // here sent people to check a tag that was fine while the real cause -
        // an IP-locked API token and a new address - went unmentioned.
        const tagLooksWrong = state.playerInfo.code === "INVALID_PLAYER_TAG";
        return {
            className: "has-error",
            title: tagLooksWrong ? "Player tag is incorrect" : "Brawl Stars API problem",
            detail: state.playerInfo.message || INVALID_PLAYER_TAG_MESSAGE,
        };
    }
    if (state.playerInfo.player_name) {
        return {
            className: "has-player",
            title: state.playerInfo.player_name,
            detail: `#${cleanTag}`,
        };
    }
    return {
        className: "",
        title: "Manual mode",
        detail: "Enter a player tag to pull live trophies and streaks.",
    };
}

function renderQueue(force = false) {
    const view = document.getElementById("view-queue");

    // Never rebuild this while someone is filling it in.
    //
    // renderQueue replaces the whole view, editor included, and seventeen
    // places call it - the running-queue poll every 1.2 s, the player-info
    // fetch, the tag debounce. So a win streak typed into the box was thrown
    // away before Save could be pressed, which reads exactly like "I add them
    // and it removes them". Callers that must redraw - after a save, after
    // picking a different brawler - pass force.
    if (!force) {
        const focused = document.activeElement;
        if (focused && view.contains(focused)
            && ["INPUT", "SELECT", "TEXTAREA"].includes(focused.tagName)) {
            state.queueRenderDeferred = true;
            return;
        }
    }
    state.queueRenderDeferred = false;
    const selectedBrawler = state.selectedBrawler || state.bootstrap.brawlers[0]?.name || "";
    const selectedCard = state.bootstrap.brawlers.find((item) => item.name === selectedBrawler);
    const hasValidPlayerInfo = Boolean(state.playerInfo.player_tag && Object.keys(state.playerInfo.stats || {}).length);
    const playerPill = getPlayerPillState();
    const defaultTarget = Number(state.bootstrap.settings.general.default_trophy_target || 1000);
    const playOrder = state.bootstrap.settings.general.play_order || "in_order";
    const pushAllButton = !state.bootstrap?.auth?.player_api
        ? `<button id="pushAllQueueLockedBtn" class="btn btn-locked needs-api-token" type="button" title="Needs a free Brawl Stars API token">${iconMarkup("queue")} Push All to ${defaultTarget}</button>`
        : hasValidPlayerInfo
            ? `<button id="pushAllQueueBtn" class="btn" type="button">${iconMarkup("queue")} Push All to ${defaultTarget}</button>`
            : "";

    view.innerHTML = `
        <div class="brawlers-layout">
            <section class="panel">
                <div class="panel-header">
                    <div>
                        <p class="eyebrow">02 / LINEUP</p>
                        <h3 class="panel-title lineup-title">Pick your<br>next player.</h3>
                    </div>
                    <div class="player-pill ${playerPill.className}">
                        ${playerPill.className === "is-loading" ? '<div class="player-pill-spinner"></div>' : ''}
                        <strong>${escapeHtml(playerPill.title)}</strong>
                        ${playerPill.className === "has-error" ? `<details class="player-api-detail"><summary>Details</summary><p>${escapeHtml(playerPill.detail)}</p></details>` : `<span>${escapeHtml(playerPill.detail)}</span>`}
                    </div>
                </div>

                <div class="queue-toolbar">
                    <div class="queue-toolbar-fields">
                        <label class="input-group grow">
                            <span>Search Brawlers</span>
                            <input id="brawlerSearch" type="search" placeholder="Search by brawler name" value="${escapeHtml(state.brawlerSearch)}">
                        </label>
                        <label class="input-group ${!state.bootstrap?.auth?.player_api ? "disabled-early-access" : ""}">
                            <span>Player Tag ${!state.bootstrap?.auth?.player_api ? `<span class="ea-badge">Needs API token</span>` : ""}</span>
                            <input id="playerTagInput" type="text" placeholder="${!state.bootstrap?.auth?.player_api ? "Add a Brawl Stars API token in Settings" : "#PLAYER"}" value="${!state.bootstrap?.auth?.player_api ? "" : escapeHtml(formatPlayerTagInput(state.bootstrap.settings.general.player_tag || ""))}" ${!state.bootstrap?.auth?.player_api ? "disabled" : ""}>
                        </label>
                    </div>
                    <div class="queue-toolbar-bottom">
                        <div class="toolbar-actions queue-load-actions">
                            <button id="loadQueueBtn" class="btn" type="button">${iconMarkup("import")} Load Queue</button>
                            ${pushAllButton}
                            <input id="queueFileInput" type="file" accept=".json,application/json" class="hidden">
                        </div>
                        <label class="input-group play-order-control">
                            <span>Play Order</span>
                            <select id="playOrderSelect" data-setting-section="general" data-setting-key="play_order">
                                <option value="in_order" ${playOrder === "in_order" ? "selected" : ""}>In Order</option>
                                <option value="lowest_to_highest" ${playOrder === "lowest_to_highest" ? "selected" : ""}>Lowest to Highest</option>
                                <option value="highest_to_lowest" ${playOrder === "highest_to_lowest" ? "selected" : ""}>Highest to Lowest</option>
                            </select>
                        </label>
                    </div>
                </div>

                <div id="brawlerGrid" class="grid-select">
                    ${renderBrawlerCards()}
                </div>
            </section>

            <section class="panel">
                ${selectedCard ? renderSelectedBrawlerEditor(selectedCard) : `<div class="empty-state">Choose a brawler to configure it.</div>`}
            </section>
        </div>
    `;

    bindQueueEvents();
}

function renderBrawlerCards() {
    const query = state.brawlerSearch.trim().toLowerCase();
    const filtered = state.bootstrap.brawlers.filter((item) => item.name.toLowerCase().includes(query));

    if (!filtered.length) {
        return `<div class="empty-state wide-empty">No brawlers match the current search.</div>`;
    }

    return filtered.map((item) => `
        <button class="b-cell ${item.name === state.selectedBrawler ? "active" : ""}" data-brawler="${escapeHtml(item.name)}">
            <img loading="lazy" decoding="async" src="${escapeHtml(item.icon_url)}" alt="${escapeHtml(item.name)}">
            <span>${escapeHtml(item.name)}</span>
        </button>
    `).join("");
}

function renderSelectedBrawlerEditor(brawler) {
    const liveStats = getLiveBrawlerStats(brawler.name);
    const existing = findExistingQueueItem(brawler.name);
    const currentType = state.queueTargetType;
    const currentTrophies = liveStats.trophies ?? existing?.trophies ?? 0;
    const currentWinStreak = liveStats.win_streak ?? existing?.win_streak ?? 0;
    const currentWins = existing?.wins ?? 0;
    const configuredDefaultTarget = Number(state.bootstrap.settings.general.default_trophy_target || 1000);
    const defaultTarget = currentType === "wins" ? Math.max(currentWins + 10, 25) : configuredDefaultTarget;
    const autoPickDefault = existing ? Boolean(existing.automatically_pick) : state.bootstrap.queue.length > 0;

    return `
        <div class="queue-editor">
            <div class="selected-brawler-top">
                <img class="brawler-detail-art" src="${escapeHtml(brawler.icon_url)}" alt="${escapeHtml(brawler.name)}">
                <div>
                    <p class="eyebrow">Selected Brawler</p>
                    <h3 class="panel-title">${escapeHtml(brawler.name)}</h3>
                    <p class="meta-line">${state.playerInfo.player_name ? `Live values synced from ${escapeHtml(state.playerInfo.player_name)}` : "Manual values are available if you do not use a player tag."}</p>
                </div>
            </div>

            <div class="seg-control">
                <button class="seg-btn ${currentType === "trophies" ? "active" : ""}" data-target-type="trophies">Target Trophies</button>
                <button class="seg-btn ${currentType === "wins" ? "active" : ""}" data-target-type="wins">Target Wins</button>
            </div>

            <div class="editor-fields">
                <label class="input-group">
                    <span>Target Amount</span>
                    <input id="queuePushUntil" type="number" min="0" value="${existing?.push_until ?? defaultTarget}">
                </label>

                ${currentType === "trophies" ? `
                    <label class="input-group">
                        <span>Current Trophies</span>
                        <input id="queueTrophies" type="number" min="0" value="${currentTrophies}">
                    </label>
                    <label class="input-group">
                        <span>Current Win Streak</span>
                        <input id="queueWinStreak" type="number" min="0" value="${currentWinStreak}">
                    </label>
                ` : `
                    <label class="input-group">
                        <span>Current Wins</span>
                        <input id="queueWins" type="number" min="0" value="${currentWins}">
                    </label>
                `}
            </div>

            <label class="check-card">
                <input id="queueAutoPick" type="checkbox" ${autoPickDefault ? "checked" : ""}>
                <span class="check-box"></span>
                <span class="check-info">
                    <strong>Automatically pick this brawler</strong>
                    <span>Enabled by default once you already have another brawler queued ahead of it.</span>
                </span>
            </label>

            <button id="saveQueueItemBtn" class="btn btn-primary w-full">${existing ? "Update Queue Entry" : "Add To Queue"}</button>
        </div>
    `;
}

function renderPlaystyles() {
    const view = document.getElementById("view-playstyles");
    const active = getActivePlaystyle();

    view.innerHTML = `
        <div class="ps-page">
            <section class="panel panel-accent playstyle-selected-shell">
                <div class="playstyle-selected-head">
                    <p class="eyebrow">03 / IN PLAY</p>
                </div>
                <div class="playstyle-selected-card-wrap">
                    ${renderPlaystyleShowcaseCard(active, true)}
                </div>
            </section>

            <section class="toolbar-strip">
                <div class="tb-search grow">
                    <input id="playstyleSearch" type="search" placeholder="Search by playstyle, brawler, or gamemode" value="${escapeHtml(state.playstyleSearch)}">
                </div>
                <div class="toolbar-actions">
                    <button id="importPlaystyleBtn" class="btn">${iconMarkup("import")} Import</button>
                    <input id="playstyleFileInput" type="file" accept=".vvok,.pyla" class="hidden">
                </div>
            </section>

            <section class="ps-lib-wrap">
                <div class="library-heading"><p class="ps-lib-title">The playbook.</p><span class="eyebrow">Choose your approach</span></div>
                <div class="ps-library">
                    ${renderPlaystyleLibrary(active)}
                </div>
            </section>
        </div>
    `;

    bindPlaystyleEvents();
}

function renderPlaystyleLibrary(active = getActivePlaystyle()) {
    const filtered = (state.bootstrap.playstyles.items || []).filter((item) => {
        if (active && item.filename === active.filename) return false;
        return matchesPlaystyleFilters(item);
    });

    return filtered.length
        ? filtered.map((item, index) => renderPlaystyleCard(item, index)).join("")
        : `<div class="empty-state wide-empty">No playstyles match the current search or filter.</div>`;
}

function renderPlaystyleCard(item, index = 0) {
    return `
        <article class="ps-card" data-activate-playstyle="${escapeHtml(item.filename)}">
            <div class="tactic-edition" aria-hidden="true"><span>VVOK / PLAYBOOK</span><strong>${String(index + 1).padStart(2, "0")}</strong></div>
            <button class="ps-delete-btn" data-delete-playstyle="${escapeHtml(item.filename)}" aria-label="Delete ${escapeHtml(item.name)}" title="Delete ${escapeHtml(item.name)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 10v7M14 10v7"/></svg></button>
            ${renderPlaystyleShowcaseCard(item, false)}
        </article>
    `;
}

function renderPlaystyleShowcaseCard(playstyle, large = false) {
    if (!playstyle) {
        return `
            <div class="playstyle-showcase ${large ? "selected" : ""}">
                <div class="playstyle-showcase-head">
                    <h4>No playstyle selected</h4>
                    <span>No metadata</span>
                </div>
                <div class="ps-vis ${large ? "large" : ""}">
                    <div class="ps-univ">No playstyle selected</div>
                </div>
            </div>
        `;
    }

    return `
        <div class="playstyle-showcase ${large ? "selected" : ""}">
            <div class="playstyle-showcase-head">
                <h4>${escapeHtml(playstyle.name)}</h4>
                <span>${escapeHtml(metaLine(playstyle))}</span>
                <p class="playstyle-card-description">${escapeHtml(playstyle.description || "No description provided.")}</p>
            </div>
            ${renderPlaystyleVisual(playstyle, large)}
        </div>
    `;
}

function renderPlaystyleVisual(playstyle, large = false) {
    if (!playstyle) {
        return `<div class="ps-vis ${large ? "large" : ""}"><div class="ps-univ">No playstyle selected</div></div>`;
    }

    const brawlers = playstyle.brawlers || [];
    const gamemodes = playstyle.gamemodes || [];
    const showBrawlers = brawlers.length > 0 && !brawlers.includes("all");
    const showGamemodes = gamemodes.length > 0 && !gamemodes.includes("all");

    if (!showBrawlers && !showGamemodes) {
        return `<div class="ps-vis ${large ? "large" : ""}"><div class="ps-univ">Universal</div></div>`;
    }

    return `
        <div class="ps-vis ${large ? "large" : ""}">
            ${showBrawlers ? `<div class="ps-part">${renderPlaystyleBrawlerThumbs(brawlers, large)}</div>` : ""}
            ${showBrawlers && showGamemodes ? `<div class="ps-div"></div>` : ""}
            ${showGamemodes ? `<div class="ps-part">${renderPlaystyleGamemodePills(gamemodes)}</div>` : ""}
        </div>
    `;
}

function renderPlaystyleBrawlerThumbs(brawlers, large) {
    return brawlers.slice(0, 6).map((name) => {
        const entry = state.bootstrap.brawlers.find((item) => item.name.toLowerCase() === String(name).toLowerCase());
        if (!entry) {
            return `<div class="ps-m-pill">${escapeHtml(String(name))}</div>`;
        }

        return `<img class="ps-b-img ${large ? "large" : ""}" src="${escapeHtml(entry.icon_url)}" alt="${escapeHtml(entry.name)}">`;
    }).join("");
}

function renderPlaystyleGamemodePills(gamemodes) {
    return gamemodes.slice(0, 4).map((mode) => `<span class="ps-m-pill">${escapeHtml(GAMEMODE_LABELS[mode] || String(mode))}</span>`).join("");
}

function profileTile(label, value, note = "", tone = "") {
    return `
        <div class="profile-tile ${tone}">
            <span class="profile-label">${escapeHtml(label)}</span>
            <strong class="profile-value">${escapeHtml(String(value))}</strong>
            ${note ? `<span class="profile-note">${escapeHtml(note)}</span>` : ""}
        </div>`;
}


function profileDuration(minutes) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return hours ? `${hours}h ${rest}m` : `${rest}m`;
}


function profileSigned(value) {
    return value > 0 ? `+${value}` : String(value);
}


function profileDate(value) {
    if (!value) return "never";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "never";
    return parsed.toLocaleDateString(undefined, {
        day: "numeric", month: "short", year: "numeric",
    });
}


// A table with a bar behind the numbers. The bar is proportional to matches
// played, so the rows somebody actually has evidence for are the ones that
// stand out - a 100% win rate over two games should not look like a result.
function profileTable(title, rows, subject) {
    if (!rows || !rows.length) return "";
    const most = Math.max(...rows.map(r => r.matches), 1);
    const body = rows.slice(0, 12).map(row => `
        <tr>
            <td class="pt-name">
                <span class="pt-bar" style="width:${(row.matches / most) * 100}%"></span>
                <span class="pt-label">${escapeHtml(row.name)}</span>
            </td>
            <td>${row.matches}</td>
            <td>${formatPercent(row.win_rate)}</td>
            <td class="${row.net >= 0 ? "pt-up" : "pt-down"}">${profileSigned(row.net)}</td>
            <td>${row.net_per_match >= 0 ? "+" : ""}${row.net_per_match}</td>
        </tr>`).join("");

    return `
        <section class="panel">
            <div class="panel-header">
                <div>
                    <p class="eyebrow">${escapeHtml(title)}</p>
                    <h3 class="panel-title">${rows.length} ${escapeHtml(subject)}</h3>
                </div>
            </div>
            <div class="profile-table-wrap">
                <table class="profile-table">
                    <thead>
                        <tr>
                            <th>${escapeHtml(subject)}</th>
                            <th>Matches</th>
                            <th>Win rate</th>
                            <th>Trophies</th>
                            <th>Per match</th>
                        </tr>
                    </thead>
                    <tbody>${body}</tbody>
                </table>
            </div>
        </section>`;
}


// One column per slot, height by matches played, tinted by win rate. Two
// numbers in one picture, because either alone is misleading: a busy hour with
// a bad win rate is the interesting case and neither chart shows it on its own.
function profileCycle(title, rows, key, caption) {
    if (!rows || !rows.length) return "";
    const most = Math.max(...rows.map(r => r.matches), 1);
    const columns = rows.map(row => {
        const height = Math.round((row.matches / most) * 100);
        const tone = row.matches === 0 ? "empty"
            : row.win_rate >= 55 ? "good"
            : row.win_rate >= 45 ? "even" : "poor";
        const label = key === "hour" ? String(row.hour).padStart(2, "0") : row.day.slice(0, 3);
        const title = `${label}: ${row.matches} matches, ${formatPercent(row.win_rate)} won, ${profileSigned(row.net)}`;
        return `
            <div class="cyc-col" title="${escapeHtml(title)}">
                <div class="cyc-bar-space">
                    <div class="cyc-bar ${tone}" style="height:${Math.max(height, row.matches ? 4 : 0)}%"></div>
                </div>
                <span class="cyc-label">${escapeHtml(label)}</span>
            </div>`;
    }).join("");

    return `
        <section class="panel">
            <div class="panel-header">
                <div>
                    <p class="eyebrow">${escapeHtml(title)}</p>
                    <h3 class="panel-title">${escapeHtml(caption)}</h3>
                    <p class="meta-line">Height is matches played, colour is win rate</p>
                </div>
            </div>
            <div class="cyc-chart">${columns}</div>
        </section>`;
}


function profileForm(form) {
    if (!form || !form.length) return "";
    const pips = form.map(match => {
        const tone = match.result === "victory" ? "win"
            : match.result === "defeat" ? "loss" : "draw";
        const label = `${match.brawler}: ${match.result} (${profileSigned(match.delta)})`;
        return `<span class="form-pip ${tone}" title="${escapeHtml(label)}"></span>`;
    }).join("");
    return `
        <section class="panel">
            <div class="panel-header">
                <div>
                    <p class="eyebrow">Recent form</p>
                    <h3 class="panel-title">Last ${form.length} matches</h3>
                    <p class="meta-line">Newest first</p>
                </div>
            </div>
            <div class="form-strip">${pips}</div>
        </section>`;
}


const LIVE_IDLE_HINT = `See what the bot sees, with everything it has found
    drawn on top. Works from anywhere the panel opens, and only runs while you
    are looking at it.`;
const LIVE_STOPPED_HINT = `Start the bot and this becomes a live picture of
    what it is looking at.`;


function liveViewShowing() {
    return Boolean(document.querySelector("#liveBody img"));
}


// Closing the stream is the point of this function. The connection costs the
// bot's machine CPU for as long as it is open, and clearing the src is what
// actually ends it - removing the element alone leaves the request running in
// some browsers.
function stopLiveView(hint) {
    const body = document.getElementById("liveBody");
    const button = document.getElementById("liveToggleBtn");
    if (!body || !button) return;

    const image = body.querySelector("img");
    if (image) {
        image.src = "";
        image.remove();
    }
    body.innerHTML = `<p class="live-hint">${hint || LIVE_IDLE_HINT}</p>`;
    button.textContent = "Watch";
}


function startLiveView() {
    const body = document.getElementById("liveBody");
    const button = document.getElementById("liveToggleBtn");
    if (!body || !button) return;

    // A stopped bot publishes no frames, so the stream would open, wait, and
    // close with an empty body. An <img> given an empty 200 fires neither load
    // nor error in every browser - tested, and it leaves the panel sitting on
    // a black box with a Stop button, which reads as broken. The page already
    // knows whether the bot is running, so it says so instead of finding out
    // the hard way.
    if (!state.bootstrap?.runtime?.is_running) {
        stopLiveView(LIVE_STOPPED_HINT);
        return;
    }

    const image = document.createElement("img");
    image.className = "live-image";
    image.alt = "What the bot is looking at";
    // Cache-busted: without it a browser will happily reuse the previous
    // stream's response and show a frozen first frame.
    image.src = `/api/stream?t=${Date.now()}`;
    image.addEventListener("error", () => stopLiveView(LIVE_STOPPED_HINT));
    body.innerHTML = "";
    body.appendChild(image);
    button.textContent = "Stop";
}


function toggleLiveView() {
    if (liveViewShowing()) {
        stopLiveView();
    } else {
        startLiveView();
    }
}


function renderLogs() {
    // The exe opens a console and it is easy to lose behind the app window;
    // people reported never having seen the log at all. Same text, somewhere
    // it cannot hide.
    const view = document.getElementById("view-logs");
    if (!view) return;

    view.innerHTML = `
        <div class="sheet">
            <div class="command-band">
                <div class="command-state">
                    <div class="command-title">Logs</div>
                    <div class="command-sub">Everything the bot printed this run</div>
                </div>
                <div class="command-actions">
                    <button id="refreshLogsBtn" class="btn">Refresh</button>
                    <button id="copyLogsBtn" class="btn">Copy</button>
                </div>
            </div>
            <pre id="logOutput" class="log-output">Loading...</pre>
        </div>`;

    document.getElementById("refreshLogsBtn")?.addEventListener("click", loadLogs);
    document.getElementById("copyLogsBtn")?.addEventListener("click", () => {
        const text = document.getElementById("logOutput")?.textContent || "";
        navigator.clipboard?.writeText(text).then(
            () => showToast("Log copied. Paste it wherever you are asking for help."),
            () => showToast("Could not copy the log.", "error"));
    });
    loadLogs();
}


async function loadLogs() {
    const box = document.getElementById("logOutput");
    if (!box) return;
    try {
        const result = await fetchJSON("/api/logs?lines=600", {}, true);
        const lines = (result && result.lines) || [];
        // Whether to follow the tail is the reader's decision, not ours.
        // Scrolling to the bottom on every refresh made the page unreadable:
        // scroll up to look at something and the next poll, a second later,
        // drags you back down.
        //
        // So stick to the bottom only while already there. Stepping away is
        // how somebody says "leave it alone", and coming back is how they say
        // "follow again". 24px of slack, because a half-scrolled last line
        // still counts as being at the end.
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
        box.textContent = lines.length
            ? lines.join("\n")
            : "Nothing logged yet. The file is written as the bot runs.";
        if (atBottom) {
            box.scrollTop = box.scrollHeight;
        }
    } catch {
        box.textContent = "Could not read the log.";
    }
}


function renderProfile() {
    const view = document.getElementById("view-profile");
    if (!view) return;

    // Derived entirely from the match history, so it can never disagree with
    // the rows the History tab lists. It lives under state.bootstrap, which is
    // where every other view reads its data from - there is no state.history,
    // and reading one is how this rendered "no matches" against 1224 of them.
    const p = (state.bootstrap && state.bootstrap.history
               && state.bootstrap.history.profile) || null;

    if (!p || !p.matches) {
        view.innerHTML = `
            <div class="profile-stack">
            <section class="panel">
                <div class="panel-header">
                    <div>
                        <p class="eyebrow">Profile</p>
                        <h3 class="panel-title">No matches recorded yet</h3>
                        <p class="meta-line">Everything here is worked out from the
                        match history, so it fills in as the bot plays.</p>
                    </div>
                </div>
            </section>
            </div>`;
        return;
    }

    const streak = p.current_streak >= 0
        ? `${p.current_streak} win streak`
        : `${Math.abs(p.current_streak)} loss streak`;
    const busiest = p.busiest_day
        ? `${profileDate(p.busiest_day.date)} — ${p.busiest_day.matches} matches`
        : "";

    view.innerHTML = `
        <div class="profile-stack">
        <section class="panel profile-hero">
            <div class="panel-header">
                <div>
                    <p class="eyebrow">Profile</p>
                    <h3 class="panel-title">${p.matches} matches played</h3>
                    <p class="meta-line">
                        ${profileDate(p.first_played)} to ${profileDate(p.last_played)}
                        | ${p.days_active} active days | ${p.sessions} sessions
                    </p>
                </div>
            </div>
            <div class="profile-grid">
                ${profileTile("Win rate", formatPercent(p.win_rate),
                              `${p.wins}W / ${p.losses}L / ${p.draws}D`)}
                ${profileTile("Trophies", profileSigned(p.trophies_net),
                              `+${p.trophies_won} won, -${p.trophies_lost} lost`,
                              p.trophies_net >= 0 ? "tone-up" : "tone-down")}
                ${profileTile("Per match", `${p.net_per_match >= 0 ? "+" : ""}${p.net_per_match}`,
                              `best ${profileSigned(p.best_match)}, worst ${p.worst_match}`)}
                ${profileTile("Time played", profileDuration(p.play_minutes),
                              `${p.matches_per_session} matches per session`)}
                ${profileTile("Today", p.matches_today,
                              `${profileSigned(p.trophies_today)} trophies, ${p.matches_week} this week`)}
                ${profileTile("Right now", streak,
                              `best ${p.best_streak} won, worst ${p.worst_streak} lost`,
                              p.current_streak >= 0 ? "tone-up" : "tone-down")}
                ${profileTile("Per day", p.matches_per_day, busiest)}
                ${p.best_brawler ? profileTile("Best brawler", p.best_brawler.name,
                    `${profileSigned(p.best_brawler.net)} over ${p.best_brawler.matches} matches`) : ""}
            </div>
        </section>

        ${profileForm(p.form)}
        ${profileCycle("Time of day", p.by_hour, "hour", "When it plays, and how it goes")}
        ${profileCycle("Day of week", p.by_weekday, "day", "Across the week")}
        ${profileTable("Brawlers", p.brawlers, "brawlers")}
        ${profileTable("Playstyles", p.playstyles, "playstyles")}
        ${profileTable("Gamemodes", p.gamemodes, "gamemodes")}
        </div>
    `;
}


function renderHistory() {
    const view = document.getElementById("view-history");
    const summary = getHistorySummary();

    view.innerHTML = `
        <section class="panel">
            <div class="panel-header history-head">
                    <div>
                        <p class="eyebrow">Match History</p>
                        <h3 class="panel-title history-total">${summary.total_matches} total matches</h3>
                        <p class="meta-line history-summary-meta">${summary.wins} wins | ${summary.losses} losses | ${summary.draws || 0} draws | ${formatPercent(summary.win_rate)} win rate</p>
                    </div>
                <div class="toolbar-actions history-actions">
                    <div class="tb-search compact-search">
                        <input id="historySearch" type="search" placeholder="Filter by brawler" value="${escapeHtml(state.historySearch)}">
                    </div>
                    <select id="historySort" aria-label="Sort match history">
                        <option value="matches" ${state.historySort === "matches" ? "selected" : ""}>Matches</option>
                        <option value="recent" ${state.historySort === "recent" ? "selected" : ""}>Recently played</option>
                        <option value="winrate" ${state.historySort === "winrate" ? "selected" : ""}>Win Rate</option>
                        <option value="name" ${state.historySort === "name" ? "selected" : ""}>Name</option>
                    </select>
                </div>
            </div>

            <div class="hist-grid">
                ${renderHistoryGrid()}
            </div>
        </section>
    `;

    document.getElementById("historySearch")?.addEventListener("input", (event) => {
        state.historySearch = event.target.value;
        const grid = document.querySelector("#view-history .hist-grid");
        if (grid) {
            grid.innerHTML = renderHistoryGrid();
        }
    });

    document.getElementById("historySort")?.addEventListener("change", (event) => {
        state.historySort = event.target.value;
        const grid = document.querySelector("#view-history .hist-grid");
        if (grid) {
            grid.innerHTML = renderHistoryGrid();
        }
    });

    view.removeEventListener("click", handleHistoryCardClick);
    view.addEventListener("click", handleHistoryCardClick);
    view.removeEventListener("keydown", handleHistoryCardKeydown);
    view.addEventListener("keydown", handleHistoryCardKeydown);
}

function getHistorySummary() {
    const items = state.bootstrap.history.items || [];
    const wins = items.reduce((total, item) => total + Number(item.wins || 0), 0);
    const losses = items.reduce((total, item) => total + Number(item.losses || 0), 0);
    const draws = items.reduce((total, item) => total + Number(item.draws || 0), 0);
    const totalMatches = wins + losses + draws;

    return {
        total_matches: totalMatches,
        wins,
        losses,
        draws,
        win_rate: totalMatches ? (wins / totalMatches) * 100 : 0,
        loss_rate: totalMatches ? (losses / totalMatches) * 100 : 0,
    };
}

function getFilteredHistoryItems() {
    return [...(state.bootstrap.history.items || [])]
        .filter((item) => item.brawler.toLowerCase().includes(state.historySearch.toLowerCase()))
        .sort(sortHistoryItems);
}

function renderHistoryGrid() {
    const items = getFilteredHistoryItems();
    return items.length
        ? items.map(renderHistoryCard).join("")
        : `<div class="empty-state wide-empty">No match history has been recorded yet.</div>`;
}

function historySparkline(item) {
    const values = (item.trophy_points || []).slice(-24).map(p => Number(p.value)).filter(Number.isFinite);
    if (values.length < 2) return "";
    const min = Math.min(...values), span = Math.max(...values) - min || 1;
    const points = values.map((value, i) => `${(i * 120 / (values.length - 1)).toFixed(1)},${(34 - (value - min) / span * 28).toFixed(1)}`).join(" ");
    return `<svg class="history-spark" viewBox="0 0 120 40" aria-label="Recent trophy trend" role="img"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>`;
}

function renderHistoryCard(item) {
    const trophyDelta = Number(item.trophy_delta || 0);
    return `
        <article class="hist-card" role="button" tabindex="0" data-history-brawler="${escapeHtml(item.brawler)}">
            <div class="hist-top">
                <div class="hist-identity">
                    <img src="${escapeHtml(item.icon_url)}" alt="${escapeHtml(item.brawler)}">
                    <div>
                        <h4>${escapeHtml(item.brawler)}</h4>
                        <p class="meta-line history-tracked">${item.total_matches} tracked matches</p>
                    </div>
                </div>
                <div class="hist-trophy-delta ${trophyDelta < 0 ? "negative" : "positive"}">
                    <span>${formatSignedNumber(trophyDelta)}</span>
                    <img src="/api/assets/support/trophies_icon.png" alt="Trophies">
                </div>
            </div>
            <div class="hist-stats">
                <div class="hist-stat win-stat">
                    <label>Wins</label>
                    <strong>${item.wins}</strong>
                </div>
                <div class="hist-stat loss-stat">
                    <label>Losses</label>
                    <strong>${item.losses}</strong>
                </div>
                <div class="hist-stat rate-stat win-rate-stat">
                    <label>Win%</label>
                    <strong>${formatPercent(item.win_rate)}</strong>
                </div>
                <div class="hist-stat rate-stat loss-rate-stat">
                    <label>Loss%</label>
                    <strong>${formatPercent(item.loss_rate)}</strong>
                </div>
            </div>
            ${historySparkline(item)}<div class="hist-more" aria-hidden="true">↗</div>
        </article>
    `;
}

function handleHistoryCardClick(event) {
    const card = event.target.closest("[data-history-brawler]");
    if (card) {
        openHistoryDetails(card.dataset.historyBrawler);
    }
}

function handleHistoryCardKeydown(event) {
    if (!["Enter", " "].includes(event.key)) return;
    const card = event.target.closest("[data-history-brawler]");
    if (!card) return;
    event.preventDefault();
    openHistoryDetails(card.dataset.historyBrawler);
}

function openHistoryDetails(brawlerName) {
    const item = (state.bootstrap.history.items || []).find((historyItem) => historyItem.brawler === brawlerName);
    if (!item) return;

    closeHistoryDetails();
    document.body.insertAdjacentHTML("beforeend", renderHistoryDetailOverlay(item));
    document.getElementById("historyDetailOverlay")?.addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
            closeHistoryDetails();
        }
    });
    bindHistoryChartRangeControls(item);
    scrollRecentChartToLatest();
    document.addEventListener("keydown", handleHistoryDetailKeydown);
}

function bindHistoryChartRangeControls(item) {
    document.querySelectorAll("[data-history-chart-range]").forEach((button) => {
        button.addEventListener("click", () => {
            state.historyChartRange = button.dataset.historyChartRange;
            const chartPanel = document.querySelector("#historyDetailOverlay .history-chart-panel");
            if (chartPanel) {
                chartPanel.outerHTML = renderHistoryChartPanel(item);
                bindHistoryChartRangeControls(item);
                scrollRecentChartToLatest();
            }
        });
    });
}

function scrollRecentChartToLatest() {
    if (state.historyChartRange !== "recent") return;
    requestAnimationFrame(() => {
        const scroller = document.querySelector("#historyDetailOverlay .history-chart-scroll-window");
        if (scroller) {
            scroller.scrollLeft = scroller.scrollWidth;
        }
    });
}

function closeHistoryDetails() {
    document.getElementById("historyDetailOverlay")?.remove();
    document.removeEventListener("keydown", handleHistoryDetailKeydown);
}

function handleHistoryDetailKeydown(event) {
    if (event.key === "Escape") {
        closeHistoryDetails();
    }
}

function renderHistoryDetailOverlay(item) {
    const trophyDelta = Number(item.trophy_delta || 0);
    const currentTrophies = item.current_trophies ?? "N/A";
    const peakTrophies = item.peak_trophies ?? "N/A";

    return `
        <div id="historyDetailOverlay" class="history-detail-overlay" role="dialog" aria-modal="true" aria-label="${escapeHtml(item.brawler)} match history details">
            <section class="history-detail-shell">
                <header class="history-detail-head">
                    <div class="history-detail-title">
                        <img src="${escapeHtml(item.icon_url)}" alt="${escapeHtml(item.brawler)}">
                        <div>
                            <h3>${escapeHtml(item.brawler)}</h3>
                            <p class="meta-line">Last played ${escapeHtml(item.last_played || "Unknown")}</p>
                        </div>
                    </div>
                    <div class="history-detail-actions">
                        <div class="history-trophy-hero ${trophyDelta < 0 ? "negative" : "positive"}">
                            <span>${formatSignedNumber(trophyDelta)}</span>
                            <img src="/api/assets/support/trophies_icon.png" alt="Trophies">
                        </div>
                    </div>
                </header>

                <div class="history-detail-grid">
                    ${renderHistoryChartPanel(item)}

                    <aside class="history-insights-panel">
                        <div class="history-kpi-grid">
                            ${renderHistoryKpi("Current", currentTrophies)}
                            ${renderHistoryKpi("Peak", peakTrophies)}
                            ${renderHistoryKpi("Win Rate", formatPercent(item.win_rate))}
                            ${renderHistoryKpi("Best Streak", item.best_win_streak || 0)}
                        </div>
                    </aside>
                </div>

                <div class="history-detail-bottom">
                    <section class="history-recent-panel">
                        <div class="history-section-head">
                            <h4>Recent results</h4>
                        </div>
                        ${renderHistoryResultGrid(item.trophy_points || [])}
                    </section>

                    <section class="history-playstyle-panel">
                        <div class="history-section-head">
                            <h4>Most used playstyles</h4>
                        </div>
                        <div class="history-playstyle-list">
                            ${(item.playstyles || []).length ? item.playstyles.map((playstyle) => `
                                <div class="history-playstyle-row">
                                    <span>${escapeHtml(playstyle.name)}</span>
                                    <strong>${playstyle.matches}</strong>
                                </div>
                            `).join("") : `<div class="empty-state">No playstyle data available.</div>`}
                        </div>
                    </section>
                </div>
            </section>
        </div>
    `;
}

function renderHistoryChartPanel(item) {
    return `
        <section class="history-chart-panel">
            <div class="history-section-head">
                <h4>Trophy Curve</h4>
                <div class="history-chart-controls">
                    <button class="${state.historyChartRange === "recent" ? "active" : ""}" type="button" data-history-chart-range="recent">Recent</button>
                    <button class="${state.historyChartRange === "all" ? "active" : ""}" type="button" data-history-chart-range="all">All</button>
                    <strong class="history-match-count">${escapeHtml(String(item.total_matches || item.trophy_points?.length || 0))} matches</strong>
                </div>
            </div>
            ${renderTrophyChart(item.trophy_points || [])}
        </section>
    `;
}

// How many matches the scrollable "Recent" curve draws. The width is 64px per
// match, so this is also a width cap: without one, a brawler with 269 tracked
// matches produced a 17,152px SVG with a circle and a tooltip on every point,
// and opening that card locked the tab up. "All" is unaffected - it squeezes
// the whole history into a fixed 640px and only draws the end dots.
const RECENT_CHART_POINTS = 60;

// How often the history is refetched while the bot runs. The runtime status
// is polled every 1.2 s because it changes that fast; a match result does not.
const HISTORY_POLL_MS = 15000;

function renderTrophyChart(points) {
    const showAll = state.historyChartRange === "all";
    const chartPoints = showAll ? points : points.slice(-RECENT_CHART_POINTS);
    if (chartPoints.length < 2) {
        return `<div class="history-chart-empty">Not enough trophy data to draw a curve yet.</div>`;
    }

    const width = showAll ? 640 : Math.max(640, (chartPoints.length - 1) * 64);
    const height = 210;
    const padLeft = 34;
    const padRight = 40;
    const padY = 26;
    const values = chartPoints.map((point) => Number(point.value || 0));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = Math.max(1, max - min);
    const xStep = (width - padLeft - padRight) / Math.max(1, chartPoints.length - 1);
    const coords = chartPoints.map((point, index) => {
        const value = Number(point.value || 0);
        const x = padLeft + index * xStep;
        const y = height - padY - ((value - min) / range) * (height - padY * 2);
        return { x, y, value, result: point.result, delta: point.delta, label: point.label };
    });
    const line = coords.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
    const area = `${padLeft},${height - padY} ${line} ${width - padRight},${height - padY}`;
    const last = coords[coords.length - 1];
    const latestLabelX = last.x;

    return `
        <div class="history-chart-wrap ${showAll ? "all" : "recent"}">
            <div class="history-chart-scroll-window">
            <svg class="history-chart" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Trophy evolution chart">
                <defs>
                    <linearGradient id="historyChartFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stop-color="rgba(255,42,68,0.32)" />
                        <stop offset="100%" stop-color="rgba(255,42,68,0.02)" />
                    </linearGradient>
                </defs>
                <line x1="${padLeft}" y1="${padY}" x2="${padLeft}" y2="${height - padY}" class="chart-axis" />
                <line x1="${padLeft}" y1="${height - padY}" x2="${width - padRight}" y2="${height - padY}" class="chart-axis" />
                <text x="${padLeft}" y="18" class="chart-label">${max}</text>
                <text x="${padLeft}" y="${height - 7}" class="chart-label">${min}</text>
                <text x="${latestLabelX.toFixed(1)}" y="${Math.max(18, last.y - 14).toFixed(1)}" text-anchor="middle" class="chart-label chart-latest-label">${last.value}</text>
                <polygon points="${area}" class="chart-area"></polygon>
                <polyline points="${line}" class="chart-line"></polyline>
                ${coords.map((point, index) => {
                    if (showAll && index !== 0 && index !== coords.length - 1) return "";
                    return `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="${point === last ? 5 : 3}" class="chart-dot ${point.result === "victory" ? "victory" : point.result === "defeat" ? "defeat" : "draw"}" data-tooltip="${escapeHtml(historyPointTooltip(point))}"></circle>`;
                }).join("")}
            </svg>
            </div>
            <div class="history-chart-meta">
                <span>${escapeHtml(chartPoints[0].label || "First match")}${
                    !showAll && points.length > chartPoints.length
                        ? ` (last ${chartPoints.length} of ${points.length})`
                        : ""}</span>
                <strong>${last.value} trophies</strong>
                <span>${escapeHtml(chartPoints[chartPoints.length - 1].label || "Latest match")}</span>
            </div>
        </div>
    `;
}

function renderHistoryKpi(label, value) {
    return `
        <div class="history-kpi">
            <label>${escapeHtml(label)}</label>
            <strong>${escapeHtml(value)}</strong>
        </div>
    `;
}

function renderHistoryResultGrid(points) {
    const tiles = points.slice(-72).reverse();
    return tiles.length
        ? `<div class="history-result-grid">${tiles.map(renderHistoryResultTile).join("")}</div>`
        : `<div class="empty-state">No recent match rows available.</div>`;
}

function renderHistoryResultTile(point) {
    const result = String(point.result || "unknown");

    return `
        <div class="history-result-tile ${escapeHtml(result)}" data-tooltip="${escapeHtml(point.label || "Unknown time")}">
            <strong>${formatSignedNumber(point.delta || 0)}</strong>
            <span>${escapeHtml(point.value ?? "N/A")}</span>
        </div>
    `;
}

function historyPointTooltip(point) {
    const delta = Number(point.delta || 0);
    const deltaClass = delta < 0 ? "negative" : "positive";
    return [
        point.label || "Unknown time",
        `<span class="tooltip-trophy-line ${deltaClass}">${formatSignedNumber(delta)} <img src="/api/assets/support/trophies_icon.png" alt=""></span>`,
        `<span class="tooltip-trophy-line total">${point.value ?? "N/A"} <img src="/api/assets/support/trophies_icon.png" alt=""></span>`,
    ].join("<br>");
}

function formatResultLabel(value) {
    return String(value || "unknown").replaceAll("_", " ");
}

// The five groups, in the order they are shown. Data rather than markup: the
// navigation, the pane and the reset button all read from the same row, so a
// section cannot end up in the list without a page or the other way round.
// ── Accounts (multi-instance) ──────────────────────────────────────────
// Each account is a separate VvokAI process on its own MuMu window, its own
// config and its own panel. This page is the supervisor: it lists them, starts
// and stops the processes, and links out to each account's own panel where that
// account's brawlers, playstyle and token are set.

function instanceEmptyMessage() {
    return `<div class="empty-state wide-empty">No accounts yet. Add one below - one per MuMu window.</div>`;
}

// Seconds to wait after Start before offering "Configure", so the account's own
// panel is actually listening by the time it can be clicked.
const INSTANCE_PANEL_WARMUP = 5;

function renderInstanceRow(item) {
    const running = item.running;
    const dot = `<span class="status-indicator ${running ? "is-running" : "is-idle"}"></span>`;
    const state_ = running ? "Running" : "Stopped";
    const toggle = running
        ? `<button class="btn" data-instance-stop="${escapeHtml(item.name)}">Stop</button>`
        : `<button class="btn btn-primary" data-instance-start="${escapeHtml(item.name)}">Start</button>`;
    // Hold "Configure" back until the account's own web server has had time to
    // come up. Offering it the instant Start was pressed meant a quick click
    // opened a port nothing was serving yet - "127.0.0.1 refused to connect".
    const warmingUp = item.uptime !== null && item.uptime !== undefined
        && item.uptime < INSTANCE_PANEL_WARMUP;
    const open = (running && item.url && !warmingUp)
        ? `<button class="btn" data-instance-open="${escapeHtml(item.name)}">Configure</button>`
        : (running && warmingUp ? `<button class="btn" disabled>Starting...</button>` : "");
    const where = escapeHtml(item.adb_serial) + (item.port ? ` &middot; :${item.port}` : "");
    // A live preview of the emulator - the only reliable way to tell which
    // account is which, since the name and serial say nothing about the lobby.
    const thumb = `<img src="/api/instances/${encodeURIComponent(item.name)}/screenshot" alt=""
        onerror="this.style.visibility='hidden'"
        style="width:132px;height:74px;object-fit:cover;border-radius:8px;background:#111;flex:0 0 auto">`;
    return `
        <div class="spec-row" style="align-items:center;gap:14px;padding:12px 0">
            ${thumb}
            <div style="flex:1;min-width:0">
                <div>${dot} <strong>${escapeHtml(item.name)}</strong> <span style="opacity:.6">${state_}</span></div>
                <div style="opacity:.6;font-size:12px">${where}</div>
            </div>
            <div class="toolbar-actions">${toggle} ${open}
                <button class="btn" data-instance-remove="${escapeHtml(item.name)}">Remove</button>
            </div>
        </div>`;
}

function renderInstances() {
    const view = document.getElementById("view-instances");
    if (!view) return;
    const data = state.instances || { is_supervisor: false, items: [] };
    if (!data.is_supervisor) {
        view.innerHTML = `<div class="empty-state wide-empty">Accounts are managed from the main panel.</div>`;
        return;
    }

    // Configuring one account: its whole panel, embedded here, so setting up
    // another is just Back and pick the next - never a separate browser tab.
    if (state.instanceViewing) {
        const item = (data.items || []).find((i) => i.name === state.instanceViewing);
        if (item && item.running && item.url) {
            // Full app window, not a cramped panel inside the content column -
            // the embedded panel needs all the room it can get.
            view.innerHTML = `
                <div style="position:fixed;inset:0;z-index:1000;display:flex;flex-direction:column;background:#0b0b0f">
                    <div style="display:flex;align-items:center;gap:14px;padding:10px 16px;background:rgba(0,0,0,.55);border-bottom:1px solid rgba(255,255,255,.08)">
                        <button class="btn btn-primary" data-instance-back="1">&larr; Back to accounts</button>
                        <span style="opacity:.85">Configuring <strong>${escapeHtml(item.name)}</strong> &middot; ${escapeHtml(item.adb_serial)}</span>
                    </div>
                    <iframe src="${escapeHtml(item.url)}" title="${escapeHtml(item.name)}" style="flex:1;width:100%;border:0;background:#0b0b0f"></iframe>
                </div>`;
            return;
        }
        state.instanceViewing = null;
    }

    const rows = (data.items || []).map(renderInstanceRow).join("") || instanceEmptyMessage();
    view.innerHTML = `
        <div class="ps-page">
            <section class="panel">
                <p class="eyebrow">Accounts</p>
                <p style="opacity:.7;margin:.3rem 0 1rem">Each account runs as its own process on its own emulator window - resources are not shared. Press Detect to find running emulators automatically, or add one by hand below. Then Start it and open its panel to set that account's token, brawlers and playstyle.</p>
                <div class="toolbar-actions" style="margin-bottom:12px">
                    <button id="instanceScanBtn" class="btn btn-primary">Detect emulators</button>
                </div>
                <div id="instanceList">${rows}</div>
            </section>
            <section class="panel">
                <p class="eyebrow">Add account</p>
                <form id="instanceAddForm" class="modal-form">
                    <label class="input-group"><span>Name</span><input id="instName" type="text" placeholder="acc1" autocomplete="off"></label>
                    <label class="input-group"><span>ADB serial</span><input id="instSerial" type="text" placeholder="127.0.0.1:16384" autocomplete="off"></label>
                    <label class="input-group"><span>Panel port</span><input id="instPort" type="number" placeholder="5001" autocomplete="off"></label>
                    <button class="btn btn-primary" type="submit">Add account</button>
                </form>
            </section>
        </div>`;
    document.getElementById("instanceAddForm")?.addEventListener("submit", handleInstanceAdd);
    document.getElementById("instanceScanBtn")?.addEventListener("click", handleInstanceScan);
}

async function handleInstanceScan(event) {
    const btn = event.currentTarget;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Scanning...";
    try {
        const result = await fetchJSON("/api/instances/scan", { method: "POST" }, true);
        if (result && result.ok === false) {
            showToast(result.message || "Scan failed.", "error");
        } else if (result && result.added && result.added.length) {
            showToast(`Added ${result.added.length} emulator(s): ${result.added.join(", ")}.`, "success");
        } else {
            showToast(`Found ${result?.found ?? 0} emulator(s), all already in the list.`, "success");
        }
    } catch (error) {
        showToast(error.message || "Scan failed.", "error");
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
    await refreshInstances();
}

async function updateInstanceList() {
    const data = await fetchJSON("/api/instances", {}, true);
    if (!data) return;
    state.instances = data;
    const list = document.getElementById("instanceList");
    if (list && data.is_supervisor) {
        list.innerHTML = (data.items || []).map(renderInstanceRow).join("") || instanceEmptyMessage();
    }
}

async function refreshInstances() {
    const data = await fetchJSON("/api/instances", {}, true);
    if (data) state.instances = data;
    renderInstances();
}

async function handleInstanceAdd(event) {
    event.preventDefault();
    const name = document.getElementById("instName").value.trim();
    const adb_serial = document.getElementById("instSerial").value.trim();
    const port = document.getElementById("instPort").value.trim();
    try {
        await fetchJSON("/api/instances", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, adb_serial, port: port || null }),
        });
        showToast(`Account "${name}" added.`, "success");
        await refreshInstances();
    } catch (error) {
        showToast(error.message || "Could not add the account.", "error");
    }
}

async function instanceAction(action, name) {
    const isRemove = action === "remove";
    const url = isRemove
        ? `/api/instances/${encodeURIComponent(name)}`
        : `/api/instances/${encodeURIComponent(name)}/${action}`;
    try {
        const result = await fetchJSON(url, { method: isRemove ? "DELETE" : "POST" }, true);
        if (result && result.ok === false) {
            showToast(result.message || `Could not ${action} ${name}.`, "error");
        } else if (result && result.message) {
            showToast(result.message, "success");
        }
    } catch (error) {
        showToast(error.message || `Could not ${action} ${name}.`, "error");
    }
    if (isRemove) await refreshInstances();
    else await updateInstanceList();
}

function startInstancePolling() {
    // Refresh only the status list (never the whole view, so a half-typed Add
    // form is not wiped), and only while the Accounts page is open.
    if (state.instancePollTimer) return;
    state.instancePollTimer = setInterval(async () => {
        if (!document.hidden && !state.instancePollBusy && state.currentView === "instances" && state.instances?.is_supervisor) {
            state.instancePollBusy = true;
            try { await updateInstanceList(); }
            finally { state.instancePollBusy = false; }
        }
    }, 4000);
}

const SETTINGS_TABS = [
    { id: "general", label: "General", blurb: "Runtime and environment" },
    { id: "bot", label: "Behavior", blurb: "Combat and recovery" },
    { id: "timers", label: "Timers", blurb: "Timing controls" },
    { id: "webhook", label: "Integrations", blurb: "Webhook and alerts" },
    { id: "debug", label: "Debug", blurb: "Diagnostics" },
];

function renderSettings() {
    const view = document.getElementById("view-settings");

    // Never rebuild the panel while someone is typing in it.
    //
    // This is called from the autosave, from the player-info refresh and from
    // the player-tag debounce, and it replaces the whole panel with fresh
    // markup - so a field being filled in was destroyed mid-word along with
    // whatever had not been saved yet. Typing an email address and watching it
    // vanish was exactly this.
    const focused = document.activeElement;
    if (focused && view.contains(focused)
        && ["INPUT", "SELECT", "TEXTAREA"].includes(focused.tagName)) {
        return;
    }

    // All five used to be stacked on one page: about sixty controls at once,
    // which is a wall rather than a page. One at a time, with the others a
    // click away.
    const active = SETTINGS_TABS.find((tab) => tab.id === state.settingsTab)
        || SETTINGS_TABS[0];

    // Settings arrive with the bootstrap. Opening the page before that lands -
    // which happens on a cold start, and is how this came up - used to throw
    // inside the field map and leave the pane blank with nothing to explain
    // it.
    const values = (state.bootstrap.settings || {})[active.id];
    if (!values) {
        view.innerHTML = `<div class="set-layout"><div></div>
            <section class="panel settings-section">
                <p class="muted">Loading settings...</p>
            </section></div>`;
        return;
    }

    const fields = active.id === "timers"
        ? SETTINGS_META.timers.map((field) =>
            renderTimerField(field, values[field.key])).join("")
        : SETTINGS_META[active.id].map((field) =>
            renderSettingField(active.id, field, values[field.key])).join("");

    view.innerHTML = `
        <div class="set-layout">
            <nav class="set-nav" aria-label="Settings sections">
                ${SETTINGS_TABS.map((tab) => `
                    <button class="set-nav-item ${tab.id === active.id ? "is-active" : ""}"
                            data-settings-tab="${tab.id}"
                            aria-current="${tab.id === active.id ? "page" : "false"}">
                        <strong>${escapeHtml(tab.label)}</strong>
                        <span>${escapeHtml(tab.blurb)}</span>
                    </button>
                `).join("")}
            </nav>

            <section class="panel settings-section">
                <div class="panel-header compact-header">
                    <div>
                        <p class="eyebrow">${escapeHtml(active.label)}</p>
                        <h3 class="panel-title">${escapeHtml(active.blurb)}</h3>
                    </div>
                    <button class="btn-reset-settings" data-reset-section="${active.id}">Reset Settings</button>
                </div>
                <div class="settings-list">${fields}</div>
            </section>
        </div>
    `;

    view.querySelectorAll("[data-settings-tab]").forEach((button) => {
        button.addEventListener("click", () => {
            state.settingsTab = button.dataset.settingsTab;
            renderSettings();
            // Otherwise a short section opens halfway down, at whatever scroll
            // position the long one was left at.
            view.scrollIntoView({ block: "start" });
        });
    });

    bindSettingsEvents();
}

function renderSettingField(section, field, value) {
    if (!shouldRenderSettingField(section, field)) {
        return "";
    }

    if (field.type === "checkbox") {
        // Advanced Debug Visuals used to be gated here because the drawing
        // data came from the early_access module. This fork computes it in
        // Play.build_advanced_visuals instead, so there is nothing to gate -
        // the lock markup that used to sit behind a constant false is gone
        // with the rest of it.
        return `
            <label class="setting-row check-card check-card-right">
                <span class="check-info">
                    <strong>${escapeHtml(field.label)}</strong>
                    <span>${escapeHtml(field.help)}</span>
                </span>
                <span class="check-control">
                    <input type="checkbox" data-setting-section="${section}" data-setting-key="${field.key}" ${value ? "checked" : ""}>
                    <span class="check-box"></span>
                </span>
            </label>
        `;
    }

    if (field.type === "select") {
        return `
            <div class="setting-row">
                <div class="setting-copy">
                    <div class="setting-label">
                        <strong>${escapeHtml(field.label)}</strong>
                        <span class="tooltip-anchor" data-tooltip="${escapeHtml(field.help)}">?</span>
                    </div>
                    <p class="help-text">${escapeHtml(field.help)}</p>
                </div>
                <div class="setting-input-wrap">
                    <select data-setting-section="${section}" data-setting-key="${field.key}">
                        ${(field.options || []).map((option) => `
                            <option value="${escapeHtml(option.value)}" ${option.value === value ? "selected" : ""}>${escapeHtml(option.label)}</option>
                        `).join("")}
                    </select>
                </div>
            </div>
        `;
    }

    // Player Tag needs a working stats source, which is now the public
    // Brawl Stars API rather than the early_access module.
    const isEarlyAccessLocked = !state.bootstrap?.auth?.player_api && field.key === "player_tag";
    return `
        <div class="setting-row ${isEarlyAccessLocked ? "setting-locked needs-api-token" : ""}">
            <div class="setting-copy">
                <div class="setting-label">
                    <strong>${escapeHtml(field.label)} ${isEarlyAccessLocked ? `<span class="ea-badge-inline">Needs API token</span>` : ""}</strong>
                    <span class="tooltip-anchor" data-tooltip="${escapeHtml(field.help)}">?</span>
                </div>
                <p class="help-text">${escapeHtml(field.help)}</p>
            </div>
            <div class="setting-input-wrap ${field.suffix ? "has-suffix" : ""}">
                <input data-setting-section="${section}" data-setting-key="${field.key}" type="${field.type}" step="${field.step || "1"}" placeholder="${isEarlyAccessLocked ? "Set Brawl Stars API Token below first" : escapeHtml(field.placeholder || "")}" value="${isEarlyAccessLocked ? "" : escapeHtml(formatSettingValue(field, value))}" ${isEarlyAccessLocked ? "readonly" : ""}>
                ${field.suffix ? `<span class="input-suffix">${escapeHtml(field.suffix)}</span>` : ""}
            </div>
        </div>
    `;
}

function shouldRenderSettingField(section, field) {
    if (!field.visibleIf) {
        return true;
    }

    const sectionSettings = state.bootstrap?.settings?.[section] || {};
    return sectionSettings[field.visibleIf.key] === field.visibleIf.value;
}

function renderTimerField(field, value) {
    return `
        <div class="timer-box">
            <div class="timer-header">
                <div>
                    <h5>${escapeHtml(field.label)}</h5>
                    <span>${escapeHtml(field.help)}</span>
                </div>
                <input data-setting-section="timers" data-setting-key="${field.key}" data-timer-input="${field.key}" type="number" step="${field.step}" value="${value}">
            </div>
            <div class="slider-shell">
                <span class="slider-edge">${field.min}s</span>
                <input class="slider" data-setting-section="timers" data-setting-key="${field.key}" data-timer-key="${field.key}" type="range" min="${field.min}" max="${field.max}" step="${field.step}" value="${value}">
                <span class="slider-edge">${field.max}s</span>
            </div>
        </div>
    `;
}

function renderQueueDock() {
    const dock = document.getElementById("queueDock");
    if (!dock) return;

    const visible = ["dashboard", "queue"].includes(state.currentView);
    dock.classList.toggle("hidden", !visible);
    if (!visible) return;

    const isQueueView = state.currentView === "queue";
    const hasQueueItems = state.bootstrap.queue.length > 0;
    const manageLabel = isQueueView ? "Clear Queue" : "Open Brawlers";
    const manageDisabled = isQueueView && !hasQueueItems ? "disabled" : "";

    dock.innerHTML = `
        <div class="queue-dock-head">
            <div>
                <p class="queue-title">Queue</p>
                <p class="meta-line">${state.bootstrap.queue.length ? `${state.bootstrap.queue.length} brawler${state.bootstrap.queue.length === 1 ? "" : "s"} ready` : "No brawlers queued yet."}</p>
            </div>
            <div class="dock-actions">
                <button id="queueDockManageBtn" class="btn btn-sm" ${manageDisabled}>${manageLabel}</button>
            </div>
        </div>
        ${renderQueueStrip(state.bootstrap.queue)}
    `;
    document.getElementById("queueDockManageBtn")?.addEventListener("click", () => {
        if (isQueueView) {
            clearQueue();
            return;
        }
        setView("queue");
    });
    bindQueueStripEvents();
}

function renderQueueStrip(queue) {
    if (!queue.length) {
        return `<div class="queue-empty">Build a queue from the Brawlers tab to see it here.</div>`;
    }

    return `
        <div id="queueStrip" class="queue-strip">
            ${queue.map((item, index) => `
                <article class="queue-item" draggable="true" data-queue-brawler="${escapeHtml(item.brawler)}" data-tooltip="${escapeHtml(queueTooltip(item))}">
                    <span class="queue-index">${index + 1}</span>
                    <img class="qi-img" src="${escapeHtml(item.icon_url)}" alt="${escapeHtml(item.brawler)}">
                    <div class="qi-text">
                        <strong>${escapeHtml(item.brawler)}</strong>
                        <span>${escapeHtml(item.current_label)}: ${item.current_value}</span>
                        <span>${escapeHtml(item.target_label)}: ${item.push_until}</span>
                    </div>
                    <button class="qi-del" data-delete-queue="${escapeHtml(item.brawler)}" aria-label="Remove ${escapeHtml(item.brawler)} from the queue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg></button>
                </article>
            `).join("")}
        </div>
    `;
}

function bindRuntimeButtons() {
    for (const [id, key] of [["schedCloseGame", "close_game_when_scheduled"],
                             ["schedShutdown", "shutdown_when_done"]]) {
        const box = document.getElementById(id);
        if (!box) continue;
        box.addEventListener("change", async () => {
            const payload = { ...(state.bootstrap.settings.bot || {}), [key]: box.checked };
            const result = await fetchJSON("/api/settings/bot", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            }, true);
            if (result && result.ok !== false) state.bootstrap.settings.bot = result;
            if (key === "shutdown_when_done" && box.checked) {
                showToast("The computer will power off 60 seconds after the bot "
                          + "finishes. Run 'shutdown /a' to cancel.", "success");
            }
        });
    }

    for (const [id, key] of [["schedStopAt", "stop_at"],
                             ["schedResumeAt", "resume_at"]]) {
        const field = document.getElementById(id);
        if (!field) continue;
        // On change, not on every keystroke: half a typed time is not a time,
        // and saving it would clear the setting on the way through.
        field.addEventListener("change", async () => {
            const value = field.value.trim();
            const payload = { ...(state.bootstrap.settings.bot || {}), [key]: value };
            const result = await fetchJSON("/api/settings/bot", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            }, true);
            if (!result || result.ok === false) {
                showToast(result?.message || "Schedule could not be saved.", "error");
                return;
            }
            state.bootstrap.settings.bot = result;
            showToast("Schedule saved. It applies the next time VvokAI starts.", "success");
            renderDashboard();
        });
    }

    document.getElementById("startRuntimeBtn")?.addEventListener("click", async () => {
        const button = document.getElementById("startRuntimeBtn");
        if (button.classList.contains("is-disabled")) return;

        const result = await fetchJSON("/api/runtime/start", { method: "POST" }, true);
        if (!result.ok) {
            if (result.auth) {
                state.bootstrap.auth = result.auth;
                toggleAuthModal();
            }
            showToast(result.code ? formatAuthToast(result) : (result.message || "Unable to start VvokAI."), "error");
            return;
        }

        state.bootstrap.runtime = result.runtime;
        updateChrome();
        renderDashboard();
        renderQueueDock();
        showToast("VvokAI runtime started.", "success");
    });

    document.getElementById("resumeRuntimeBtn")?.addEventListener("click", async () => {
        const result = await fetchJSON("/api/runtime/start", { method: "POST" }, true);
        if (!result.ok) {
            if (result.auth) {
                state.bootstrap.auth = result.auth;
                toggleAuthModal();
            }
            showToast(result.code ? formatAuthToast(result) : (result.message || "Unable to resume VvokAI."), "error");
            return;
        }

        state.bootstrap.runtime = result.runtime;
        updateChrome();
        renderDashboard();
        renderQueueDock();
        showToast("VvokAI runtime resumed.", "success");
    });

    document.getElementById("pauseRuntimeBtn")?.addEventListener("click", async () => {
        const button = document.getElementById("pauseRuntimeBtn");
        if (button?.classList.contains("is-disabled")) return;
        const result = await fetchJSON("/api/runtime/pause", { method: "POST" }, true);
        if (!result.ok) {
            showToast(result.message || "Unable to pause VvokAI.", "error");
            return;
        }

        state.bootstrap.runtime = result.runtime;
        updateChrome();
        renderDashboard();
        renderQueueDock();
        showToast(result.message || "Pause requested.", "success");
    });

    document.getElementById("stopRuntimeBtn")?.addEventListener("click", async () => {
        const result = await fetchJSON("/api/runtime/stop", { method: "POST" }, true);
        if (!result.ok) {
            showToast(result.message || "Unable to stop VvokAI.", "error");
            return;
        }

        state.bootstrap.runtime = result.runtime;
        updateChrome();
        renderDashboard();
        renderQueueDock();
        showToast(result.message || "Stop requested.", "success");
    });
}

function startRuntimePolling() {
    if (state.runtimePollTimer) return;
    const tick = async () => {
        try { await refreshRuntimeState(); }
        finally {
            const delay = document.hidden ? 10000 : state.bootstrap?.runtime?.is_running ? 1200 : 4000;
            state.runtimePollTimer = setTimeout(tick, delay);
        }
    };
    state.runtimePollTimer = setTimeout(tick, 1200);
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            if (liveViewShowing()) stopLiveView();
        } else {
            refreshRuntimeState();
        }
    });
}

async function refreshRuntimeState() {
    if (!state.bootstrap || document.hidden || state.runtimePollBusy) return;
    state.runtimePollBusy = true;

    try {
        const result = await fetchJSON("/api/runtime/status", {}, true);
        if (!result.ok || !result.runtime) return;

        const prevState = state.bootstrap.runtime?.state;
        state.bootstrap.runtime = result.runtime;

        if (state.currentView === "logs") {
            loadLogs();
        }

        if (result.runtime.is_running) {
            await refreshRunningQueue();
            // Not on every tick. A match takes minutes, the history payload is
            // ~290 KB built by re-parsing the whole CSV, and asking for it
            // every 1.2 s was the tab freezing rather than anything on screen.
            const now = Date.now();
            if (now - (state.lastHistoryPoll || 0) >= HISTORY_POLL_MS) {
                state.lastHistoryPoll = now;
                await refreshMatchHistory();
            }
        }

        // Every poll, not only when the state changes. A running bot stays
        // "running" for hours, and everything that actually moves - the rate,
        // what it is doing - moves inside that.
        updateLiveChrome();

        if (prevState !== result.runtime.state) {
            updateChrome();
            if (state.currentView === "dashboard") {
                renderDashboard();
                renderQueueDock();
            }
            if (result.runtime.state === "error") {
                showToast(result.runtime.last_error || "VvokAI stopped with an error.", "error");
            }

            if (prevState === "running" && !result.runtime.is_running) {
                await refreshMatchHistory();
            }
        }
    } catch {
        return;
    } finally {
        state.runtimePollBusy = false;
    }
}

function historySignature(history) {
    const summary = (history && history.summary) || {};
    return [summary.total_matches, summary.wins, summary.losses, summary.draws,
            (history && history.items || []).length].join("/");
}

async function refreshMatchHistory() {
    try {
        const result = await fetchJSON("/api/history", {}, true);
        if (!result || !result.items) return;

        // A fingerprint, not a deep compare. JSON.stringify on both sides of
        // this was ~580 KB of string building per poll, on the main thread,
        // to answer a question the totals already answer: the history only
        // ever grows, so a changed count is a new match and nothing else.
        const signature = historySignature(result);
        if (signature === state.historySignature) return;
        state.historySignature = signature;

        state.bootstrap.history = result;

        if (state.currentView === "history") {
            const summary = getHistorySummary();
            const totalEl = document.querySelector("#view-history .history-total");
            const metaEl = document.querySelector("#view-history .history-summary-meta");
            if (totalEl) totalEl.textContent = `${summary.total_matches} total matches`;
            if (metaEl) metaEl.textContent = `${summary.wins} wins | ${summary.losses} losses | ${summary.draws || 0} draws | ${formatPercent(summary.win_rate)} win rate`;

            const grid = document.querySelector("#view-history .hist-grid");
            if (grid) grid.innerHTML = renderHistoryGrid();
        }
    } catch {
        return;
    }
}

async function refreshRunningQueue() {
    const result = await fetchJSON("/api/queue", {}, true);
    if (!result.items) return;

    const nextQueue = result.items || [];
    if (JSON.stringify(nextQueue) === JSON.stringify(state.bootstrap.queue)) return;

    state.bootstrap.queue = nextQueue;
    syncQueueFormState();
    if (state.currentView === "dashboard") {
        renderDashboard();
    }
    if (state.currentView === "queue") {
        renderQueue();
    }
    renderQueueDock();
}

function bindQueueEvents() {
    document.getElementById("brawlerSearch")?.addEventListener("input", (event) => {
        state.brawlerSearch = event.target.value;
        document.getElementById("brawlerGrid").innerHTML = renderBrawlerCards();
        bindBrawlerCardEvents();
    });

    document.getElementById("playerTagInput")?.addEventListener("input", (event) => {
        event.target.value = ensurePlayerTagPrefix(event.target.value);
    });

    document.getElementById("playerTagInput")?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            event.target.blur();
        }
    });

    document.getElementById("playerTagInput")?.addEventListener("blur", async (event) => {
        event.target.value = formatPlayerTagInput(event.target.value);
        await commitPlayerTagUpdate(event.target.value.trim());
    });

    document.getElementById("loadQueueBtn")?.addEventListener("click", () => {
        document.getElementById("queueFileInput")?.click();
    });

    document.getElementById("queueFileInput")?.addEventListener("change", handleQueueImport);

    document.getElementById("pushAllQueueBtn")?.addEventListener("click", pushAllToDefaultTarget);

    document.getElementById("playOrderSelect")?.addEventListener("change", async (event) => {
        await savePlayOrder(event.target.value);
    });

    bindBrawlerCardEvents();

    document.querySelectorAll("[data-target-type]").forEach((button) => {
        button.addEventListener("click", () => {
            state.queueTargetType = button.dataset.targetType;
            renderQueue(true);
        });
    });

    document.getElementById("saveQueueItemBtn")?.addEventListener("click", saveQueueItem);
}

function bindBrawlerCardEvents() {
    document.querySelectorAll("[data-brawler]").forEach((button) => {
        button.addEventListener("click", () => {
            state.selectedBrawler = button.dataset.brawler;
            syncQueueFormState();
            renderQueue(true);
        });
    });
}

function bindQueueStripEvents() {
    document.querySelectorAll("[data-delete-queue]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();

            const brawler = button.dataset.deleteQueue;
            try {
                const result = await fetchJSON(`/api/queue/${encodeURIComponent(brawler)}`, { method: "DELETE" });
                state.bootstrap.queue = result.items;

                if (state.selectedBrawler === brawler) {
                    syncQueueFormState();
                }

                renderDashboard();
                renderQueue(true);
                renderQueueDock();
                showToast(`${brawler} removed from queue.`, "success");
            } catch (error) {
                showToast(error.message || `Unable to remove ${brawler} from queue.`, "error");
            }
        });
    });

    const strip = document.getElementById("queueStrip");
    if (!strip) return;

    let originalOrder = [];
    let suppressQueueItemClick = false;

    strip.querySelectorAll("[data-queue-brawler]").forEach((item) => {
        item.addEventListener("click", (event) => {
            if (event.target.closest("[data-delete-queue]")) return;
            if (suppressQueueItemClick) {
                suppressQueueItemClick = false;
                return;
            }
            selectBrawlerFromQueue(item.dataset.queueBrawler);
        });

        item.addEventListener("dragstart", () => {
            originalOrder = [...strip.querySelectorAll("[data-queue-brawler]")].map((node) => node.dataset.queueBrawler);
            suppressQueueItemClick = true;
            item.classList.add("dragging");
        });

        item.addEventListener("dragend", async () => {
            item.classList.remove("dragging");
            const order = [...strip.querySelectorAll("[data-queue-brawler]")].map((node) => node.dataset.queueBrawler);
            if (JSON.stringify(order) === JSON.stringify(originalOrder)) return;
            await persistQueueOrder(order);
        });
    });

    strip.addEventListener("dragover", (event) => {
        event.preventDefault();
        const dragged = strip.querySelector(".dragging");
        if (!dragged) return;

        const afterElement = getDragAfterElement(strip, event.clientX);
        if (!afterElement) {
            strip.appendChild(dragged);
        } else {
            strip.insertBefore(dragged, afterElement);
        }
    });
}

function getDragAfterElement(container, x) {
    const elements = [...container.querySelectorAll("[data-queue-brawler]:not(.dragging)")];

    return elements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        const offset = x - box.left - box.width / 2;

        if (offset < 0 && offset > closest.offset) {
            return { offset, element: child };
        }

        return closest;
    }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
}

async function clearQueue() {
    if (!state.bootstrap.queue.length) return;

    try {
        const result = await fetchJSON("/api/queue", { method: "DELETE" });
        state.bootstrap.queue = result.items || [];
        syncQueueFormState();
        renderDashboard();
        renderQueue(true);
        renderQueueDock();
        showToast("Queue cleared.", "success");
    } catch (error) {
        showToast(error.message || "Unable to clear queue.", "error");
    }
}

async function persistQueueOrder(order) {
    try {
        const result = await fetchJSON("/api/queue/reorder", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order }),
        });

        state.bootstrap.queue = result.items;
        renderDashboard();
        renderQueue(true);
        renderQueueDock();
        showToast("Queue reordered.", "success");
    } catch (error) {
        showToast(error.message || "Unable to reorder queue.", "error");
        renderDashboard();
        renderQueue(true);
        renderQueueDock();
    }
}

function bindPlaystyleEvents() {
    document.getElementById("playstyleSearch")?.addEventListener("input", (event) => {
        state.playstyleSearch = event.target.value;
        const library = document.querySelector("#view-playstyles .ps-library");
        if (library) {
            library.innerHTML = renderPlaystyleLibrary();
            bindPlaystyleCardEvents();
        }
    });

    document.getElementById("importPlaystyleBtn")?.addEventListener("click", () => {
        if (!window.confirm("WARNING: Importing custom playstyles carries security risks.\nPlaystyle files (.vvok) contain Python code that runs directly on your system.\nOnly import playstyles from authors you completely trust.\n\nDo you want to proceed?")) {
            return;
        }
        document.getElementById("playstyleFileInput")?.click();
    });

    document.getElementById("playstyleFileInput")?.addEventListener("change", handlePlaystyleImport);

    bindPlaystyleCardEvents();
}

function bindPlaystyleCardEvents() {
    document.querySelectorAll("[data-delete-playstyle]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();

            const filename = button.dataset.deletePlaystyle;
            const playstyle = state.bootstrap.playstyles.items?.find((item) => item.filename === filename);
            const label = playstyle?.name || filename;
            if (!window.confirm(`Delete "${label}"? This removes the playstyle file.`)) return;

            const result = await fetchJSON(`/api/playstyles/${encodeURIComponent(filename)}`, { method: "DELETE" });
            state.bootstrap.playstyles = result.playstyles;
            renderDashboard();
            renderPlaystyles();
            showToast(`${label} deleted.`, "success");
        });
    });

    document.querySelectorAll("[data-activate-playstyle]").forEach((button) => {
        button.addEventListener("click", async () => {
            const filename = button.dataset.activatePlaystyle;
            const result = await fetchJSON("/api/playstyles/active", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename }),
            });

            state.bootstrap.playstyles = result.playstyles;
            state.bootstrap.settings.bot.current_playstyle = filename;
            renderDashboard();
            renderPlaystyles();
            showToast("Playstyle activated.", "success");
        });
    });
}

function bindSettingsEvents() {
    document.querySelectorAll("[data-setting-section]").forEach((input) => {
        const eventName = input.type === "checkbox" || input.type === "range" ? "input" : "change";
        if (input.dataset.settingKey === "player_tag") {
            input.addEventListener("input", () => {
                input.value = ensurePlayerTagPrefix(input.value);
            });
            input.addEventListener("blur", () => {
                input.value = formatPlayerTagInput(input.value);
                scheduleAutosave(input);
            });
        }
        input.addEventListener(eventName, () => scheduleAutosave(input));

        // Text-like fields also save as they are typed, not only when focus
        // leaves them. "change" fires on blur, so anything typed and not yet
        // blurred existed nowhere but in the DOM - and the moment any of the
        // several callers of renderSettings() rebuilt the panel, it was gone.
        // The autosave is debounced, so this is still one request per pause in
        // typing rather than one per keystroke.
        if (["text", "password", "number", "email"].includes(input.type)
            && input.dataset.settingKey !== "player_tag") {
            input.addEventListener("input", () => scheduleAutosave(input));
        }
    });

    document.querySelectorAll("[data-timer-key]").forEach((slider) => {
        setSliderVisual(slider);
        const syncTimerInput = () => {
            const input = document.querySelector(`[data-timer-input="${slider.dataset.timerKey}"]`);
            if (input) {
                input.value = slider.value;
            }
            setSliderVisual(slider);
            return input;
        };
        slider.addEventListener("input", syncTimerInput);
        slider.addEventListener("input", () => scheduleAutosave(slider));
        slider.addEventListener("change", () => {
            syncTimerInput();
            scheduleAutosave(slider);
        });
    });

    document.querySelectorAll("[data-timer-input]").forEach((input) => {
        input.addEventListener("input", () => {
            const slider = document.querySelector(`[data-timer-key="${input.dataset.timerInput}"]`);
            if (slider) {
                slider.value = input.value;
                setSliderVisual(slider);
            }
            scheduleAutosave(input);
        });
    });

    document.querySelectorAll("[data-reset-section]").forEach((button) => {
        button.addEventListener("click", () => {
            const section = button.dataset.resetSection;
            resetSectionSettings(section);
        });
    });
}

function setSliderVisual(slider) {
    const min = Number(slider.min || 0);
    const max = Number(slider.max || 100);
    const value = Number(slider.value || min);
    const percent = max === min ? 0 : ((value - min) / (max - min)) * 100;
    slider.style.background = `linear-gradient(90deg, rgba(255,42,68,1) 0%, rgba(255,112,137,1) ${percent}%, rgba(255,255,255,0.08) ${percent}%, rgba(255,255,255,0.08) 100%)`;
}

async function commitPlayerTagUpdate(tag) {
    clearTimeout(state.playerTagTimer);
    const cleanNew = cleanPlayerTag(tag);
    const cleanSaved = cleanPlayerTag(state.bootstrap.settings.general.player_tag || "");
    const tagChanged = cleanNew !== cleanSaved;
    const previousLookupFailed = state.playerInfo.ok === false;

    if (!tagChanged && !previousLookupFailed) return;

    await updatePlayerTag(formatPlayerTagInput(tag));
}

async function updatePlayerTag(tag) {
    setPlayerTagLoading(true);
    try {
        const saved = await fetchJSON("/api/settings/general", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ player_tag: tag }),
        });

        state.bootstrap.settings.general = { ...state.bootstrap.settings.general, ...saved };
        await refreshPlayerInfo(tag, true);
        renderSettings();
    } finally {
        setPlayerTagLoading(false);
    }
}

function setPlayerTagLoading(isLoading) {
    state.playerTagLoading = isLoading;

    const pill = document.querySelector(".player-pill");
    if (pill) {
        const pillState = getPlayerPillState();
        pill.className = `player-pill ${pillState.className}`;
        const spinnerHtml = pillState.className === "is-loading" ? '<div class="player-pill-spinner"></div>' : '';
        pill.innerHTML = `${spinnerHtml}<strong>${escapeHtml(pillState.title)}</strong><span>${escapeHtml(pillState.detail)}</span>`;
    }

    const tagInput = document.getElementById("playerTagInput");
    if (tagInput) {
        tagInput.disabled = isLoading;
        tagInput.closest(".input-group")?.classList.toggle("is-loading-input", isLoading);
    }
}

async function refreshPlayerInfo(tag, notify) {
    const cleanTag = cleanPlayerTag(tag);
    if (!cleanTag) {
        state.playerInfo = { ok: true, player_tag: "", player_name: "", stats: {} };
        renderQueue();
        return;
    }

    const result = await fetchJSON(`/api/player-info?tag=${encodeURIComponent(formatPlayerTagInput(cleanTag))}`, {}, true);
    if (!result.ok) {
        state.playerInfo = { ok: false, player_tag: cleanTag, player_name: "", stats: {}, message: result.message || INVALID_PLAYER_TAG_MESSAGE };
        renderQueue();
        if (notify) {
            showToast(result.message || INVALID_PLAYER_TAG_MESSAGE, "error");
        }
        return;
    }

    state.playerInfo = result;
    renderQueue();
    if (notify) {
        showToast(`Player data synced for ${result.player_name || result.player_tag}.`, "success");
    }
}

async function saveQueueItem() {
    const existing = findExistingQueueItem(state.selectedBrawler);
    const liveStats = getLiveBrawlerStats(state.selectedBrawler);
    const payload = {
        brawler: state.selectedBrawler,
        type: state.queueTargetType,
        push_until: Number(document.getElementById("queuePushUntil")?.value || 0),
        trophies: Number(document.getElementById("queueTrophies")?.value || liveStats.trophies || existing?.trophies || 0),
        wins: Number(document.getElementById("queueWins")?.value || existing?.wins || 0),
        win_streak: Number(document.getElementById("queueWinStreak")?.value || liveStats.win_streak || existing?.win_streak || 0),
        automatically_pick: document.getElementById("queueAutoPick")?.checked || false,
    };

    try {
        const result = await fetchJSON("/api/queue", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        state.bootstrap.queue = result.items;
        syncQueueFormState();
        renderDashboard();
        renderQueue(true);
        renderQueueDock();
        showToast(`${payload.brawler} saved to queue.`, "success");
    } catch (error) {
        showToast(error.message || `Unable to save ${payload.brawler} to queue.`, "error");
    }
}

async function pushAllToDefaultTarget() {
    const result = await fetchJSON("/api/queue/push-all-to-target", { method: "POST" }, true);
    if (!result.ok) {
        showToast(result.message || "Unable to push brawlers to target.", "error");
        return;
    }

    state.bootstrap.queue = result.items || [];
    syncQueueFormState();
    renderDashboard();
    renderQueue(true);
    renderQueueDock();
    showToast(`${result.added_count || 0} brawler${result.added_count === 1 ? "" : "s"} below target queued.`, "success");
}

async function savePlayOrder(playOrder) {
    const saved = await fetchJSON("/api/settings/general", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ play_order: playOrder }),
    });

    state.bootstrap.settings.general = { ...state.bootstrap.settings.general, ...saved };
    if (playOrder !== "in_order") {
        const queueResult = await fetchJSON("/api/queue", {}, true);
        if (queueResult.items) {
            state.bootstrap.queue = queueResult.items;
            syncQueueFormState();
            renderDashboard();
            if (state.currentView === "queue") {
                renderQueue(true);
            }
            renderQueueDock();
        }
    }
    renderSettings();
}

function scheduleAutosave(input) {
    const section = input.dataset.settingSection;
    if (!section) return;

    clearTimeout(state.pendingSaves[section]);
    state.pendingSaves[section] = setTimeout(() => {
        autosaveSection(section).catch((error) => showToast(error.message || `${section} settings failed to save.`, "error"));
    }, 280);
}

async function autosaveSection(section) {
    const payload = collectSectionPayload(section);
    const result = await fetchJSON(`/api/settings/${section}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    }, true);

    if (!result || result.ok === false) {
        showToast(result?.message || `${section} settings failed to save.`, "error");
        return;
    }

    state.bootstrap.settings[section] = result;

    if (section === "general") {
        await refreshPlayerInfo(result.player_tag || "", false);
    }

    renderSettings();
}

async function resetSectionSettings(section) {
    try {
        const result = await fetchJSON(`/api/settings/${section}/reset`, {
            method: "POST"
        });

        state.bootstrap.settings[section] = result;

        if (section === "general") {
            await refreshPlayerInfo(result.player_tag || "", false);
        }

        renderSettings();
        showToast(`${section.charAt(0).toUpperCase() + section.slice(1)} settings reset to defaults.`, "success");
    } catch (error) {
        showToast(error.message || `Failed to reset ${section} settings.`, "error");
    }
}

function collectSectionPayload(section) {
    const payload = {};

    document.querySelectorAll(`[data-setting-section="${section}"]`).forEach((input) => {
        const key = input.dataset.settingKey;
        if (!key) return;
        payload[key] = input.type === "checkbox" ? input.checked : input.value;
        if (key === "player_tag") {
            payload[key] = formatPlayerTagInput(input.value);
        }
    });

    if (section === "debug" && payload.debug_view === false) {
        payload.advanced_debug_visuals = false;
        payload.record_debug_preview_clips = false;
    }

    return payload;
}

async function handlePlaystyleImport(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/playstyles/import", { method: "POST", body: formData });
    const result = await response.json();

    if (!response.ok || !result.ok) {
        showToast(result.message || "Playstyle import failed.", "error");
        return;
    }

    state.bootstrap.playstyles = result.playstyles;
    renderDashboard();
    renderPlaystyles();
    showToast(`${result.filename} imported.`, "success");
    event.target.value = "";
}

async function handleQueueImport(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/queue/import", { method: "POST", body: formData });
    const result = await response.json().catch(() => ({}));

    if (!response.ok || !result.ok) {
        showToast(result.message || "Queue import failed.", "error");
        event.target.value = "";
        return;
    }

    state.bootstrap.queue = result.items || [];
    if (state.bootstrap.queue[0]?.brawler) {
        state.selectedBrawler = state.bootstrap.queue[0].brawler;
    }

    syncQueueFormState();
    renderDashboard();
    renderQueue(true);
    renderQueueDock();
    showToast(`${state.bootstrap.queue.length} queue item${state.bootstrap.queue.length === 1 ? "" : "s"} loaded.`, "success");
    event.target.value = "";
}

function syncQueueFormState() {
    const existing = findExistingQueueItem(state.selectedBrawler);
    state.queueTargetType = existing?.type || state.queueTargetType || "trophies";
}

function findExistingQueueItem(brawlerName) {
    return state.bootstrap.queue.find((item) => item.brawler === brawlerName);
}

function selectBrawlerFromQueue(brawlerName) {
    const catalogEntry = state.bootstrap.brawlers.find((item) => item.name.toLowerCase() === String(brawlerName).toLowerCase());
    state.selectedBrawler = catalogEntry?.name || brawlerName;
    syncQueueFormState();
    setView("queue");
    renderQueue(true);
    requestAnimationFrame(() => {
        document.querySelector(`[data-brawler="${cssEscape(state.selectedBrawler)}"]`)?.scrollIntoView({ block: "center", inline: "nearest" });
    });
}

function getLiveBrawlerStats(brawlerName) {
    return state.playerInfo.stats[brawlerName] || {};
}

function getActivePlaystyle() {
    return state.bootstrap.playstyles.current || state.bootstrap.playstyles.items?.find((item) => item.is_active) || state.bootstrap.playstyles.items?.[0] || null;
}

function metaLine(item) {
    if (!item) return "No metadata";

    const parts = [];
    if (item.author) parts.push(item.author);
    if (item.date) parts.push(item.date);
    return parts.join(" | ") || "Unknown";
}

function matchesPlaystyleFilters(item) {
    const search = state.playstyleSearch.trim().toLowerCase();
    const searchParts = [
        item.name,
        item.author,
        item.description,
        ...(item.brawlers || []),
        ...((item.gamemodes || []).map((mode) => GAMEMODE_LABELS[mode] || mode)),
    ].join(" ").toLowerCase();

    const searchMatch = !search || searchParts.includes(search);
    return searchMatch;
}

function queueTooltip(item) {
    return `<strong>${escapeHtml(item.brawler)}</strong><br>${escapeHtml(item.current_label)}: ${item.current_value}<br>${escapeHtml(item.target_label)}: ${item.push_until}<br>Auto Pick: ${item.automatically_pick ? "On" : "Off"}`;
}

function renderAuthMessage(result, variant = "error") {
    const message = document.getElementById("authMessage");
    if (!message) return;

    if (!result?.message && !result?.code) {
        message.className = "auth-message hidden";
        message.innerHTML = "";
        return;
    }

    const copy = AUTH_ERROR_COPY[result.code] || {};
    const title = copy.title || (variant === "info" ? "Authentication check" : "Login failed");
    const detail = copy.detail || result.message || "Try again. If it keeps failing, check the Python logs for the auth code.";
    const meta = authMetaLine(result);

    message.className = `auth-message ${variant === "info" ? "info" : ""}`;
    message.innerHTML = `
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(detail)}</span>
        ${meta ? `<span class="meta-line">${escapeHtml(meta)}</span>` : ""}
    `;
}

function authMetaLine(result) {
    if (!result) return "";
    const parts = [];
    if (result.code) parts.push(`Code: ${result.code}`);
    if (result.detected_version) parts.push(`Detected: ${result.detected_version}`);
    if (result.max_version) parts.push(`Allowed: ${result.max_version}`);
    return parts.join(" | ");
}

function formatAuthToast(result) {
    const copy = AUTH_ERROR_COPY[result?.code];
    return copy?.title || result?.message || "Login failed.";
}

function sortHistoryItems(a, b) {
    if (state.historySort === "winrate") return b.win_rate - a.win_rate || b.total_matches - a.total_matches;
    if (state.historySort === "recent") return String(b.last_played_sort || "").localeCompare(String(a.last_played_sort || "")) || b.total_matches - a.total_matches;
    if (state.historySort === "name") return a.brawler.localeCompare(b.brawler);
    return b.total_matches - a.total_matches || b.win_rate - a.win_rate;
}

function formatPercent(value) {
    return `${Math.round(Number(value) || 0)}%`;
}

function formatSignedNumber(value) {
    const number = Math.round(Number(value) || 0);
    return `${number >= 0 ? "+" : ""}${number}`;
}

async function fetchJSON(url, options = {}, allowFailure = false) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok && !allowFailure) {
        throw new Error(payload.message || `Request failed for ${url}`);
    }

    return payload;
}

// The trace keeps the last sixty readings and redraws them as one polyline.
// Sixty is what fits across the header at one sample per pixel-ish; older
// than that is history, and history has its own page.
const IPS_HISTORY = 60;
const ipsSamples = [];

function pushIpsSample(value) {
    const path = document.getElementById("ipsTracePath");
    const readout = document.getElementById("ipsValue");
    if (!path || !readout) return;

    const running = Number.isFinite(value) && value > 0;
    ipsSamples.push(running ? value : 0);
    while (ipsSamples.length > IPS_HISTORY) ipsSamples.shift();

    // Scaled against the highest reading in the window rather than a fixed
    // ceiling, so the shape stays readable whatever the machine manages.
    const peak = Math.max(20, ...ipsSamples);
    const step = 600 / Math.max(1, IPS_HISTORY - 1);
    path.setAttribute("points", ipsSamples
        .map((v, i) => `${(i * step).toFixed(1)},${(24 - (v / peak) * 22).toFixed(1)}`)
        .join(" "));
    path.classList.toggle("is-live", running);

    readout.textContent = running ? value.toFixed(1) : "——";
    readout.classList.toggle("is-live", running);
}

function showToast(message, variant = "success") {
    const toast = document.getElementById("toast");
    if (!toast) return;

    toast.textContent = message;
    toast.className = `toast ${variant}`;
    toast.classList.remove("hidden");

    clearTimeout(showToast.timeoutId);
    showToast.timeoutId = setTimeout(() => toast.classList.add("hidden"), 2600);
}

function iconMarkup(name) {
    const S = `viewBox="0 0 24 24" aria-hidden="true"`;
    const icons = {
        dashboard:  `<svg ${S}><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>`,
        queue:      `<svg ${S}><path d="M3 5h.01"/><path d="M3 12h.01"/><path d="M3 19h.01"/><path d="M8 5h13"/><path d="M8 12h13"/><path d="M8 19h13"/></svg>`,
        playstyles: `<svg ${S}><rect width="18" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/></svg>`,
        history:    `<svg ${S}><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>`,
        settings:   `<svg ${S}><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"/><circle cx="12" cy="12" r="3"/></svg>`,
        play:       `<svg ${S}><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/></svg>`,
        pause:      `<svg ${S}><rect x="14" y="3" width="5" height="18" rx="1"/><rect x="5" y="3" width="5" height="18" rx="1"/></svg>`,
        stop:       `<svg ${S}><rect width="18" height="18" x="3" y="3" rx="2"/></svg>`,
        import:     `<svg ${S}><path d="M12 3v12"/><path d="m17 8-5-5-5 5"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/></svg>`,
        close:      `<svg ${S}><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`,
        logs:       `<svg ${S}><path d="M12 19h8"/><path d="m4 17 6-6-6-6"/></svg>`,
        copy:       `<svg ${S}><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>`,
        // Filled marks for the community rows: these read as logos rather than
        // line icons, so they get their own fill rule in the stylesheet.
        telegram:   `<svg ${S} class="icon-filled"><path d="M21.9 4.3 18.6 20c-.2 1.1-.9 1.4-1.8.9l-5-3.7-2.4 2.3c-.3.3-.5.5-1 .5l.4-5.1 9.3-8.4c.4-.4-.1-.6-.6-.2L6.1 13.1l-4.9-1.5c-1.1-.3-1.1-1 .2-1.5l19.2-7.4c.9-.3 1.7.2 1.4 1.6z"/></svg>`,
        github:     `<svg ${S} class="icon-filled"><path d="M12 2A10 10 0 0 0 8.84 21.5c.5.08.66-.23.66-.5v-1.7C6.73 19.91 6.14 18 6.14 18A2.7 2.7 0 0 0 5 16.5c-.91-.62.07-.6.07-.6a2.1 2.1 0 0 1 1.53 1.03 2.15 2.15 0 0 0 2.91.83c.05-.5.25-.83.45-1.02-2.55-.29-5.23-1.27-5.23-5.68a4.45 4.45 0 0 1 1.18-3.08 4.14 4.14 0 0 1 .11-3.04s.97-.31 3.18 1.18a10.9 10.9 0 0 1 5.79 0c2.2-1.49 3.17-1.18 3.17-1.18a4.14 4.14 0 0 1 .12 3.04 4.44 4.44 0 0 1 1.18 3.08c0 4.42-2.69 5.39-5.25 5.67.28.24.52.68.52 1.38v2.04c0 .27.16.59.67.5A10 10 0 0 0 12 2z"/></svg>`,
        link:       `<svg ${S}><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
    };

    return icons[name] || "";
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function cssEscape(value) {
    if (window.CSS?.escape) return CSS.escape(String(value));
    return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

// Nothing in this fork is paid, so nothing sells anything.
//
// What used to live here was a modal selling a paid tier, with a link to a
// Discord channel to buy it in. It was wrong twice over: this fork has no paid
// tier, and the thing it appeared for was not a paid feature anyway. The
// two places that raised it are gated on whether a *free* Brawl Stars API
// token has been entered, which is a five-minute job in Settings, so that is
// what they say now and where they take you.
function goToApiTokenSetting() {
    setView("settings");
    state.settingsTab = "general";
    renderSettings();
    showToast("Add a free Brawl Stars API token here to sync live player stats.", "info");
    // Land on the field rather than at the top of a long page.
    requestAnimationFrame(() => {
        const field = document.querySelector('[data-setting-key="brawl_api_token"]');
        field?.closest(".setting-row")?.scrollIntoView({behavior: "smooth", block: "center"});
        field?.focus({preventScroll: true});
    });
}
