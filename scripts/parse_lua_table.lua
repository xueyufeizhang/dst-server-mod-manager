#!/usr/bin/env lua
-- parse_lua_table.lua <path-to-lua-file>
--
-- Loads a Lua file that is expected to `return { ... }` (e.g. a DST
-- modoverrides.lua) inside a sandbox and prints the table as JSON:
--
--   {"ok": true, "data": {...}}      on success
--   {"ok": false, "error": "..."}    on failure (still exit code 0)
--
-- Compatible with Lua 5.1 (incl. LuaJIT) through Lua 5.5.
--
-- NOTE: the JSON encoder is intentionally duplicated from parse_modinfo.lua
-- so that each helper script stays fully standalone.

----------------------------------------------------------------------------
-- Minimal JSON encoder
----------------------------------------------------------------------------

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
    if n ~= n or n == math.huge or n == -math.huge then
        return "null" -- JSON cannot represent NaN / infinity
    end
    if math.type and math.type(n) == "integer" then
        return string.format("%d", n)
    end
    if n == math.floor(n) and math.abs(n) < 2 ^ 53 then
        return string.format("%.0f", n)
    end
    return string.format("%.14g", n)
end

local encode

local function json_table(t, seen)
    if seen[t] then
        return '"<cycle>"'
    end
    seen[t] = true

    -- Dense integer-keyed tables become arrays; everything else becomes an
    -- object (an empty table becomes {}, which is what modoverrides needs
    -- for empty configuration_options).
    local count = 0
    for _ in pairs(t) do count = count + 1 end
    local is_array = count > 0 and count == #t

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
    return json_string("<" .. tv .. ">")
end

local function emit(t)
    io.write(encode(t), "\n")
end

----------------------------------------------------------------------------
-- Sandboxed loading
----------------------------------------------------------------------------

local function make_env()
    -- modoverrides.lua should be pure data, but expose a few safe helpers in
    -- case someone hand-wrote logic in theirs. No io/os/require.
    local env = {
        pairs = pairs, ipairs = ipairs, next = next, select = select,
        type = type, tostring = tostring, tonumber = tonumber,
        pcall = pcall, error = error, assert = assert,
        string = string, table = table, math = math,
        print = function() end,
    }
    env._G = env
    return env
end

local function load_sandboxed(path, env)
    if _VERSION == "Lua 5.1" then
        local chunk, err = loadfile(path)
        if chunk then setfenv(chunk, env) end
        return chunk, err
    end
    return loadfile(path, "t", env)
end

----------------------------------------------------------------------------
-- Main
----------------------------------------------------------------------------

local path = arg and arg[1]
if not path then
    emit({ ok = false, error = "usage: parse_lua_table.lua <file.lua>" })
    os.exit(0)
end

local chunk, load_err = load_sandboxed(path, make_env())
if not chunk then
    emit({ ok = false, error = "failed to load file: " .. tostring(load_err) })
    os.exit(0)
end

local ok, result = pcall(chunk)
if not ok then
    emit({ ok = false, error = "failed to execute file: " .. tostring(result) })
    os.exit(0)
end

if type(result) ~= "table" then
    emit({ ok = false, error = "file did not return a table (got " .. type(result) .. ")" })
    os.exit(0)
end

emit({ ok = true, data = result })
