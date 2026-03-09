function fileCheckboxes() {
  return Array.from(document.querySelectorAll('input[name="selected"]'));
}

function dirCheckboxes() {
  return Array.from(document.querySelectorAll(".dir-toggle"));
}

function globToRegex(pattern) {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");

  return new RegExp("^" + escaped + "$");
}

function initSelectPage() {
  const checkAll = document.getElementById("checkAll");
  const uncheckAll = document.getElementById("uncheckAll");
  const applyIgnore = document.getElementById("applyIgnore");
  const ignoreInput = document.getElementById("ignore_patterns");

  if (!checkAll || !uncheckAll || !applyIgnore || !ignoreInput) {
    return;
  }

  checkAll.addEventListener("click", () => {
    fileCheckboxes().forEach((checkbox) => {
      checkbox.checked = true;
    });

    dirCheckboxes().forEach((checkbox) => {
      checkbox.checked = true;
    });
  });

  uncheckAll.addEventListener("click", () => {
    fileCheckboxes().forEach((checkbox) => {
      checkbox.checked = false;
    });

    dirCheckboxes().forEach((checkbox) => {
      checkbox.checked = false;
    });
  });

  dirCheckboxes().forEach((dirCheckbox) => {
    dirCheckbox.addEventListener("change", () => {
      const prefix = dirCheckbox.dataset.dir + "/";

      fileCheckboxes().forEach((fileCheckbox) => {
        if (fileCheckbox.dataset.path.startsWith(prefix)) {
          fileCheckbox.checked = dirCheckbox.checked;
        }
      });
    });
  });

  applyIgnore.addEventListener("click", () => {
    const patterns = ignoreInput.value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    if (!patterns.length) {
      return;
    }

    const regexes = patterns.map(globToRegex);
    fileCheckboxes().forEach((fileCheckbox) => {
      const ignored = regexes.some((regex) => regex.test(fileCheckbox.dataset.path));
      if (ignored) {
        fileCheckbox.checked = false;
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initSelectPage();
});
