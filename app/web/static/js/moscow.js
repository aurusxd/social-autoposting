(() => {
  "use strict";

  // The panel plans posts in Moscow time whatever the browser is set to.
  // Moscow has kept a fixed UTC+3 without DST since 2014, so a plain offset
  // matches the server without pulling in a timezone database.
  const OFFSET_MINUTES = 180;

  const pad = (value) => String(value).padStart(2, "0");

  const format = (date) =>
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;

  window.MoscowTime = {
    /** Moscow wall clock `minutesAhead` from now, for `datetime-local`. */
    inputValue: (minutesAhead = 0) =>
      format(new Date(Date.now() + (OFFSET_MINUTES + minutesAhead) * 60000)),
  };
})();
