/* ClearML Authentik SLO watcher. Does not initiate Authentik login. */
(function () {
  'use strict';
  var observedActive = false;
  var stopped = false;
  function check() {
    if (stopped || document.visibilityState === 'hidden') return;
    fetch('/sso/session-status', {credentials: 'same-origin', cache: 'no-store'})
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (status) {
        if (!status || stopped) return;
        if (status.monitored && status.active) observedActive = true;
        if (observedActive && status.monitored && !status.active) {
          stopped = true;
          window.location.replace('/sso/logged-out');
        }
      })
      .catch(function () { /* transient failures do not force a browser logout */ });
  }
  check();
  setInterval(check, 5000);
  document.addEventListener('visibilitychange', check);
})();
