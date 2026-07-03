-- Sample mod that exercises the DST-specific globals the engine normally
-- provides while running modinfo.lua: ChooseTranslationTable and locale.

local T = ChooseTranslationTable

name = T({ "Extra Storage", zh = "额外存储" })
description = T({
    "Adds bigger chests and backpacks. Sample mod for dst-mod-manager testing.",
    zh = "更大的箱子和背包。用于 dst-mod-manager 本地测试的示例 mod。",
})
author = "sample-author"
version = "2.0"

api_version = 10
dst_compatible = true
all_clients_require_mod = true

configuration_options = {
    {
        name = "CHEST_SIZE",
        label = "Chest size",
        hover = "How many slots chests have.",
        options = {
            { description = "3x3", data = 9 },
            { description = "4x4", data = 16 },
        },
        default = 9,
    },
    {
        name = "OWNER_TAG",
        label = "Owner tag",
        hover = "String-typed option.",
        options = {
            { description = "None", data = "none" },
            { description = "Custom", data = "custom" },
        },
        default = "none",
    },
    {
        name = "NIL_DEMO",
        label = "Nil default demo",
        hover = "First choice has data = nil to exercise null handling.",
        options = {
            { description = "Off (nil)", data = nil },
            { description = "On", data = true },
        },
        default = nil,
    },
}
