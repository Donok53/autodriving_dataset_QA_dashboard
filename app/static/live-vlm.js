function initLiveVlm(panel) {
  const image = panel.querySelector("[data-live-vlm-image]");
  const form = panel.querySelector("[data-live-vlm-form]");
  const urlInput = panel.querySelector("[data-live-vlm-url]");
  const status = panel.querySelector("[data-live-vlm-status]");
  const openLink = panel.querySelector("[data-live-vlm-open]");

  if (!image || !form || !urlInput) {
    return;
  }

  function pageUrl(streamUrl) {
    try {
      const parsed = new URL(streamUrl);
      return `${parsed.origin}/`;
    } catch (_error) {
      return streamUrl;
    }
  }

  function setStatus(text, className) {
    if (!status) {
      return;
    }
    status.textContent = text;
    status.classList.remove("good", "warn", "bad");
    status.classList.add(className);
  }

  function connect(streamUrl) {
    const trimmed = streamUrl.trim();
    if (!trimmed) {
      return;
    }
    const cacheBuster = `t=${Date.now()}`;
    const separator = trimmed.includes("?") ? "&" : "?";
    image.src = `${trimmed}${separator}${cacheBuster}`;
    if (openLink) {
      openLink.href = pageUrl(trimmed);
    }
    setStatus("연결 중", "warn");
  }

  image.addEventListener("load", () => setStatus("수신 중", "good"));
  image.addEventListener("error", () => setStatus("오프라인", "bad"));

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    connect(urlInput.value);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-live-vlm]").forEach(initLiveVlm);
});
