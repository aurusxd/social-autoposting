(() => {
  "use strict";

  const card = document.querySelector("[data-post-id]");
  if (!card) {
    return;
  }

  const postId = card.dataset.postId;
  const message = document.getElementById("post-message");
  const retryButton = document.getElementById("retry");
  const deleteButton = document.getElementById("delete-post");
  const scheduleInput = document.getElementById("schedule-at");
  const rescheduleButton = document.getElementById("reschedule");
  const publishNowButton = document.getElementById("publish-now");

  const say = (text, kind = "") => {
    message.textContent = text;
    message.className = "composer-message" + (kind ? ` is-${kind}` : "");
  };

  const detailFrom = async (response) => {
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        return payload.detail;
      }
    } catch (_) {
      /* the body was not JSON */
    }
    return `Ошибка ${response.status}`;
  };

  const call = async (path, button, onSuccess, body) => {
    button.disabled = true;
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!response.ok) {
        say(await detailFrom(response), "error");
        return;
      }
      onSuccess(response);
    } catch (_) {
      say("Сеть недоступна, попробуйте ещё раз", "error");
    } finally {
      button.disabled = false;
    }
  };

  retryButton?.addEventListener("click", () => {
    say("Ставлю задания обратно в очередь…");
    call(`/api/posts/${postId}/retry`, retryButton, () => {
      window.location.reload();
    });
  });

  if (scheduleInput) {
    scheduleInput.min = window.MoscowTime.inputValue(1);
  }

  rescheduleButton?.addEventListener("click", () => {
    if (!scheduleInput.value) {
      say("Укажите дату и время публикации", "error");
      return;
    }
    say("Переношу публикацию…");
    call(
      `/api/posts/${postId}/schedule`,
      rescheduleButton,
      () => {
        window.location.reload();
      },
      // Bare Moscow wall clock; the server converts it to UTC.
      { scheduled_at: scheduleInput.value },
    );
  });

  publishNowButton?.addEventListener("click", () => {
    if (!window.confirm("Опубликовать пост прямо сейчас?")) {
      return;
    }
    say("Ставлю пост в очередь…");
    call(
      `/api/posts/${postId}/schedule`,
      publishNowButton,
      () => {
        window.location.reload();
      },
      { scheduled_at: null },
    );
  });

  deleteButton?.addEventListener("click", () => {
    if (!window.confirm("Удалить пост вместе с вложениями?")) {
      return;
    }
    call(`/api/posts/${postId}/delete`, deleteButton, () => {
      window.location.href = "/history";
    });
  });
})();
