// Shared state between the widgets below: dirty-checkers feed the
// leave-with-unsaved-changes warning; form submits suppress it (saving IS
// how you leave the page).
var DST = { dirtyCheckers: [], suppressUnloadWarning: false };

function dstAnyDirty() {
  return DST.dirtyCheckers.some(function (fn) { return fn(); });
}

document.addEventListener("submit", function () {
  DST.suppressUnloadWarning = true;
}, true);

window.addEventListener("beforeunload", function (e) {
  if (DST.suppressUnloadWarning || !dstAnyDirty()) return;
  e.preventDefault();
  e.returnValue = ""; // required by Chrome for the dialog to show
});

// Log viewer: jump to the newest lines (the bottom) on load.
(function () {
  var view = document.getElementById("log-view");
  if (view) view.scrollTop = view.scrollHeight;
})();

// Files page: grow each editor to fit its whole content (the page scrolls,
// not the box; the textarea itself is not user-resizable), and grey out
// each file's Save button until its content actually differs from disk.
(function () {
  var forms = document.querySelectorAll(".file-editor form");
  if (!forms.length) return;

  function fit(el) {
    el.style.height = "auto";
    el.style.height = Math.max(el.scrollHeight + 4, 520) + "px";
  }

  forms.forEach(function (form) {
    var area = form.querySelector("textarea");
    var saveBtn = form.querySelector('button[type="submit"]');
    if (!area) return;

    function dirty() {
      // defaultValue is the content as served == what is on disk.
      return area.value !== area.defaultValue;
    }
    function update() {
      if (saveBtn) {
        saveBtn.disabled = !dirty();
        saveBtn.title = dirty() ? "" : "No changes yet";
      }
    }

    DST.dirtyCheckers.push(dirty);
    fit(area);
    update();
    area.addEventListener("input", function () {
      fit(area);
      update();
    });
  });
})();

// Client-side search filter for the mods list. Each .mod-card carries a
// data-search attribute with "name folder id author" in lowercase.
(function () {
  var search = document.getElementById("mod-search");
  if (!search) return;
  search.addEventListener("input", function () {
    var q = search.value.trim().toLowerCase();
    document.querySelectorAll(".mod-card").forEach(function (card) {
      var hit = !q || (card.dataset.search || "").indexOf(q) !== -1;
      card.style.display = hit ? "" : "none";
    });
  });
})();

// Mods page: live status for background steamcmd downloads. While a job is
// running, show an animated bar with elapsed time and steamcmd's latest
// output; when it finishes, reload with the result as a flash message —
// unless there are unsaved edits, in which case offer a manual reload so
// the edits aren't silently thrown away.
(function () {
  var panel = document.getElementById("download-status");
  if (!panel) return;
  var label = panel.querySelector(".dl-label");
  var log = panel.querySelector(".dl-log");
  var sawActive = false;

  function setDownloadButtonsDisabled(disabled) {
    document
      .querySelectorAll('button[formaction="/mods/download"], button[name="action"][value="add_download"]')
      .forEach(function (btn) { btn.disabled = disabled; });
  }

  function finish(s) {
    var level = s.status === "done" ? "success" : "error";
    var target = "/mods?msg=" + encodeURIComponent(s.message) + "&level=" + level;
    if (!dstAnyDirty()) {
      DST.suppressUnloadWarning = true;
      window.location.href = target;
      return;
    }
    // Keep the panel up with the outcome and let the user decide.
    var fill = panel.querySelector(".progress-fill");
    if (fill) fill.style.display = "none";
    label.textContent = (s.status === "done" ? "Download finished: " : "Download failed: ") + s.message;
    log.innerHTML = 'This page was not reloaded because you have unsaved changes. ' +
      '<a href="' + target + '">Reload now</a> (unsaved changes will be lost) or save first.';
  }

  function poll() {
    fetch("/mods/download/status", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.active) {
          sawActive = true;
          panel.style.display = "";
          label.textContent = "Downloading workshop-" + s.workshop_id + "… " + s.elapsed + "s elapsed";
          log.textContent = s.log || "starting steamcmd…";
          setDownloadButtonsDisabled(true);
          setTimeout(poll, 1500);
        } else if (sawActive && s.workshop_id) {
          finish(s);
        } else {
          panel.style.display = "none";
          setDownloadButtonsDisabled(false);
        }
      })
      .catch(function () { setTimeout(poll, 3000); });
  }
  poll();
})();

// Mods page: grey out the Save buttons until something actually changes.
// The backend independently skips writes/backups for unchanged content, so
// this is purely a UX layer (with JS disabled the buttons just stay active).
(function () {
  var form = document.getElementById("mods-form");
  if (!form) return;
  // Only the Save buttons — not the pending-mod Remove buttons (formaction).
  var saveButtons = form.querySelectorAll('button[type="submit"]:not([formaction])');
  if (!saveButtons.length) return;

  function snapshot() {
    var parts = [];
    form.querySelectorAll("select, input").forEach(function (el) {
      if (!el.name) return; // e.g. the search box
      if (el.type === "checkbox") {
        parts.push(el.name + "=" + el.checked);
      } else if (el.type !== "hidden") {
        parts.push(el.name + "=" + el.value);
      }
    });
    return parts.join("&");
  }

  var initial = snapshot();
  function dirty() {
    return snapshot() !== initial;
  }
  function update() {
    saveButtons.forEach(function (btn) {
      btn.disabled = !dirty();
      btn.title = dirty() ? "" : "No changes yet";
    });
  }

  DST.dirtyCheckers.push(dirty);
  form.addEventListener("change", update);
  form.addEventListener("input", update);
  update();
})();

// Backups page: select-all checkbox, live count on the delete button, and a
// count-aware confirm before batch deletion.
(function () {
  var form = document.getElementById("backups-form");
  if (!form) return;
  var selectAll = document.getElementById("backup-select-all");
  var deleteBtn = document.getElementById("delete-selected");

  function rows() {
    return form.querySelectorAll('input[name="session_ids"]');
  }
  function checkedCount() {
    return form.querySelectorAll('input[name="session_ids"]:checked').length;
  }
  function update() {
    var n = checkedCount();
    deleteBtn.disabled = n === 0;
    deleteBtn.textContent = n ? "Delete selected (" + n + ")" : "Delete selected";
    if (selectAll) {
      var total = rows().length;
      selectAll.checked = n > 0 && n === total;
      selectAll.indeterminate = n > 0 && n < total;
    }
  }

  if (selectAll) {
    selectAll.addEventListener("change", function () {
      rows().forEach(function (box) { box.checked = selectAll.checked; });
      update();
    });
  }
  form.addEventListener("change", function (e) {
    if (e.target && e.target.name === "session_ids") update();
  });
  form.addEventListener("submit", function (e) {
    var n = checkedCount();
    if (n === 0 || !confirm("Permanently delete " + n + " backup record(s)? This cannot be undone.")) {
      e.preventDefault();
      DST.suppressUnloadWarning = false; // submit was cancelled; keep guarding
    }
  });
  update();
})();

// "Copy <first shard> → <others>": within one mod card, copy every option
// value from the source shard's column to the other shards' columns.
// Enabled checkboxes are left alone; nothing is written until Save.
(function () {
  document.querySelectorAll(".copy-shard").forEach(function (btn) {
    var originalLabel = btn.textContent;
    btn.addEventListener("click", function () {
      var card = btn.closest(".mod-card");
      if (!card) return;
      var source = btn.dataset.source;

      // Group option fields by "workshopId__optionIndex"; each group holds
      // one field per shard (field names: opt__<shard>__<id>__<idx>).
      var groups = {};
      card.querySelectorAll('select[name^="opt__"], input[name^="opt__"]').forEach(function (el) {
        var p = el.name.split("__");
        if (p.length !== 4) return;
        var key = p[2] + "__" + p[3];
        (groups[key] = groups[key] || []).push({ shard: p[1], el: el });
      });

      var copied = 0;
      Object.keys(groups).forEach(function (key) {
        var group = groups[key];
        var src = null;
        group.forEach(function (g) { if (g.shard === source) src = g; });
        if (!src) return;
        group.forEach(function (g) {
          if (g.shard === source || g.el.disabled) return;
          if (g.el.tagName === "SELECT") {
            // Only copy when the target select has an option with that value.
            for (var i = 0; i < g.el.options.length; i++) {
              if (g.el.options[i].value === src.el.value) {
                if (g.el.value !== src.el.value) { g.el.value = src.el.value; copied++; }
                break;
              }
            }
          } else if (g.el.value !== src.el.value) {
            g.el.value = src.el.value;
            copied++;
          }
        });
      });

      btn.textContent = copied
        ? "Copied " + copied + " value(s) — remember to Save"
        : "Nothing to copy — values already match";
      setTimeout(function () { btn.textContent = originalLabel; }, 2500);
      card.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
})();

// "Reset to defaults": set every option field in the card back to the
// modinfo default carried in data-default. Nothing is written until the
// user presses Save, so the action is reviewable and cancelable.
(function () {
  document.querySelectorAll(".reset-defaults").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var card = btn.closest(".mod-card");
      if (!card) return;
      card.querySelectorAll("select[data-default], input[type=text][data-default]").forEach(function (el) {
        if (el.disabled) return;
        if (el.tagName === "SELECT") {
          // Only switch when an option with the default value exists.
          for (var i = 0; i < el.options.length; i++) {
            if (el.options[i].value === el.dataset.default) {
              el.value = el.dataset.default;
              break;
            }
          }
        } else {
          el.value = el.dataset.default;
        }
      });
      var original = "Reset to defaults";
      btn.textContent = "Defaults restored — remember to Save";
      setTimeout(function () { btn.textContent = original; }, 2500);
      // Programmatic value changes don't fire events on their own; notify
      // the dirty-tracker so the Save buttons light up.
      card.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
})();
