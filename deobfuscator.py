#!/usr/bin/env python3
import re
import sys
import subprocess
import time
import os
import glob
import math
import tempfile
import shutil
import functools

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine.trace_filter import TraceFilter, filter_trace_lines
from engine.static_decoder import StaticConstantDecoder, decode_prometheus_constants
from engine.ast_optimizer import AstOptimizer, optimize_lua_code
from engine.syntax_normalizer import normalize_luau_syntax, LuauSyntaxNormalizer
from engine.constant_inliner import ConstantInliner, inline_constants_in_code
import trace_to_lua


COMPOUND_ASSIGNMENT_OPERATORS = ("+=", "-=", "*=", "/=", "%=", "^=", "..=")
LUA_CONTROL_STRUCTURE_TOO_LONG = "control structure too long"


def check_platform():
    if not sys.platform.startswith("linux"):
        raise SystemExit(
            f"Error: Epimetheus is an exclusively Linux-native engine. "
            f"Only Linux is supported (current platform: {sys.platform})."
        )


@functools.lru_cache(maxsize=1)
def get_lua_executable():
    check_platform()

    env_path = os.environ.get("LUA51_EXECUTABLE")
    if env_path:
        if os.path.isfile(env_path) or shutil.which(env_path):
            return env_path

    base_dir = os.path.dirname(os.path.abspath(__file__))
    lua_bin_dir = os.path.join(base_dir, "lua_bin")

    candidates = [
        os.path.join(lua_bin_dir, "lua5.1"),
        os.path.join(lua_bin_dir, "lua"),
        os.path.join("lua_bin", "lua5.1"),
        os.path.join("lua_bin", "lua"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            if not os.access(candidate, os.X_OK):
                try:
                    os.chmod(candidate, os.stat(candidate).st_mode | 0o111)
                except Exception:
                    pass
            return os.path.abspath(candidate)

    for candidate in ("lua5.1", "lua51", "lua"):
        path = shutil.which(candidate)
        if path:
            return path

    return "lua5.1"


def _find_table_literal_end(content, open_brace_index):
    depth = 0
    quote = None
    idx = open_brace_index

    while idx < len(content):
        char = content[idx]

        if quote:
            if char == "\\":
                idx += 2
                continue
            if char == quote:
                quote = None
            idx += 1
            continue

        if char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx + 1

        idx += 1

    return -1


def extract_static_constants(content, var_name):
    table_match = re.search(rf'\blocal\s+{re.escape(var_name)}\s*=\s*\{{', content)
    if not table_match:
        return ""

    open_brace_index = content.find("{", table_match.start())
    table_end = _find_table_literal_end(content, open_brace_index)
    if table_end == -1:
        return ""

    lua_code = r'''
local safe_env = {
    ipairs = ipairs, pairs = pairs, string = string, table = table,
    tonumber = tonumber, tostring = tostring, type = type, print = print,
    select = select, unpack = unpack, math = math, pcall = pcall,
}
if setfenv then
    setfenv(1, safe_env)
end
os = nil io = nil package = nil dofile = nil loadfile = nil debug = nil

local function escape_lua_string(s)
    local parts = {'"'}
    for i = 1, #s do
        local byte = string.byte(s, i)
        if byte == 92 then
            table.insert(parts, "\\\\")
        elseif byte == 34 then
            table.insert(parts, "\\\"")
        elseif byte == 10 then
            table.insert(parts, "\\n")
        elseif byte == 13 then
            table.insert(parts, "\\r")
        elseif byte == 9 then
            table.insert(parts, "\\t")
        elseif byte >= 32 and byte <= 126 then
            table.insert(parts, string.char(byte))
        else
            table.insert(parts, string.format("\\%03d", byte))
        end
    end
    table.insert(parts, '"')
    return table.concat(parts)
end

local constants = __STATIC_TABLE__
local out = "local Constants = {"
for i, v in ipairs(constants) do
    out = out .. "\n    [" .. i .. "] = " .. escape_lua_string(v) .. ","
end
out = out .. "\n}"
print(out)
'''.replace("__STATIC_TABLE__", content[open_brace_index:table_end])

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".lua",
        delete=False,
    ) as temp_handle:
        temp_path = temp_handle.name
        temp_handle.write(lua_code)

    try:
        process = subprocess.run(
            [get_lua_executable(), temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if process.returncode == 0:
            return process.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # Secondary fallback: use engine's StaticConstantDecoder directly in Python
    try:
        decoded_block = decode_prometheus_constants(content)
        if decoded_block and "local Constants = {" in decoded_block:
            return decoded_block.strip()
    except Exception:
        pass

    return ""


def _configure_text_streams():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_text_streams()

def deobfuscate_file(filepath):
    print(f"Processing {filepath}...")
    
    if ".deobf." in filepath or ".report." in filepath:
        return
        
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return

    content = normalize_luau_syntax(content)

    match = re.search(r'local\s+([a-zA-Z0-9_]+)\s*=\s*\{\s*(?:\[\d+\]\s*=\s*)?["\']', content)
    if not match:
        print(f"Could not identify string table variable in {filepath}.")
        return
    var_name = match.group(1)
    static_constants = None

    mock_env_code = r"""
local real_type = type
local real_tonumber = tonumber
local real_unpack = unpack
local real_concat = table.concat
local real_tostring = tostring
local real_print = print
local print = real_print

local _WAIT_COUNT = 0
local _LOOP_COUNTER = 0
local _MAX_LOOPS = tonumber((os and os.getenv and os.getenv("LUA_MAX_SBX")) or "150") or 150
local _LOOP_BODIES = {}

local function _check_loop()
    _LOOP_COUNTER = _LOOP_COUNTER + 1
    if _LOOP_COUNTER > _MAX_LOOPS then
        return false
    end
    return true
end

local function type(v)
    local mt = getmetatable(v)
    if mt and mt.__is_mock_dummy then
        return "userdata"
    end
    return real_type(v)
end

local function typeof(v)
    local mt = getmetatable(v)
    if mt and mt.__is_mock_dummy then
        return "Instance"
    end
    return type(v)
end

local function tonumber(v, base)
    if type(v) == "userdata" or (type(v) == "table" and getmetatable(v) and getmetatable(v).__is_mock_dummy) then
        return 1
    end
    return real_tonumber(v, base)
end

local function unpack(t, i, j)
    if not _check_loop() then
        return real_unpack(t, i, j)
    end
    if real_type(t) == "table" then
        local looks_like_chunk = true
        for k, v in pairs(t) do
            if real_type(k) ~= "number" then looks_like_chunk = false break end
        end
        
        if looks_like_chunk and #t > 0 then
            print("UNPACK CALLED WITH TABLE (Potential Chunk): size=" .. #t)
            local success, res = pcall(real_concat, t, ",")
            if success then
                print("CAPTURED CHUNK STRING: " .. res)
                if res:find("http", 1, true) or res:find("www.", 1, true) then
                    local url = res:match("https?://[%w%.%-%/%?%_%=%&%:]+") or res:match("www%.[%w%.%-%/%?%_%=%&%:]+")
                    if url then
                        print("URL DETECTED IN UNPACK --> " .. url)
                    end
                end
            end
        end
    end
    return real_unpack(t, i, j)
end

local function table_concat(t, sep, i, j)
    local res = real_concat(t, sep, i, j)
    if real_type(res) == "string" and (res:find("http", 1, true) or res:find("www.", 1, true)) then
        local url = res:match("https?://[%w%.%-%/%?%_%=%&%:]+") or res:match("www%.[%w%.%-%/%?%_%=%&%:]+")
        if url then
            print("URL DETECTED IN CONCAT --> " .. url)
        end
    end
    return res
end

local function escape_lua_string(s)
    local parts = {'"'}
    for i = 1, #s do
        local byte = string.byte(s, i)
        if byte == 92 then
            table.insert(parts, "\\\\")
        elseif byte == 34 then
            table.insert(parts, "\\\"")
        elseif byte == 10 then
            table.insert(parts, "\\n")
        elseif byte == 13 then
            table.insert(parts, "\\r")
        elseif byte == 9 then
            table.insert(parts, "\\t")
        elseif byte >= 32 and byte <= 126 then
            table.insert(parts, string.char(byte))
        else
            table.insert(parts, string.format("\\%03d", byte))
        end
    end
    table.insert(parts, '"')
    return table.concat(parts)
end

local function recursive_tostring(v, depth)
    if depth == nil then depth = 0 end
    if depth > 2 then return tostring(v) end
    
    if real_type(v) == "string" then
        return escape_lua_string(v)
    elseif real_type(v) == "number" then
        if v == math.floor(v) and v >= -2147483648 and v <= 2147483647 then
            return tostring(math.floor(v))
        end
        return tostring(v)
    elseif real_type(v) == "boolean" then
        return tostring(v)
    elseif v == nil then
        return "nil"
    elseif real_type(v) == "table" then
        if getmetatable(v) and getmetatable(v).__is_mock_dummy then
            return tostring(v)
        end
        local parts = {}
        local keys = {}
        for k in pairs(v) do table.insert(keys, k) end
        table.sort(keys, function(a,b) return tostring(a) < tostring(b) end)

        for _, k in ipairs(keys) do
            local val = v[k]
            local k_str = tostring(k)
            if real_type(k) == "string" then k_str = '["' .. k .. '"]' end
            table.insert(parts, k_str .. " = " .. recursive_tostring(val, depth + 1))
        end
        return "{" .. real_concat(parts, ", ") .. "}"
    elseif real_type(v) == "function" then
        return tostring(v)
    else
        return tostring(v)
    end
end

local function create_dummy(name)
    local d = {}
    local mt = {
        __is_mock_dummy = true,
        __index = function(_, k)
             print("ACCESSED --> " .. name .. "." .. k)
             if k == "LocalPlayer" then
                 local lp = create_dummy(name .. ".LocalPlayer")
                 local mt_lp = getmetatable(lp)
                 local old_idx = mt_lp.__index
                 mt_lp.__index = function(tbl, prop)
                     if prop == "Name" then return "fartitutatu" end
                     if prop == "UserId" then return 123456 end
                     if prop == "DisplayName" then return "fartitutatu" end
                     return old_idx(tbl, prop)
                 end
                 return lp
             end
             if k == "HttpGet" or k == "HttpGetAsync" then
                 return function(_, url, ...)
                     print("URL DETECTED --> " .. tostring(url))
                     return create_dummy("HttpGetResult")
                 end
            end
            return create_dummy(name .. "." .. k)
        end,
        __newindex = function(_, k, v)
            local val_str = recursive_tostring(v, 0)
            print("PROP_SET --> " .. name .. "." .. k .. " = " .. val_str)
        end,
        __call = function(_, ...)
            local args = {...}
            local arg_str = ""
            for i, v in ipairs(args) do
                if i > 1 then arg_str = arg_str .. ", " end
                arg_str = arg_str .. recursive_tostring(v)
            end

            local var_name = name:gsub("%.", "_") .. "_" .. math.random(100, 999)
            print("CALL_RESULT --> local " .. var_name .. " = " .. name .. "(" .. arg_str .. ")")
            if name == "task.wait" or name == "wait" then
                _WAIT_COUNT = _WAIT_COUNT + 1
                if _WAIT_COUNT > 10 then
                     error("Too many waits!")
                end
            end

            
            for i, v in ipairs(args) do
                if real_type(v) == "function" then
                    print("--- ENTERING CLOSURE FOR " .. name .. " ---")
                    local success, err = pcall(v, 
                        create_dummy("arg1"), create_dummy("arg2"), 
                        create_dummy("arg3"), create_dummy("arg4"))
                    if not success then 
                        print("-- CLOSURE ERROR: " .. tostring(err)) 
                    end
                    print("--- EXITING CLOSURE FOR " .. name .. " ---")
                elseif real_type(v) == "table" then
                    for tk, tv in pairs(v) do
                        if real_type(tv) == "function" then
                            print("--- ENTERING CLOSURE FOR " .. name .. "." .. tostring(tk) .. " ---")
                            local success, err = pcall(tv,
                                create_dummy("arg1"), create_dummy("arg2"),
                                create_dummy("arg3"), create_dummy("arg4"))
                            if not success then
                                print("-- CLOSURE ERROR: " .. tostring(err))
                            end
                            print("--- EXITING CLOSURE FOR " .. name .. "." .. tostring(tk) .. " ---")
                        end
                    end
                end
            end

            if name == "readfile" or name == "loadfile" or name == "dofile" then
                return ""
            end
            if name == "isfile" or name == "isfolder" then
                return false
            end
            if name == "listfiles" then
                return {}
            end
            if name == "writefile" or name == "appendfile" or name == "makefolder" or name == "delfile" or name == "delfolder" then
                return nil
            end
            
            return create_dummy(var_name)
        end,
        __tostring = function() return name end,
        __concat = function(a, b) return tostring(a) .. tostring(b) end,
        __add = function(a, b) return create_dummy("("..tostring(a).."+"..tostring(b)..")") end,
        __sub = function(a, b) return create_dummy("("..tostring(a).."-"..tostring(b)..")") end,
        __mul = function(a, b) return create_dummy("("..tostring(a).."*"..tostring(b)..")") end,
        __div = function(a, b) return create_dummy("("..tostring(a).."/"..tostring(b)..")") end,
        __mod = function(a, b) return create_dummy("("..tostring(a).."%"..tostring(b)..")") end,
        __pow = function(a, b) return create_dummy("("..tostring(a).."^"..tostring(b)..")") end,
        __unm = function(a) return create_dummy("-"..tostring(a)) end,
        __lt = function(a, b) return false end,
        __le = function(a, b) return false end,
        __eq = function(a, b) return false end,
        __len = function(a) return 2 end,
    }
    setmetatable(d, mt)
    return d
end

local function mock_pairs(t)
    local mt = getmetatable(t)
    if mt and mt.__is_mock_dummy then
        local i = 0
        return function(...)
            i = i + 1
            if i <= 1 then
                return i, create_dummy(tostring(t).."_v"..i)
            end
            return nil
        end
    end
    return pairs(t)
end

local function mock_ipairs(t)
    local mt = getmetatable(t)
    if mt and mt.__is_mock_dummy then
        local i = 0
        return function(...)
            i = i + 1
            if i <= 1 then
                return i, create_dummy(tostring(t).."_v"..i)
            end
            return nil
        end
    end
    return ipairs(t)
end

local safe_string = {}
local real_string = {}
for k, v in pairs(string) do real_string[k] = v end

for k, orig_func in pairs(real_string) do
    safe_string[k] = function(...)
        local has_dummy = false
        local n = select("#", ...)
        for i = 1, n do
            local a = select(i, ...)
            if real_type(a) == "table" and getmetatable(a) and getmetatable(a).__is_mock_dummy then
                has_dummy = true
                break
            end
        end
        if not has_dummy then
            local ok, res = pcall(orig_func, ...)
            if ok then return res end
            return ""
        end
        local args = {...}
        for i = 1, #args do
            if real_type(args[i]) == "table" and getmetatable(args[i]) and getmetatable(args[i]).__is_mock_dummy then
                args[i] = tostring(args[i])
            end
        end
        local ok, res = pcall(orig_func, unpack(args))
        if ok then return res end
        return ""
    end
end
safe_string.char = function(...)
    local args = {...}
    for i = 1, #args do
        local value = tonumber(args[i]) or 0
        args[i] = math.floor(value) % 256
    end
    return string.char(unpack(args))
end
safe_string.dump = function(f)
    if f == safe_string.char or f == safe_string.dump then
        error("unable to dump given function")
    end
    return string.dump(f)
end

local real_debug_getinfo = debug and debug.getinfo
local real_debug_getupvalue = debug and debug.getupvalue
local real_debug_traceback = debug and debug.traceback

local safe_debug = {
    ["getinfo"] = function(f, ...)
        if real_debug_getinfo then
            local info = real_debug_getinfo(f, ...)
            if info then
                if f == safe_string.char or f == safe_string.dump or f == pcall or f == xpcall then
                    info.what = "C"
                    info.source = "=[C]"
                    info.linedefined = -1
                    info.lastlinedefined = -1
                    info.short_src = "[C]"
                end
            end
            return info
        end
        return nil
    end,
    ["getupvalue"] = function(f, n)
        if f == safe_string.char or f == safe_string.dump then
            return nil
        end
        if real_debug_getupvalue then
            return real_debug_getupvalue(f, n)
        end
        return nil
    end,
    ["sethook"] = function(...)
        -- Disarm Prometheus AntiTamper line-based hook
        return
    end,
    ["traceback"] = function(...)
        if real_debug_traceback then
            return real_debug_traceback(...)
        end
        return ""
    end
}

local safe_os = {
    ["clock"] = (os and os.clock) or function() return 0 end,
    ["time"] = (os and os.time) or function() return 0 end,
    ["difftime"] = (os and os.difftime) or function(a, b) return (a or 0) - (b or 0) end,
    ["date"] = (os and os.date) or function() return "" end,
}

local MockEnv = {}
local safe_globals = {
    ["string"] = safe_string,
    ["table"] = {
        ["insert"] = table.insert,
        ["remove"] = table.remove,
        ["sort"] = table.sort,
        ["concat"] = table_concat,
        ["maxn"] = table.maxn
    },
    ["math"] = math,
    ["pairs"] = mock_pairs,
    ["ipairs"] = mock_ipairs,
    ["select"] = select,
    ["unpack"] = unpack,
    ["tonumber"] = function(v, base)
        if real_type(v) == "table" and getmetatable(v) and getmetatable(v).__is_mock_dummy then
            local n = tonumber(tostring(v), base)
            return n or 0
        end
        return tonumber(v, base)
    end,
    ["tostring"] = tostring,
    ["type"] = type,
    ["typeof"] = typeof,
    ["pcall"] = pcall,
    ["xpcall"] = xpcall,
    ["getfenv"] = function(target) return MockEnv end,
    ["setmetatable"] = setmetatable,
    ["getmetatable"] = getmetatable,
    ["error"] = error,
    ["assert"] = assert,
    ["next"] = next,
    ["print"] = function(...)
        local args = {...}
        local parts = {}
        for i,v in ipairs(args) do table.insert(parts, tostring(v)) end
        real_print("TRACE_PRINT --> " .. table.concat(parts, "\t"))
    end,
    ["_VERSION"] = _VERSION,
    ["rawset"] = rawset,
    ["rawget"] = rawget,
    ["os"] = safe_os,
    ["io"] = create_dummy("io"),
    ["package"] = create_dummy("package"),
    ["debug"] = safe_debug,
    ["dofile"] = function(f)
        real_print("SANDBOX BLOCKED DOFILE --> " .. tostring(f))
        return function(...) return create_dummy("FileModule") end
    end,
    ["loadfile"] = function(f)
        real_print("SANDBOX BLOCKED LOADFILE --> " .. tostring(f))
        return function(...) return create_dummy("FileModule") end
    end,
    ["loadstring"] = function(s) 
        print("LOADSTRING DETECTED: size=" .. tostring(#s)) 
        print("LOADSTRING CONTENT START")
        print(s)
        print("LOADSTRING CONTENT END")
        return function(...) return create_dummy("LoadedModule") end
    end
}

local exploit_funcs = {
    ["getgc"] = true, ["getinstances"] = true, ["getnilinstances"] = true,
    ["getloadedmodules"] = true, ["getconnections"] = true, ["firesignal"] = true, ["fireclickdetector"] = true,
    ["firetouchinterest"] = true, ["isnetworkowner"] = true, ["gethiddenproperty"] = true, ["sethiddenproperty"] = true,
    ["setsimulationradius"] = true, ["rconsoleprint"] = true, ["rconsolewarn"] = true, ["rconsoleerr"] = true,
    ["rconsoleinfo"] = true, ["rconsolename"] = true, ["rconsoleclear"] = true, ["consoleprint"] = true, ["consolewarn"] = true,
    ["consoleerr"] = true, ["consoleinfo"] = true, ["consolename"] = true, ["consoleclear"] = true, ["warn"] = true, ["print"] = true,
    ["error"] = true, ["debug"] = true, ["clonefunction"] = true, ["hookfunction"] = true, ["newcclosure"] = true, ["replaceclosure"] = true,
    ["restoreclosure"] = true, ["islclosure"] = true, ["iscclosure"] = true, ["checkcaller"] = true, ["getnamecallmethod"] = true,
    ["setnamecallmethod"] = true, ["getrawmetatable"] = true, ["setrawmetatable"] = true, ["setreadonly"] = true,
    ["isreadonly"] = true, ["iswindowactive"] = true, ["keypress"] = true, ["keyrelease"] = true, ["mouse1click"] = true,
    ["mouse1press"] = true, ["mouse1release"] = true, ["mousescroll"] = true, ["mousemoverel"] = true, ["mousemoveabs"] = true,
    ["hookmetamethod"] = true, ["getcallingscript"] = true, ["makefolder"] = true, ["writefile"] = true, ["readfile"] = true,
    ["appendfile"] = true, ["loadfile"] = true, ["listfiles"] = true, ["isfile"] = true, ["isfolder"] = true, ["delfile"] = true,
    ["delfolder"] = true, ["dofile"] = true, ["bit"] = true, ["bit32"] = true,
    ["Vector2"] = true, ["Vector3"] = true, ["CFrame"] = true, ["UDim"] = true, ["UDim2"] = true, ["Color3"] = true, ["Instance"] = true, ["Ray"] = true,
    ["Enum"] = true, ["BrickColor"] = true, ["NumberRange"] = true, ["NumberSequence"] = true, ["ColorSequence"] = true,
    ["task"] = true, ["coroutine"] = true, ["Delay"] = true, ["delay"] = true, ["Spawn"] = true, ["spawn"] = true, ["Wait"] = true, ["wait"] = true,
    ["workspace"] = true, ["Workspace"] = true, ["tick"] = true, ["time"] = true, ["elapsedTime"] = true, ["utf8"] = true,
    ["setclipboard"] = true, ["toclipboard"] = true, ["set_clipboard"] = true, ["setrbxclipboard"] = true, ["getclipboard"] = true,
    ["request"] = true, ["http_request"] = true, ["syn"] = true, ["HttpGet"] = true, ["HttpPost"] = true, ["http"] = true,
    ["identifyexecutor"] = true, ["getexecutorname"] = true, ["Drawing"] = true, ["gethui"] = true, ["cloneref"] = true, ["clone_ref"] = true,
    ["queue_on_teleport"] = true, ["syn_queue_on_teleport"] = true, ["queueonteleport"] = true
}

setmetatable(MockEnv, {
    __index = function(t, k)
        if safe_globals[k] then
            return safe_globals[k]
        end

        if k == "game" then
            print("ACCESSED --> game")
            return create_dummy("game")
        end
        if k == "getgenv" or k == "getrenv" or k == "getreg" then
            return function() return MockEnv end
        end

        if exploit_funcs[k] then
            print("ACCESSED --> " .. k)
            return create_dummy(k)
        end

        -- 4. Fallback: Return NIL (to satisfy Fallback Path logic)
        print("ACCESSED (NIL) --> " .. k)
        return nil
    end,
    
    __newindex = function(t, k, v)
        local val_str = ""
        if real_type(v) == "string" then
            val_str = '"' .. v .. '"'
        elseif real_type(v) == "number" or real_type(v) == "boolean" then
            val_str = tostring(v)
        elseif real_type(v) == "table" then
            val_str = recursive_tostring(v, 0)
        else
            val_str = tostring(v)
        end
        print("SET GLOBAL --> " .. tostring(k) .. " = " .. val_str)
        rawset(t, k, v)

        if real_type(v) == "function" then
            _PROBED_GLOBALS = _PROBED_GLOBALS or {}
            if not _PROBED_GLOBALS[k] then
                _PROBED_GLOBALS[k] = true
                print("--- ENTERING CLOSURE FOR " .. tostring(k) .. " ---")
                local success, err = pcall(v, create_dummy("arg1"), create_dummy("arg2"), create_dummy("arg3"))
                if not success then
                    print("-- CLOSURE ERROR: " .. tostring(err))
                end
                print("--- EXITING CLOSURE FOR " .. tostring(k) .. " ---")
            end
        end
    end
})

safe_globals["_G"] = MockEnv
safe_globals["shared"] = MockEnv
_G.print = safe_globals["print"]
_G.warn = safe_globals["print"]
setmetatable(_G, { __index = MockEnv })

-- Purge dangerous host globals from interpreter environment before executing untrusted code
io = nil
package = nil
dofile = safe_globals["dofile"]
loadfile = safe_globals["loadfile"]
if os then
    os.execute = nil
    os.remove = nil
    os.rename = nil
    os.exit = nil
    os.tmpname = nil
    os.getenv = nil
end
debug = safe_debug

setfenv(1, MockEnv)
"""

    idx_args = content.rfind("(getfenv")
    if idx_args == -1:
         idx_args = content.rfind("( getfenv")

    if idx_args == -1:
         idx_args = len(content)

    ret_matches = list(re.finditer(r'return\s*\(\s*function', content[:idx_args]))
    if not ret_matches:
        print(f"Could not find return(function injection point in {filepath}.")
        return
    idx_ret = ret_matches[-1].start()

    dumper_code = f"""
    print("--- CONSTANTS START ---")
    if {var_name} and type({var_name}) == "table" then
        local sorted_keys = {{}}
        for k in pairs({var_name}) do table.insert(sorted_keys, k) end
        table.sort(sorted_keys)
        local nl = string.char(10)
        local out = "local Constants = {{" .. nl
        for i, k in ipairs(sorted_keys) do
            local v = {var_name}[k]
            local v_str = escape_lua_string(v)
            out = out .. "    [" .. k .. "] = " .. v_str .. "," .. nl
        end
        out = out .. "}}"
        print(out)
    end
    print("--- CONSTANTS END ---")
    """

    new_content = mock_env_code + content[:idx_ret] + dumper_code + content[idx_ret:]

    if "getfenv and getfenv()or _ENV" in new_content:
        new_content = new_content.replace("getfenv and getfenv()or _ENV", "MockEnv")
    else:
        new_content = re.sub(r'getfenv\s+and\s+getfenv\(\)or\s+_ENV', 'MockEnv', new_content)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".lua",
        delete=False,
    ) as temp_handle:
        temp_file = temp_handle.name
        temp_handle.write(new_content)

    print(f"Executing deobfuscation for {filepath}...")

    process = subprocess.Popen([get_lua_executable(), temp_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stdout_lines = []
    
    RELEVANT_PREFIXES = (
        "ACCESSED", "CALL_RESULT", "local Constants =", 
        "URL DETECTED", "SET GLOBAL", "UNPACK CALLED", 
        "CAPTURED CHUNK", "CLOSURE", "TRACE_PRINT", 
        "PROP_SET", "LOADSTRING"
    )
    
    stdout_data = b""
    err = b""
    try:
        stdout_data, err = process.communicate(timeout=20)
    except subprocess.TimeoutExpired as exc:
        print("Timeout reached.")
        process.kill()
        stdout_data, err = process.communicate()
        if exc.output:
            stdout_data = exc.output + stdout_data
        if exc.stderr:
            err = exc.stderr + err
    except Exception as e:
        print(f"Error: {e}")
        process.kill()

    if stdout_data:
        for line in stdout_data.decode('utf-8', errors='replace').splitlines():
            stdout_lines.append(line.strip())
            if any(prefix in line for prefix in RELEVANT_PREFIXES):
                print(line.strip())
    stderr_text = ""
    if err:
        stderr_text = err.decode('utf-8', errors='replace')

    constants_str = ""
    trace_lines = []

    in_constants = False
    for line in stdout_lines:
        if line == "--- CONSTANTS START ---":
            in_constants = True
            continue
        if line == "--- CONSTANTS END ---":
            in_constants = False
            continue

        if in_constants:
            constants_str += line + "\n"
        elif any(prefix in line for prefix in RELEVANT_PREFIXES):
            trace_lines.append(line)

    if not constants_str and LUA_CONTROL_STRUCTURE_TOO_LONG in stderr_text:
        static_constants = extract_static_constants(content, var_name)
        if static_constants:
            print("Lua 5.1 could not compile the full script; using static string-table fallback.")
            constants_str = static_constants + "\n"
    elif stderr_text.strip():
        if LUA_CONTROL_STRUCTURE_TOO_LONG in stderr_text:
            static_constants = extract_static_constants(content, var_name)
            if static_constants:
                print("Lua 5.1 could not compile the full script; using static string-table fallback.")
                if not constants_str:
                    constants_str = static_constants + "\n"
        else:
            print("STDERR:", stderr_text)

    # Apply engine trace filter to compress VM loops and eliminate table unpack noise
    trace_filter = TraceFilter(max_consecutive_duplicates=5)
    filtered_trace_lines = trace_filter.filter_lines(trace_lines)

    report_file = filepath + ".report.txt"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("--- DEOBFUSCATION REPORT ---\n")
        f.write(f"File: {filepath}\n\n")
        f.write("--- TRACE ---\n")
        for line in filtered_trace_lines:
            f.write(line + "\n")
        f.write("\n--- CONSTANTS ---\n")
        f.write(constants_str)

    print(f"Report saved to {report_file}")

    deobf_path = filepath + ".deobf.lua"
    const_path = filepath + ".constants.lua"

    try:
        trace_to_lua.parse_trace_lines(
            filtered_trace_lines,
            constants_str,
            out_file_path=deobf_path,
            const_file_path=const_path,
        )
    except Exception as e:
        print(f"Failed to convert trace: {e}")
        import traceback
        traceback.print_exc()

    deobf_path = filepath + ".deobf.lua"
    if os.path.exists(deobf_path):
        try:
            with open(deobf_path, "r", encoding="utf-8", errors="replace") as df:
                deobf_content = df.read()
            inlined_content = inline_constants_in_code(deobf_content)
            if inlined_content != deobf_content:
                with open(deobf_path, "w", encoding="utf-8") as df:
                    df.write(inlined_content)
        except Exception:
            pass

    if os.path.exists(temp_file):
        os.remove(temp_file)
    #if os.path.exists(report_file):
    #    os.remove(report_file)

VERSION = "2.7.0"


def print_help():
    print(f"""⚡ Epimetheus v{VERSION} — Advanced Prometheus & Luau Deobfuscator Engine
Linux x86_64 Edition

Usage:
  python3 deobfuscator.py <script.lua>        Deobfuscate a single Lua/Luau script
  python3 deobfuscator.py <directory>         Batch deobfuscate all .lua scripts in directory

Options:
  -h, --help                                 Display this help message and exit
  -v, --version                              Display version information and exit
  -j, --jobs <N|auto>                        Parallel worker processes for batch processing (default: 1)

Output:
  <name>.deobf.lua                           Reconstructed clean Lua source code
  <name>.constants.lua                       Extracted and decoded constants table
  <name>.report.txt                          Filtered execution trace report
""")


def main():
    check_platform()

    import argparse

    parser = argparse.ArgumentParser(
        prog="deobfuscator.py",
        description=f"⚡ Epimetheus v{VERSION} — Advanced Prometheus & Luau Deobfuscator Engine (Linux x86_64)",
        add_help=False,
    )
    parser.add_argument("target", nargs="?", default="obfuscated_scripts", help="Lua script file or directory of scripts to deobfuscate")
    parser.add_argument("-j", "--jobs", default=1, help="Number of parallel worker processes for batch processing (default: 1)")
    parser.add_argument("-h", "--help", action="store_true", help="Display this help message and exit")
    parser.add_argument("-v", "--version", action="store_true", help="Display version information and exit")

    args, unknown = parser.parse_known_args()

    if args.help:
        print_help()
        return

    if args.version:
        print(f"Epimetheus v{VERSION} (Linux x86_64)")
        return

    target = args.target

    # Determine worker count
    jobs = 1
    if args.jobs:
        if str(args.jobs).lower() in ("auto", "max"):
            jobs = min(os.cpu_count() or 4, 4)
        else:
            try:
                jobs = max(1, int(args.jobs))
            except ValueError:
                jobs = 1

    if os.path.isfile(target):
        deobfuscate_file(target)
    elif os.path.isdir(target):
        files = glob.glob(os.path.join(target, "*.lua"))
        valid_files = [
            f for f in sorted(files)
            if not ("temp_deob" in f or ".report.txt" in f or ".deobf." in f or ".constants." in f)
        ]
        if not valid_files:
            print(f"No .lua scripts found in directory: {target}")
            return

        if jobs > 1 and len(valid_files) > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            max_workers = min(jobs, len(valid_files), os.cpu_count() or 4)
            print(f"[*] Processing {len(valid_files)} files in parallel (jobs={max_workers})...\n")
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(deobfuscate_file, f): f for f in valid_files}
                for fut in as_completed(futures):
                    f = futures[fut]
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"[-] Error processing {f}: {e}")
                    print("-" * 40)
        else:
            for file in valid_files:
                deobfuscate_file(file)
                print("-" * 40)
    else:
        if len(sys.argv) <= 1:
            print_help()
        else:
            print(f"Error: Target path not found: {target}")
            sys.exit(1)


if __name__ == "__main__":
    main()
