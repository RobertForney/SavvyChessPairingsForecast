/* Installation and connection status for the shared browser/mobile shell. */
(() => {
  "use strict";

  const installButton = document.getElementById("install-app");
  const installDialog = document.getElementById("install-dialog");
  const instructions = document.getElementById("install-instructions");
  const banner = document.getElementById("connection-banner");
  const standalone = window.matchMedia("(display-mode: standalone)");
  let installPrompt = null;
  let backendAvailable = null;
  let healthCheck = null;

  function isInstalled() {
    return standalone.matches || navigator.standalone === true;
  }

  function updateInstallButton() {
    installButton.hidden = isInstalled();
  }

  function renderConnection() {
    const disconnected = !navigator.onLine || backendAvailable === false;
    banner.hidden = !disconnected;
    if (!disconnected) {
      banner.replaceChildren();
      return;
    }
    const message = document.createElement("span");
    message.textContent = !navigator.onLine
      ? "You’re offline. A saved workspace, when available, is read only. Reconnect for current forecasts and tournament replay."
      : "The pairing service is unavailable. A saved workspace, when available, is read only. Current forecasts and tournament replay require the service.";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary";
    retry.textContent = "Reconnect";
    retry.disabled = !navigator.onLine || healthCheck !== null;
    retry.addEventListener("click", () => {
      checkBackend().then((available) => {
        // Refresh only after a deliberate retry, so an automatic reconnect does
        // not discard an in-progress form. The main app restores its live state.
        if (available) document.dispatchEvent(new Event("savvy:reconnect"));
      });
    });
    banner.replaceChildren(message, retry);
  }

  function checkBackend() {
    if (healthCheck) return healthCheck;
    if (!navigator.onLine) return Promise.resolve(false);
    healthCheck = (async () => {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 8000);
      let available = false;
      try {
        const response = await fetch("/api/health", {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        const result = response.ok ? await response.json() : null;
        available = result?.ok === true;
      } catch {
        available = false;
      } finally {
        window.clearTimeout(timer);
      }
      document.dispatchEvent(new CustomEvent("savvy:backend", {
        detail: { available },
      }));
      return available;
    })().finally(() => {
      healthCheck = null;
      renderConnection();
    });
    renderConnection();
    return healthCheck;
  }

  document.addEventListener("savvy:backend", (event) => {
    if (typeof event.detail?.available === "boolean") {
      backendAvailable = event.detail.available;
      renderConnection();
    }
  });
  window.addEventListener("offline", renderConnection);
  window.addEventListener("online", () => {
    // Connectivity alone does not prove that the pairing service is reachable.
    backendAvailable = false;
    renderConnection();
    void checkBackend();
  });

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    updateInstallButton();
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    installButton.hidden = true;
  });
  standalone.addEventListener?.("change", updateInstallButton);

  installButton.addEventListener("click", async () => {
    if (installPrompt) {
      const prompt = installPrompt;
      installPrompt = null;
      try {
        await prompt.prompt();
        const choice = await prompt.userChoice;
        if (choice.outcome === "accepted") installButton.hidden = true;
      } catch {
        showInstallInstructions();
      }
      return;
    }
    showInstallInstructions();
  });

  function showInstallInstructions() {
    const appleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent)
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const appleDesktop = /Mac/.test(navigator.platform) && !appleMobile;
    if (appleMobile) {
      instructions.textContent = "Open this website in Safari, tap Share, then Add to Home Screen. If shown, turn on Open as Web App and tap Add. If Add to Home Screen is hidden, use Edit Actions in the Share menu.";
    } else if (appleDesktop) {
      instructions.textContent = "In Safari on macOS Sonoma or later, choose File → Add to Dock, then Add. In Chrome or Edge, use the browser’s Install app option when available.";
    } else if (/Android/.test(navigator.userAgent)) {
      instructions.textContent = "In Chrome, open the browser menu and choose Add to Home screen or Install app. Follow the browser’s confirmation. Menu wording can vary by browser and device.";
    } else {
      instructions.textContent = "In Chrome or Edge, choose the Install app icon in the address bar or the browser menu when available. You can also use Savvy Pairings directly in this browser.";
    }
    if (!window.isSecureContext) {
      instructions.textContent += " Installation requires this app to be served over HTTPS. Localhost is supported for development on the same computer.";
    }
    installDialog.showModal();
  }

  document.getElementById("close-install").addEventListener("click", () => {
    installDialog.close();
  });

  if ("serviceWorker" in navigator && window.isSecureContext) {
    // The worker caches a fixed list of public shell assets. API responses,
    // uploaded files, and the sample workbook never enter its cache.
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      // The online workspace remains usable if browser policy blocks workers.
    });
  }
  updateInstallButton();
  renderConnection();
})();
