(function () {
  var el = document.getElementById("status");
  var text = document.getElementById("status-text");

  function check() {
    fetch("/api/v1/health")
      .then(function (r) {
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.json();
      })
      .then(function (data) {
        var s = data.data && data.data.status;
        if (s === "healthy") {
          el.className = "status status-connected";
          text.textContent = "Backend connected (v" + (data.data && data.data.version || "?") + ")";
        } else {
          el.className = "status status-disconnected";
          text.textContent = "Backend unhealthy (" + (s || "unknown") + ")";
        }
      })
      .catch(function () {
        el.className = "status status-disconnected";
        text.textContent = "Backend unreachable";
      });
  }

  check();
  setInterval(check, 15000);
})();
