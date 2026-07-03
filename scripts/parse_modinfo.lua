#!/usr/bin/env lua
-- parse_modinfo.lua <path-to-modinfo.lua> [folder_name]
--
-- Executes a DST modinfo.lua inside a sandboxed environment and prints a
-- single JSON document to stdout:
--
--   {"ok": true, "modinfo": {...}}    on success
--   {"ok": false, "error": "..."}     on failure (still exit code 0; the
--                                     Python caller reads the "ok" flag)
--
-- Compatible with Lua 5.1 (incl. LuaJIT) through Lua 5.5.
-- No external dependencies.
--
-- NOTE: the JSON encoder below is intentionally duplicated in
-- parse_lua_table.lua so that each helper script stays fully standalone
-- (no require/dofile path games when invoked from arbitrary directories).

----------------------------------------------------------------------------
-- Minimal JSON encoder
----------------------------------------------------------------------------

-- Tables tagged with this metatable are always encoded as JSON arrays,
-- even when empty (a bare empty Lua table is ambiguous).
local ARRAY_MT = { __jsontype = "array" }

local function array(t)
    return setmetatable(t or {}, ARRAY_MT)
end

local ESCAPE_MAP = {
    ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
    ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

local function escape_char(c)
    return ESCAPE_MAP[c] or string.format("\\u%04x", string.byte(c))
end

local function json_string(s)
    -- %c matches control characters on all Lua versions (unlike %z / \0).
    return '"' .. s:gsub('[%c"\\]', escape_char) .. '"'
end

local function json_number(n)
    -- JSON cannot represent NaN / infinity; degrade to null.
    if n ~= n or n == math.huge or n == -math.huge then
        return "null"
    end
    if math.type and math.type(n) == "integer" then
        return string.format("%d", n)
    end
    if n == math.floor(n) and math.abs(n) < 2 ^ 53 then
        return string.format("%.0f", n) -- avoid "5.0" noise for whole floats
    end
    return string.format("%.14g", n)
end

local encode

local function json_table(t, seen)
    if seen[t] then
        return '"<cycle>"'
    end
    seen[t] = true

    local mt = getmetatable(t)
    local is_array = mt ~= nil and mt.__jsontype == "array"
    if not is_array then
        local count = 0
        for _ in pairs(t) do count = count + 1 end
        is_array = count > 0 and count == #t
    end

    local parts = {}
    if is_array then
        for i = 1, #t do
            parts[#parts + 1] = encode(t[i], seen)
        end
        seen[t] = nil
        return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, v in pairs(t) do
        parts[#parts + 1] = json_string(tostring(k)) .. ":" .. encode(v, seen)
    end
    seen[t] = nil
    return "{" .. table.concat(parts, ",") .. "}"
end

encode = function(v, seen)
    local tv = type(v)
    if tv == "nil" then
        return "null"
    elseif tv == "boolean" then
        return v and "true" or "false"
    elseif tv == "number" then
        return json_number(v)
    elseif tv == "string" then
        return json_string(v)
    elseif tv == "table" then
        return json_table(v, seen or {})
    end
    -- function / userdata / thread: not representable, flatten to a string
    return json_string("<" .. tv .. ">")
end

local function emit(t)
    io.write(encode(t), "\n")
end

----------------------------------------------------------------------------
-- Sandboxed loading of modinfo.lua
----------------------------------------------------------------------------

-- Only pure/safe standard functions are exposed; io/os/require are withheld.
-- print is a no-op so a stray print in a modinfo cannot corrupt our JSON.
local function make_env(folder_name)
    local env = {
        pairs = pairs, ipairs = ipairs, next = next, select = select,
        type = type, tostring = tostring, tonumber = tonumber,
        pcall = pcall, error = error, assert = assert,
        rawget = rawget, rawset = rawset, rawequal = rawequal,
        setmetatable = setmetatable, getmetatable = getmetatable,
        unpack = unpack or table.unpack,
        string = string, table = table, math = math,
        print = function() end,

        -- Globals that the DST engine provides when it runs modinfo.lua:
        locale = "en",
        folder_name = folder_name,
        ChooseTranslationTable = function(tbl)
            if type(tbl) ~= "table" then return tbl end
            return tbl.en or tbl[1]
        end,
    }
    env._G = env
    return env
end

local function load_sandboxed(path, env)
    if _VERSION == "Lua 5.1" then
        -- Lua 5.1 / LuaJIT: no env parameter on loadfile; use setfenv.
        local chunk, err = loadfile(path)
        if chunk then setfenv(chunk, env) end
        return chunk, err
    end
    return loadfile(path, "t", env)
end

----------------------------------------------------------------------------
-- Conversion of modinfo globals to a JSON-friendly structure
----------------------------------------------------------------------------

-- Keep serializable scalars as-is; flatten anything else to a string so the
-- UI can still display *something* instead of failing the whole mod.
local function scrub_scalar(v)
    local tv = type(v)
    if tv == "nil" or tv == "boolean" or tv == "number" or tv == "string" then
        return v
    end
    return tostring(v)
end

local function opt_string(v, fallback)
    if v == nil then return fallback end
    if type(v) == "string" then return v end
    return tostring(v)
end

local function convert_choices(raw)
    local choices = array({})
    if type(raw) ~= "table" then
        return choices
    end
    for _, choice in ipairs(raw) do
        if type(choice) == "table" then
            choices[#choices + 1] = {
                description = opt_string(choice.description, ""),
                data = scrub_scalar(choice.data), -- nil => key omitted => JSON caller sees null
                hover = opt_string(choice.hover, nil),
            }
        end
    end
    return choices
end

local function convert_configuration_options(raw)
    local out = array({})
    if type(raw) ~= "table" then
        return out
    end
    for _, opt in ipairs(raw) do
        if type(opt) == "table" then
            out[#out + 1] = {
                name = opt_string(opt.name, ""),
                label = opt_string(opt.label, ""),
                hover = opt_string(opt.hover, ""),
                default = scrub_scalar(opt.default),
                options = convert_choices(opt.options),
            }
        end
    end
    return out
end

----------------------------------------------------------------------------
-- Main
----------------------------------------------------------------------------

local path = arg and arg[1]
if not path then
    emit({ ok = false, error = "usage: parse_modinfo.lua <modinfo.lua> [folder_name]" })
    os.exit(0)
end

local env = make_env(arg[2] or "unknown-folder")

local chunk, load_err = load_sandboxed(path, env)
if not chunk then
    emit({ ok = false, error = "failed to load modinfo.lua: " .. tostring(load_err) })
    os.exit(0)
end

local ok, run_err = pcall(chunk)
if not ok then
    emit({ ok = false, error = "failed to execute modinfo.lua: " .. tostring(run_err) })
    os.exit(0)
end

emit({
    ok = true,
    modinfo = {
        name = opt_string(env.name, ""),
        description = opt_string(env.description, ""),
        author = opt_string(env.author, ""),
        version = opt_string(env.version, ""),
        configuration_options = convert_configuration_options(env.configuration_options),
    },
})
