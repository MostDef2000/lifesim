"use strict";

(() => {
  const errors = {
    auth_failed: "Ошибка входа, попробуйте ещё раз",
    state_mismatch: "Сессия входа устарела, попробуйте ещё раз",
    provider_error: "Провайдер входа недоступен, попробуйте позже",
    group_denied: "Вход по приглашениям. Если у вас есть приглашение — откройте ссылку из письма",
    registration_closed: "Регистрация новых игроков закрыта",
    username_taken: "Имя занято, обратитесь к администратору",
  };

  const status = document.getElementById("auth-popup-status");
  if (document.body.dataset.ok !== "true") {
    const code = document.body.dataset.error;
    status.textContent = Object.prototype.hasOwnProperty.call(errors, code)
      ? errors[code]
      : errors.auth_failed;
    return;
  }

  status.textContent = "Вход выполнен. Вернитесь в игру.";
  (async () => {
    try {
      const opener = window.opener;
      if (!opener || opener.closed) return;
      // The callback response carries Set-Cookie for the app session. Give
      // the browser a tick to commit it before telling the opener to reload
      // — otherwise the opener's boot probe can race the cookie and see 401.
      await new Promise((resolve) => setTimeout(resolve, 150));
      opener.postMessage({ type: "lifesim-auth", ok: true }, location.origin);
      window.close();
    } catch (e) {
      // The fixed return link remains usable if the opener is unavailable.
    }
  })();
})();
