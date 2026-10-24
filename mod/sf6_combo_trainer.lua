-- SF6 連段練習器的遊戲內腳本，放在 reframework\autorun\ 裡由 REFramework 載入。
--
-- 做兩件事：
-- 1. 訓練模式中把雙方的連擊數寫到 reframework\data\SF6ComboTrainer_state.json，
--    練習器讀這個檔案，判斷連段是不是真的打中、有沒有中途斷掉。
-- 2. 只在載入畫面、以及開配對的視窗（Battle Setting）時，
--    畫面上顯示跟練習器同風格的紙卡提醒：對戰前要關掉 mod。其他畫面都不顯示。
-- 3. 練習器要求時（連段斷了自動重置），在訓練模式重置一次（一般重置，或播放儲存狀態）。
--    練習器把要求寫到 reframework\data\SF6ComboTrainer_cmd.json，這裡每 6 幀看一次。
-- 4. 除錯用（平常關閉）：記錄遊戲畫面（GUI）的內部名稱到 SF6ComboTrainer_gui.json。
--
-- 除了第 3 項的訓練模式重置（播放儲存狀態時會讓遊戲以為 LT 被按了幾幀），只讀遊戲資料，不改遊戲裡的其他東西。
-- 讀取的欄位與重置做法參考 SF6_Tools／SF6CC（MIT License）。

local SCRIPT_VERSION = 12
local OUT_FILE = "SF6ComboTrainer_state.json"
local HEARTBEAT_FRAMES = 30          -- 沒變化時每 30 幀（約 0.5 秒）也寫一次，讓練習器知道遊戲還開著
local ONLINE_MODES = { [14] = true, [15] = true, [16] = true, [17] = true, [18] = true }
local FLOW_BATTLE_HUB = 9
local FLOW_REPLAY = 10

local sdk, re, json, imgui = sdk, re, json, imgui

local td_battle = sdk.find_type_definition("gBattle")
local f_player = td_battle and td_battle:get_field("Player")

local frame, seq, last_write = 0, 0, -100000
local last = { p1 = -1, p2 = -1, training = nil, online = nil, flow = nil }
local training_cache, training_left = false, 0

local function read_training()
    -- 訓練模式的資料存在時才算在訓練模式（每 30 幀查一次就好）
    training_left = training_left - 1
    if training_left > 0 then return training_cache end
    training_left = 30
    local ok, value = pcall(function()
        local tm = sdk.get_managed_singleton("app.training.TrainingManager")
        return tm ~= nil and tm:get_field("_tData") ~= nil
    end)
    training_cache = ok and value == true
    return training_cache
end

local function read_flow()
    local ok, id = pcall(function()
        local bfm = sdk.get_managed_singleton("app.bFlowManager")
        local work = bfm and bfm:get_field("m_flow_work")
        return work and work._FlowMap and work._FlowMap._ID
    end)
    if ok and id then return tonumber(tostring(id)) end
    return nil
end

local function read_online()
    local ok, mode = pcall(function()
        local network = sdk.get_managed_singleton("app.network.NetworkManager")
        local session = network and network:get_field("<Session>k__BackingField")
        local fg = session and session:get_field("<FGBattle>k__BackingField")
        local rule = fg and fg:get_field("<BattleRule>k__BackingField")
        return rule and rule:get_field("GameMode")
    end)
    if not ok or mode == nil then return false end
    return ONLINE_MODES[tonumber(tostring(mode))] == true
end

local function read_combo(p)
    if not p then return nil end
    local v = p:get_field("combo_cnt")
    return v and tonumber(tostring(v)) or nil
end

local function read_counts()
    local ok, c1, c2 = pcall(function()
        if not f_player then return nil, nil end
        local sp = f_player:get_data(nil)
        if not sp or not sp.mcPlayer then return nil, nil end
        return read_combo(sp.mcPlayer[0]), read_combo(sp.mcPlayer[1])
    end)
    if not ok then return nil, nil end
    return c1, c2
end

local function write_state(state)
    seq = seq + 1
    state.seq = seq
    state.frame = frame
    state.version = SCRIPT_VERSION
    pcall(json.dump_file, OUT_FILE, state)
    last_write = frame
end

-- ---- 認出遊戲畫面（GUI） ----
-- Battle Setting 畫面的內部名稱（用記錄功能在遊戲裡確認過：打開時出現、關掉就消失）
local BATTLE_SETTING_GUI = { "MatchingSetting", "MatchingFighterSetting", "OtherMatchingFighterSetting" }
local GUI_LOG = false                -- 要再找其他畫面的名稱時改成 true，會記錄到 SF6ComboTrainer_gui.json
local GUI_LOG_FILE = "SF6ComboTrainer_gui.json"
local gui_drawing, gui_now = {}, {}
local gui_known, gui_events, gui_dirty, gui_last_write = {}, {}, false, -100000

-- 載入畫面：LoadingUI 一直都在畫，所以要另外看它現在是不是真的看得到
local LOADING_GUI = "LoadingUI"
local LOADING_LOG_FILE = "SF6ComboTrainer_loading.json"
local loading_drawing, loading_now = nil, nil      -- 這一幀／上一幀 LoadingUI 的狀態
local loading_log, loading_sig = {}, nil

local function loading_flags(element, go)
    local f = {}
    pcall(function() local v = element:call("get_View"); f.view = v and v:call("get_Visible") end)
    pcall(function() f.draw = go:call("get_DrawSelf") end)
    pcall(function() f.enabled = element:call("get_Enabled") end)
    return f
end

re.on_pre_gui_draw_element(function(element, context)
    pcall(function()
        local go = element:call("get_GameObject")
        local name = go and go:call("get_Name")
        if name then
            gui_drawing[name] = true
            if name == LOADING_GUI then loading_drawing = loading_flags(element, go) end
        end
    end)
    return true
end)

local gui_flips = {}                 -- 每個畫面出現／消失過幾次（太常切換的不記，免得檔案爆掉）

local function read_pause_bit()
    local ok, v = pcall(function()
        local pm = sdk.get_managed_singleton("app.PauseManager")
        return pm and pm:get_field("_CurrentPauseTypeBit")
    end)
    return ok and v and tonumber(tostring(v)) or -1
end

local last_pause = nil
local function log_event(e)
    if #gui_events < 3000 then
        gui_events[#gui_events + 1] = e
        gui_dirty = true
    end
end

local function update_gui(training)
    local prev = gui_now
    gui_now, gui_drawing = gui_drawing, {}
    loading_now, loading_drawing = loading_drawing, nil
    -- 記下 LoadingUI 狀態的變化（確認哪個欄位代表「看得到」，最多 200 筆）
    local f = loading_now or {}
    local sig = tostring(loading_now ~= nil) .. tostring(f.view) .. tostring(f.draw) .. tostring(f.enabled)
    if sig ~= loading_sig and #loading_log < 200 then
        loading_sig = sig
        loading_log[#loading_log + 1] = { frame = frame, drawn = loading_now ~= nil, view = f.view, draw = f.draw,
                                          enabled = f.enabled, training = training }
        pcall(json.dump_file, LOADING_LOG_FILE, loading_log)
    end
    if not GUI_LOG then return end
    -- 記錄畫面出現（on=true）和消失（on=false），用來確認 Battle Setting 關掉後會不會消失
    for name in pairs(gui_now) do
        if not prev[name] then
            gui_flips[name] = (gui_flips[name] or 0) + 1
            if gui_flips[name] <= 20 then log_event({ frame = frame, name = name, on = true, training = training }) end
        end
    end
    for name in pairs(prev) do
        if not gui_now[name] then
            gui_flips[name] = (gui_flips[name] or 0) + 1
            if gui_flips[name] <= 20 then log_event({ frame = frame, name = name, on = false, training = training }) end
        end
    end
    local pause = read_pause_bit()
    if pause ~= last_pause then
        last_pause = pause
        log_event({ frame = frame, name = "(pause_bit)", value = pause, training = training })
    end
    if gui_dirty and frame - gui_last_write >= 30 then
        local current = {}
        for name in pairs(gui_now) do current[#current + 1] = name end
        table.sort(current)
        pcall(json.dump_file, GUI_LOG_FILE, { frame = frame, current = current, events = gui_events })
        gui_dirty, gui_last_write = false, frame
    end
end

local function loading_visible()
    local f = loading_now
    return f ~= nil and f.view == true and f.draw ~= false and f.enabled ~= false
end

local function battle_setting_open()
    for _, name in ipairs(BATTLE_SETTING_GUI) do
        if gui_now[name] then return true end
    end
    return false
end

-- ---- 提醒紙卡：跟練習器同一種風格（白紙、黑色手繪粗框、紅色螢光筆底線、手寫字） ----
-- 顏色是 0xAABBGGRR；紙、影子、螢光筆都半透明，看得到後面遊戲的字；手繪框和提醒字不透明，才看得清楚
local PAPER, INK, SHADOW, MARKER = 0x73F7FAFA, 0xFF1E1E1E, 0x22000000, 0x806070E0
local OUTLINE = 0xB0F7FAFA                                -- 提醒字外圍一圈白邊，背景很花時也讀得到
-- 視窗旗標：沒標題、不能改大小、不能移動、沒捲軸、不能收合、沒有背景、不吃滑鼠、出現時不搶焦點
local TEXT_FLAGS = 1 | 2 | 4 | 8 | 32 | 128 | 512 | 4096
local FONT_FILES = { "LXGWWenKaiTC-Bold.ttf", "msyhbd.ttc", "msyh.ttc" }
local fonts, fonts_scale = nil, nil

local function load_fonts(scale)
    if fonts and fonts_scale == scale then return fonts end
    fonts_scale = scale
    fonts = { big = nil, small = nil }
    for _, name in ipairs(FONT_FILES) do
        local ok1, big = pcall(imgui.load_font, name, math.floor(40 * scale))
        local ok2, small = pcall(imgui.load_font, name, math.floor(26 * scale))
        if ok1 and big and ok2 and small then
            fonts.big, fonts.small = big, small
            break
        end
    end
    return fonts
end

-- 固定的「隨機」抖動，畫面每一幀都一樣，線才不會閃
local function jitter(i, amp)
    local v = math.sin(i * 12.9898 + 78.233) * 43758.5453
    return (v - math.floor(v) - 0.5) * 2 * amp
end

local function thick_line(x1, y1, x2, y2, w, col)
    local dx, dy = x2 - x1, y2 - y1
    local len = math.sqrt(dx * dx + dy * dy)
    if len < 0.001 then return end
    local nx, ny = -dy / len * w / 2, dx / len * w / 2
    draw.filled_quad(x1 + nx, y1 + ny, x2 + nx, y2 + ny, x2 - nx, y2 - ny, x1 - nx, y1 - ny, col)
end

-- 手繪的邊：分成幾段，每段端點稍微抖一下
local function wobbly_side(x1, y1, x2, y2, w, seed, amp)
    local steps = math.max(2, math.floor(math.max(math.abs(x2 - x1), math.abs(y2 - y1)) / 60))
    local px, py = x1, y1
    for i = 1, steps do
        local t = i / steps
        local qx, qy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        if i < steps then
            qx, qy = qx + jitter(seed + i, amp), qy + jitter(seed + i + 50, amp)
        end
        thick_line(px, py, qx, qy, w, INK)
        px, py = qx, qy
    end
end

local function draw_card(x, y, w, h, scale)
    local s = scale
    draw.filled_rect(x + 6 * s, y + 7 * s, w, h, SHADOW)          -- 右下的影子
    draw.filled_rect(x, y, w, h, PAPER)
    local lw, amp = 3.2 * s, 1.6 * s
    wobbly_side(x, y, x + w, y + 1 * s, lw, 1, amp)
    wobbly_side(x + w, y, x + w - 1 * s, y + h, lw, 21, amp)
    wobbly_side(x + w, y + h, x, y + h - 1 * s, lw, 41, amp)
    wobbly_side(x, y + h, x + 1 * s, y, lw, 61, amp)
end

local function draw_marker(x, y, w, h, seed)
    -- 螢光筆：兩三筆稍微歪斜的紅色粗線，蓋在字的下半部
    for i = 0, 1 do
        local yy = y + h * (0.58 + i * 0.16)
        local tilt = jitter(seed + i, h * 0.05)
        thick_line(x - h * 0.12, yy + tilt, x + w + h * 0.12, yy - tilt, h * 0.26, MARKER)
    end
end

local function draw_warning(lines, center_x)
    local sw, sh = 1920, 1080
    pcall(function()
        local r = imgui.get_display_size()
        sw, sh = r.x, r.y
    end)
    local scale = math.max(0.6, sh / 1080)
    local f = load_fonts(scale)
    if not f.big then return end

    local pad_x, pad_y, gap = 34 * scale, 20 * scale, 8 * scale
    imgui.push_font(f.big)
    local ok1, s1 = pcall(imgui.calc_text_size, lines[1])
    imgui.pop_font()
    imgui.push_font(f.small)
    local ok2, s2 = pcall(imgui.calc_text_size, lines[2])
    imgui.pop_font()
    if not (ok1 and ok2 and s1 and s2) then return end
    local w = math.max(s1.x, s2.x) + pad_x * 2
    local h = s1.y + gap + s2.y + pad_y * 2
    -- 放在畫面上方，不擋住選單分頁；Battle Setting 時往右移，不擋住左邊的設定欄
    local x = math.max(8, math.min(sw - w - 8, sw * (center_x or 0.5) - w / 2))
    local y = sh * 0.15

    draw_card(x, y, w, h, scale)
    local t1x = x + (w - s1.x) / 2
    draw_marker(t1x, y + pad_y, s1.x, s1.y, 7)

    -- 先畫一圈白邊再畫黑字（白邊錯開 1.5 倍縮放的距離）
    local function outlined(text, cx, cy)
        local o = 1.5 * scale
        for _, d in ipairs({ { -o, 0 }, { o, 0 }, { 0, -o }, { 0, o } }) do
            imgui.set_cursor_pos(Vector2f.new(cx + d[1], cy + d[2]))
            imgui.text_colored(text, OUTLINE)
        end
        imgui.set_cursor_pos(Vector2f.new(cx, cy))
        imgui.text_colored(text, INK)
    end

    imgui.set_next_window_pos(Vector2f.new(x, y))
    imgui.set_next_window_size(Vector2f.new(w, h))
    imgui.push_style_var(2, Vector2f.new(0, 0))           -- 視窗內距
    imgui.push_style_var(4, 0.0)                          -- 視窗邊框
    -- 字型推了一定要拿掉、視窗開了一定要關、樣式推了一定要還原，中間出錯也一樣，不然遊戲畫面會壞掉
    local ok_win, opened = pcall(imgui.begin_window, "SF6ComboTrainer_warning", true, TEXT_FLAGS)
    if ok_win and opened then
        imgui.push_font(f.big)
        pcall(outlined, lines[1], t1x - x, pad_y)
        imgui.pop_font()
        imgui.push_font(f.small)
        pcall(outlined, lines[2], (w - s2.x) / 2, pad_y + s1.y + gap)
        imgui.pop_font()
        imgui.end_window()                                -- REFramework 的寫法：開成功才關
    end
    imgui.pop_style_var(2)
end

local MENU_LINES = { "MOD 還開著！", "線上對戰前，請先關遊戲，在練習器把 mod 關掉" }
local SETTING_LINES = { "MOD 還開著！", "要開排名賽／友誼賽前，請先關遊戲，在練習器把 mod 關掉" }

-- ---- 除錯用：找訓練模式「播放儲存狀態」是哪個遊戲函式（找到後把 STATE_PROBE 關掉） ----
local STATE_PROBE = false
local PROBE_FILE = "SF6ComboTrainer_probe.json"
local probe = { types = {}, methods = {}, calls = {}, count = {}, errors = {} }
local probe_dirty, probe_last = false, -100000

-- 第二輪：第一輪找到 SnapShoted、TrainingSnapShot、GetLoadSlotID，這次把這幾個型別的函式全部記下來
local PROBE_TYPES = { "app.training.TrainingManager", "app.training.TrainingCommonFunc",
                      "app.training.TrainingCommonFunc.TrainingSnapShot", "app.training.TrainingData.TM_OtherSetting" }

local function probe_scan()
    local hooked = 0
    probe.fields = {}
    for _, tname in ipairs(PROBE_TYPES) do
        local td = sdk.find_type_definition(tname)
        if td then
            probe.types[#probe.types + 1] = tname
            pcall(function()
                for _, f in ipairs(td:get_fields()) do
                    local ft = f:get_type()
                    probe.fields[#probe.fields + 1] = tname .. "." .. f:get_name() .. " : " .. (ft and ft:get_full_name() or "?")
                end
            end)
            pcall(function()
                for _, m in ipairs(td:get_methods()) do
                    local mname = m:get_name()
                    -- get_ 開頭的是讀值，每幀都在跑，不用看
                    if string.sub(mname, 1, 4) ~= "get_" and string.sub(mname, 1, 1) ~= "." and hooked < 600 then
                        local full = tname .. "." .. mname
                        probe.methods[#probe.methods + 1] = full
                        local ok, err = pcall(sdk.hook, m, function()
                            -- 每個函式只記前 30 次（每幀都跑的函式才不會把檔案塞爆），總數也算
                            local n = (probe.count[full] or 0) + 1
                            probe.count[full] = n
                            if n <= 30 then
                                probe.calls[#probe.calls + 1] = { frame = frame, name = full }
                                probe_dirty = true
                            end
                        end, nil)
                        if ok then hooked = hooked + 1 else probe.errors[#probe.errors + 1] = full .. ": " .. tostring(err) end
                    end
                end
            end)
        end
    end
    probe_dirty = true
end

if STATE_PROBE then pcall(probe_scan) end

local function probe_write()
    if not STATE_PROBE or not probe_dirty or frame - probe_last < 60 then return end
    pcall(json.dump_file, PROBE_FILE, { frame = frame, types = probe.types, methods = probe.methods,
                                        calls = probe.calls, counts = probe.count, errors = probe.errors,
                                        fields = probe.fields })
    probe_dirty, probe_last = false, frame
end

-- ---- 練習器要求重置 ----
local CMD_FILE = "SF6ComboTrainer_cmd.json"
local CMD_EVERY = 6
local last_cmd = nil                 -- 第一次讀到的要求只當起點，不執行（避免遊戲一開就重置）

local our_refresh_frame = -100000

local function request_reset()
    pcall(function()
        local tm = sdk.get_managed_singleton("app.training.TrainingManager")
        if tm and tm:get_field("_tData") then
            tm._IsReqRefresh = true
            our_refresh_frame = frame
        end
    end)
end

-- ---- 播放儲存狀態：模擬按一下 LT ----
-- 實測確認：直接呼叫 FromStorage 拿不到玩家存的那一份，所以改成讓遊戲以為
-- 手把的 LT（玩家設定的「播放儲存狀態」快捷鍵）被按了幾幀，走跟玩家自己按完全一樣的路。
-- 做法：攔截 via.hid.GamePadDevice 讀按鍵的函式，在回傳值加上 LT。
-- 玩家自己按快捷鍵播放時，順便記下當時按著的鍵（快捷鍵若是 FUNC＋LT 這類組合也學得到）。
-- 模擬後 20 幀內遊戲沒播放，就改呼叫 requestLoadState，再不行才退回一般重置。
local STATE_DIAG_FILE = "SF6ComboTrainer_reset.json"
local PAD_LT = 2048                  -- SF6_Tools 實測的 LT 位元
local PAD_LEARN_MASK = 0xFE10        -- 學快捷鍵時不算方向鍵和攻擊鍵
local INJECT_FRAMES = 4
local CHECK_FRAMES = 20

local state_diag = { calls = 0, log = {}, hooks = {}, hook_calls = {}, learned = nil, last = nil }
local inject_left, inject_first = 0, false
local pending = nil                  -- { stage = "lt"|"req", deadline = 幀 }
local our_until = -1                 -- 這一幀以前的 FromStorage 是我們觸發的
local last_fs_frame = -100000
local pad_hist = {}                  -- 最近幾幀實際按著的鍵（不含模擬的）
local req_load = nil

local function diag_write()
    pcall(json.dump_file, STATE_DIAG_FILE, state_diag)
end

local function inject_mask()
    return state_diag.learned or PAD_LT
end

-- 攔截讀按鍵的函式
pcall(function()
    local td = sdk.find_type_definition("via.hid.GamePadDevice")
    if not td then state_diag.hooks.error = "找不到 via.hid.GamePadDevice"; return end
    for _, name in ipairs({ "get_Button", "get_ButtonDown" }) do
        local m = td:get_method(name)
        if m then
            local down = name == "get_ButtonDown"
            local ok, err = pcall(sdk.hook, m, nil, function(retval)
                if inject_left <= 0 or (down and not inject_first) then return retval end
                state_diag.hook_calls[name] = (state_diag.hook_calls[name] or 0) + 1
                local v = sdk.to_int64(retval) or 0
                return sdk.to_ptr(v | inject_mask())
            end)
            state_diag.hooks[name] = ok and "ok" or tostring(err)
        else
            state_diag.hooks[name] = "沒有這個函式"
        end
    end
end)

-- 讀實際按著的鍵（模擬中不讀，免得學到自己加的 LT）
local function read_pad_mask()
    local ok, mask = pcall(function()
        local mgr = sdk.get_native_singleton("via.hid.GamePad")
        local td = sdk.find_type_definition("via.hid.GamePad")
        if not (mgr and td) then return 0 end
        local devices = sdk.call_native_func(mgr, td, "get_ConnectingDevices")
        if not devices then return 0 end
        local all = 0
        for i = 0, (devices:call("get_Count") or 0) - 1 do
            local pad = devices:call("get_Item", i)
            if pad then all = all | (pad:call("get_Button") or 0) end
        end
        return all
    end)
    return ok and (tonumber(mask) or 0) or 0
end

-- 遊戲播放儲存狀態時都會呼叫 FromStorage：用來確認模擬有沒有成功，也用來學玩家的快捷鍵
pcall(function()
    local td = sdk.find_type_definition("app.training.TrainingManager")
    req_load = td and td:get_method("requestLoadState")
    state_diag.hooks.requestLoadState = req_load and "有" or "沒有這個函式"
    local fs = td and td:get_method("FromStorage")
    if not fs then state_diag.hooks.FromStorage = "沒有這個函式"; return end
    sdk.hook(fs, function(args)
        state_diag.calls = state_diag.calls + 1
        last_fs_frame = frame
        local ours = frame <= our_until
        local pressed = ours and 0 or read_pad_mask()     -- 遊戲常在按下的同一幀就播放
        for _, m in ipairs(pad_hist) do pressed = pressed | m end
        pressed = pressed & PAD_LEARN_MASK
        -- 只學有 LT 的組合（別的快捷鍵觸發的重置不算）
        if not ours and pressed & PAD_LT ~= 0 then state_diag.learned = pressed end
        if #state_diag.log < 40 then
            state_diag.log[#state_diag.log + 1] = { frame = frame, ours = ours, pad = pressed }
        end
        diag_write()
    end, nil)
    state_diag.hooks.FromStorage = "ok"
end)

local function play_saved_state()
    inject_left, inject_first = INJECT_FRAMES, true
    our_until = frame + CHECK_FRAMES + 10
    pending = { stage = "lt", start = frame, deadline = frame + CHECK_FRAMES }
    state_diag.last = "模擬按鍵 " .. inject_mask() .. " @" .. frame
    diag_write()
end

-- 每幀：模擬倒數、記實際按鍵、檢查遊戲有沒有播放
local function state_tick(training)
    if inject_left > 0 then
        inject_left = inject_left - 1
        inject_first = false
    elseif training then
        table.insert(pad_hist, read_pad_mask())
        if #pad_hist > 6 then table.remove(pad_hist, 1) end
    end
    if not pending then return end
    if last_fs_frame >= pending.start then
        state_diag.last = (pending.stage == "lt" and "模擬按鍵成功" or "requestLoadState 成功") .. " @" .. frame
        pending = nil
        diag_write()
    elseif frame >= pending.deadline then
        if pending.stage == "lt" and req_load then
            local ok, err = pcall(function()
                local tm = sdk.get_managed_singleton("app.training.TrainingManager")
                if req_load:get_num_params() ~= 0 then error("requestLoadState 需要參數") end
                req_load:call(tm)
            end)
            if ok then
                our_until = frame + CHECK_FRAMES + 10
                pending = { stage = "req", start = frame, deadline = frame + CHECK_FRAMES }
                state_diag.last = "模擬按鍵沒反應，改試 requestLoadState @" .. frame
            else
                pending = nil
                state_diag.last = "模擬按鍵沒反應；" .. tostring(err) .. "；改用一般重置"
                request_reset()
            end
        else
            pending = nil
            state_diag.last = "沒有播放儲存狀態，改用一般重置 @" .. frame
            request_reset()
        end
        diag_write()
    end
end

diag_write()

local function check_cmd(training, flow)
    if frame % CMD_EVERY ~= 0 then return end
    local ok, cmd = pcall(json.load_file, CMD_FILE)
    local id = ok and type(cmd) == "table" and cmd.reset or nil
    if id == nil then
        if last_cmd == nil then last_cmd = false end
        return
    end
    if last_cmd == nil then last_cmd = id; return end
    if id == last_cmd then return end
    last_cmd = id
    if training and flow ~= FLOW_REPLAY then
        if cmd.mode == "state" then play_saved_state() else request_reset() end
    end
end

re.on_frame(function()
    frame = frame + 1
    local online = read_online()
    local flow = read_flow()
    local training = read_training() and not online and flow ~= FLOW_BATTLE_HUB
    update_gui(training)
    check_cmd(training, flow)
    state_tick(training)
    probe_write()

    local p1, p2 = nil, nil
    if training then p1, p2 = read_counts() end
    local changed = p1 ~= last.p1 or p2 ~= last.p2 or training ~= last.training
        or online ~= last.online or flow ~= last.flow
    if changed or frame - last_write >= HEARTBEAT_FRAMES then
        write_state({ training = training, online = online, flow = flow or -1,
                      p1 = p1 or 0, p2 = p2 or 0, counts_ok = p1 ~= nil })
        last.p1, last.p2, last.training, last.online, last.flow = p1, p2, training, online, flow
    end

    if loading_visible() then
        pcall(draw_warning, MENU_LINES)
    elseif battle_setting_open() and flow ~= FLOW_REPLAY then
        pcall(draw_warning, SETTING_LINES, 0.62)
    end
end)
