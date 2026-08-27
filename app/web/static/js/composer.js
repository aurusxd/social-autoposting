(() => {
  "use strict";

  const form = document.getElementById("composer");
  if (!form) {
    return;
  }

  const mediaLimit = Number(form.dataset.mediaLimit || 10);
  const caption = document.getElementById("caption");
  const captionCount = document.getElementById("caption-count");
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const mediaList = document.getElementById("media-list");
  const mediaCount = document.getElementById("media-count");
  const targetsBox = document.getElementById("targets");
  const refreshButton = document.getElementById("refresh-targets");
  const submitButton = document.getElementById("submit");
  const resetButton = document.getElementById("reset");
  const message = document.getElementById("composer-message");

  /** @type {{token: string, fileName: string, mediaType: string, sizeLabel: string, previewUrl: string}[]} */
  const media = [];
  let uploading = 0;

  // Text ---------------------------------------------------------------

  const updateCaptionCount = () => {
    captionCount.textContent = String(caption.value.length);
  };
  caption.addEventListener("input", updateCaptionCount);
  updateCaptionCount();

  // Messages -----------------------------------------------------------

  const say = (text, kind = "") => {
    message.textContent = text;
    message.className = "composer-message" + (kind ? ` is-${kind}` : "");
  };

  const errorFrom = async (response) => {
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        return payload.detail;
      }
      if (Array.isArray(payload.detail) && payload.detail.length) {
        return payload.detail[0].msg || "Проверьте заполненные поля";
      }
    } catch (_) {
      /* the body was not JSON */
    }
    return `Ошибка ${response.status}`;
  };

  const expired = (response) => {
    if (response.status === 401) {
      window.location.href = "/login";
      return true;
    }
    return false;
  };

  // Media --------------------------------------------------------------

  const renderMedia = () => {
    mediaCount.textContent = `${media.length} из ${mediaLimit}`;
    mediaList.replaceChildren();

    media.forEach((item, index) => {
      const row = document.createElement("li");
      row.className = "media-item";

      const preview =
        item.mediaType === "photo"
          ? document.createElement("img")
          : document.createElement("video");
      preview.className = "media-preview";
      preview.src = item.previewUrl;
      if (item.mediaType === "photo") {
        preview.alt = item.fileName;
        preview.loading = "lazy";
      } else {
        preview.muted = true;
        preview.preload = "metadata";
      }

      const info = document.createElement("div");
      info.className = "media-info";
      const name = document.createElement("span");
      name.className = "media-name";
      name.textContent = item.fileName;
      const meta = document.createElement("span");
      meta.className = "hint";
      meta.textContent = `${item.mediaType === "photo" ? "фото" : "видео"} · ${item.sizeLabel}`;
      info.append(name, meta);

      const actions = document.createElement("div");
      actions.className = "media-actions";
      actions.append(
        iconButton("↑", "Выше", index === 0, () => move(index, -1)),
        iconButton("↓", "Ниже", index === media.length - 1, () => move(index, 1)),
        iconButton("✕", "Удалить", false, () => remove(index)),
      );

      row.append(preview, info, actions);
      mediaList.append(row);
    });
  };

  const iconButton = (label, title, disabled, onClick) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "icon-button";
    button.textContent = label;
    button.title = title;
    button.disabled = disabled;
    button.addEventListener("click", onClick);
    return button;
  };

  const move = (index, offset) => {
    const target = index + offset;
    if (target < 0 || target >= media.length) {
      return;
    }
    [media[index], media[target]] = [media[target], media[index]];
    renderMedia();
  };

  const remove = async (index) => {
    const [item] = media.splice(index, 1);
    renderMedia();
    try {
      await fetch("/api/media/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: item.token }),
      });
    } catch (_) {
      /* the file stays on disk until the next cleanup; nothing to show */
    }
  };

  const uploadFile = (file) =>
    new Promise((resolve, reject) => {
      const row = document.createElement("li");
      row.className = "media-item";
      const info = document.createElement("div");
      info.className = "media-info";
      const name = document.createElement("span");
      name.className = "media-name";
      name.textContent = file.name;
      const progress = document.createElement("div");
      progress.className = "progress";
      const bar = document.createElement("div");
      bar.className = "progress-bar";
      progress.append(bar);
      info.append(name, progress);
      row.append(info);
      mediaList.append(row);

      const body = new FormData();
      body.append("file", file);

      const request = new XMLHttpRequest();
      request.open("POST", "/api/media");
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          bar.style.width = `${(event.loaded / event.total) * 100}%`;
        }
      });
      request.addEventListener("load", () => {
        row.remove();
        if (request.status === 401) {
          window.location.href = "/login";
          reject(new Error("unauthorized"));
          return;
        }
        let payload = {};
        try {
          payload = JSON.parse(request.responseText);
        } catch (_) {
          /* keep the generic message below */
        }
        if (request.status !== 201) {
          reject(new Error(payload.detail || `Не удалось загрузить ${file.name}`));
          return;
        }
        resolve({
          token: payload.token,
          fileName: payload.file_name,
          mediaType: payload.media_type,
          sizeLabel: payload.size_label,
          previewUrl: payload.preview_url,
        });
      });
      request.addEventListener("error", () => {
        row.remove();
        reject(new Error(`Сеть прервала загрузку ${file.name}`));
      });
      request.send(body);
    });

  const addFiles = async (files) => {
    const queue = Array.from(files);
    if (!queue.length) {
      return;
    }
    if (media.length + uploading + queue.length > mediaLimit) {
      say(`В одном посте не больше ${mediaLimit} файлов`, "error");
      return;
    }

    uploading += queue.length;
    updateSubmitState();
    for (const file of queue) {
      try {
        media.push(await uploadFile(file));
        say("");
      } catch (error) {
        say(error.message, "error");
      } finally {
        uploading -= 1;
      }
      renderMedia();
      updateSubmitState();
    }
  };

  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-over");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-over");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer?.files?.length) {
      addFiles(event.dataTransfer.files);
    }
  });

  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });

  // Targets ------------------------------------------------------------

  const renderTargets = (groups) => {
    targetsBox.replaceChildren();
    if (!groups.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Нет доступных площадок.";
      targetsBox.append(empty);
      return;
    }

    groups.forEach((group) => {
      const fieldset = document.createElement("fieldset");
      fieldset.className = "target-group";
      fieldset.dataset.platform = group.platform;

      const legend = document.createElement("legend");
      legend.className = "target-legend";
      const label = document.createElement("span");
      label.textContent = group.label;
      const selectAll = document.createElement("button");
      selectAll.type = "button";
      selectAll.className = "button button-link";
      selectAll.dataset.selectAll = group.platform;
      selectAll.textContent = "Все";
      legend.append(label, selectAll);

      const grid = document.createElement("div");
      grid.className = "target-grid";
      group.targets.forEach((target) => {
        const item = document.createElement("label");
        item.className = "target";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.name = "targets";
        input.value = target.id;
        const name = document.createElement("span");
        name.className = "target-name";
        name.textContent = target.name;
        const kind = document.createElement("span");
        kind.className = "target-kind";
        kind.textContent = target.kind_label;
        item.append(input, name, kind);
        grid.append(item);
      });

      fieldset.append(legend, grid);
      targetsBox.append(fieldset);
    });
  };

  targetsBox.addEventListener("click", (event) => {
    const platform = event.target.dataset?.selectAll;
    if (!platform) {
      return;
    }
    const boxes = targetsBox.querySelectorAll(
      `[data-platform="${platform}"] input[type="checkbox"]`,
    );
    const turnOn = Array.from(boxes).some((box) => !box.checked);
    boxes.forEach((box) => {
      box.checked = turnOn;
    });
    updateSubmitState();
  });

  targetsBox.addEventListener("change", updateSubmitState);

  refreshButton.addEventListener("click", async () => {
    const chosen = new Set(selectedTargets());
    refreshButton.disabled = true;
    say("Обновляю список площадок…");
    try {
      const response = await fetch("/api/targets?refresh=true");
      if (expired(response)) {
        return;
      }
      if (!response.ok) {
        say(await errorFrom(response), "error");
        return;
      }
      const payload = await response.json();
      renderTargets(payload.groups);
      targetsBox.querySelectorAll('input[name="targets"]').forEach((box) => {
        box.checked = chosen.has(box.value);
      });
      if (payload.whatsapp_failed) {
        say("Чаты WhatsApp получить не удалось — показаны остальные площадки", "error");
      } else {
        say("Список обновлён", "success");
      }
    } catch (_) {
      say("Не удалось обновить список площадок", "error");
    } finally {
      refreshButton.disabled = false;
      updateSubmitState();
    }
  });

  // Submit --------------------------------------------------------------

  function selectedTargets() {
    return Array.from(
      targetsBox.querySelectorAll('input[name="targets"]:checked'),
    ).map((box) => box.value);
  }

  function updateSubmitState() {
    const ready =
      uploading === 0 &&
      selectedTargets().length > 0 &&
      (caption.value.trim().length > 0 || media.length > 0);
    submitButton.disabled = !ready;
  }

  caption.addEventListener("input", updateSubmitState);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const targets = selectedTargets();
    if (!targets.length) {
      say("Выберите хотя бы одну площадку", "error");
      return;
    }

    submitButton.disabled = true;
    say("Публикую…");
    try {
      const response = await fetch("/api/posts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          caption: caption.value,
          media: media.map((item) => item.token),
          targets,
        }),
      });
      if (expired(response)) {
        return;
      }
      if (!response.ok) {
        say(await errorFrom(response), "error");
        return;
      }
      const payload = await response.json();
      window.location.href = `/posts/${payload.post_id}`;
    } catch (_) {
      say("Сеть недоступна, пост не отправлен", "error");
    } finally {
      updateSubmitState();
    }
  });

  resetButton.addEventListener("click", async () => {
    caption.value = "";
    updateCaptionCount();
    while (media.length) {
      await remove(0);
    }
    targetsBox.querySelectorAll('input[name="targets"]').forEach((box) => {
      box.checked = false;
    });
    say("");
    updateSubmitState();
  });

  renderMedia();
  updateSubmitState();
})();
