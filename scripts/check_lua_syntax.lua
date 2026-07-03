#!/usr/bin/env lua
-- check_lua_syntax.lua <file.lua>
--
-- Compile-checks a Lua file WITHOUT executing it and prints a JSON verdict:
--   {"ok": true}                     the file parses
--   {"ok": false, "error": "..."}    syntax error (still exit code 0)
--
-- Used to validate dedicated_server_mods_setup.lua edits, which call
-- engine functions (ServerModSetup) and therefore cannot be *executed*
-- outside the game. Compatible with Lua 5.1 (incl. LuaJIT) through 5.5.

local ESCAPE_MAP = {
    ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
    ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

local function json_string(s)
    return '"' .. s:gsub('[%c"\\]', function(c)
        return ESCAPE_MAP[c] or string.format("\\u%04x", string.byte(c))
    end) .. '"'
end

local path = arg and arg[1]
if not path then
    io.write('{"ok":false,"error":"usage: check_lua_syntax.lua <file.lua>"}\n')
    os.exit(0)
end

local chunk, err = loadfile(path)
if chunk then
    io.write('{"ok":true}\n')
else
    io.write('{"ok":false,"error":' .. json_string(tostring(err)) .. '}\n')
end
