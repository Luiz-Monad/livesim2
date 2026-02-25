-- vlc_wait.lua
-- VLC Lua Interface
-- Waits until playback enters Playing state,
-- records for N seconds, then stops and quits VLC.

local wait_s = 20
local timeout_s = 60
local close_s = 1
local poll_ms = 500

vlc.msg.info("[vlc_wait] Interface loading...")

-- VLC passes interface config directly as `config`
if type(config) == "table" then
    vlc.msg.info("[vlc_wait] Raw config table detected")

    if config.poll_msec then
        poll_ms = tonumber(config.poll_msec) or poll_ms
    end

    if config.wait_sec then
        wait_s = tonumber(config.wait_sec) or wait_s
    end

    if config.timeout_sec then
        timeout_s = tonumber(config.timeout_sec) or timeout_s
    end

    if config.close_sec then
        close_s = tonumber(config.close_sec) or close_s
    end
else
    vlc.msg.warn("[vlc_wait] No config table detected, using defaults")
end

vlc.msg.info("[vlc_wait] Settings:")
vlc.msg.info("[vlc_wait]   poll_ms=" .. poll_ms)
vlc.msg.info("[vlc_wait]   wait_s=" .. wait_s)
vlc.msg.info("[vlc_wait]   timeout_s=" .. timeout_s)
vlc.msg.info("[vlc_wait]   close_s=" .. close_s)

local launch_time = os.time()
local start_time = nil
local start_pts = nil

while true do
    -- vlc.msg.dbg("[vlc_wait] pooling")

    local input = vlc.object.input()

    if input then
        local state = vlc.var.get(input, "state")
        local pts = vlc.var.get(input, "time")
        -- vlc.msg.dbg("[vlc_wait] state: " .. state)
        -- vlc.msg.dbg("[vlc_wait] pts: " .. pts)

        if state >= 2 then
            if not start_time then
                start_time = os.time()
                vlc.msg.info("[vlc_wait] Playback detected at " .. start_time)
            end
            if not start_pts and pts > 0 then
                start_pts = pts
                vlc.msg.info("[vlc_wait] PTS detected at " .. pts / 1000000)
            end
 
            local s_time = start_time
            if not s_time then
                s_time = 0
            end
            local elapsed = os.time() - s_time
            -- vlc.msg.dbg("[vlc_wait] elapsed clock: " .. elapsed .. "s")

            local s_pts = start_pts
            if not s_pts then
                s_pts = 0
            end
            local elapsed_pts = (pts - s_pts) / 1000000
            -- vlc.msg.dbg("[vlc_wait] elapsed media time: " .. elapsed_pts .. "s")

            if elapsed >= wait_s then
                vlc.msg.info("[vlc_wait] elapsed clock: " .. elapsed .. "s")
                vlc.msg.info("[vlc_wait] elapsed media time: " .. elapsed_pts .. "s")
                vlc.msg.info("[vlc_wait] Target media duration reached. Stopping.")
                vlc.playlist.stop()
                break
            end
        end
    else
        if (os.time() - launch_time) >= timeout_s then
            vlc.msg.warn("[vlc_wait] Timeout waiting for stream.")
            break
        end
    end

    vlc.misc.mwait(vlc.misc.mdate() + poll_ms * 1000)
end

vlc.misc.mwait(vlc.misc.mdate() + close_s * 1000000)
vlc.misc.quit()
