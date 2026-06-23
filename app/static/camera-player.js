const cameraPlayers = document.querySelectorAll("[data-camera-player]");

function clampFrameDelay(delayMs) {
  if (!Number.isFinite(delayMs) || delayMs <= 0) {
    return 160;
  }
  return Math.min(Math.max(delayMs, 80), 500);
}

function frameDelay(frames, currentIndex, nextIndex) {
  const currentTime = Date.parse(frames[currentIndex]?.timestamp || "");
  const nextTime = Date.parse(frames[nextIndex]?.timestamp || "");
  return clampFrameDelay(nextTime - currentTime);
}

function initCameraPlayer(player) {
  const image = player.querySelector("[data-camera-player-image]");
  const manifest = player.querySelector("[data-camera-frame-manifest]");
  const playButton = player.querySelector("[data-camera-play]");
  const previousButton = player.querySelector("[data-camera-prev]");
  const nextButton = player.querySelector("[data-camera-next]");
  const seeker = player.querySelector("[data-camera-seeker]");
  const status = player.querySelector("[data-camera-status]");
  const topic = player.querySelector("[data-camera-topic]");
  const details = player.querySelector("[data-camera-details]");
  const xaiOverlay = player.querySelector("[data-camera-xai-overlay]");
  const xaiMode = player.querySelector("[data-camera-xai-mode]");
  const xaiTopic = player.querySelector("[data-camera-xai-topic]");
  const xaiExplanation = player.querySelector("[data-camera-xai-explanation]");
  const xaiEvidence = player.querySelector("[data-camera-xai-evidence]");
  const frameButtons = Array.from(player.querySelectorAll("[data-camera-frame]"));

  let manifestFrames = [];
  try {
    manifestFrames = JSON.parse(manifest?.textContent || "[]");
  } catch {
    manifestFrames = [];
  }

  const frames = manifestFrames.map((frame) => ({
    src: frame.image_url || frame.data_url || "",
    topic: frame.topic || "",
    timestamp: frame.timestamp || "",
    meta: `${frame.width || 0}x${frame.height || 0} · ${frame.encoding || ""}`,
    xaiOverlay: frame.xai_overlay || null,
  })).filter((frame) => frame.src);

  if (frames.length === 0) {
    frameButtons.forEach((button) => {
      const thumb = button.querySelector("img");
      frames.push({
        src: thumb?.getAttribute("src") || "",
        topic: button.dataset.frameTopic || "",
        timestamp: button.dataset.frameTimestamp || "",
        meta: button.dataset.frameMeta || "",
        xaiOverlay: null,
      });
    });
  }

  const thumbnailIndexes = frameButtons.map((button) => Number(button.dataset.frameIndex || 0));

  if (!image || !playButton || frames.length === 0) {
    return;
  }

  let currentIndex = 0;
  let playbackTimer = null;
  let isPlaying = false;

  function setFrame(index) {
    currentIndex = (index + frames.length) % frames.length;
    const frame = frames[currentIndex];
    image.src = frame.src;

    if (seeker) {
      seeker.value = String(currentIndex + 1);
    }
    if (status) {
      status.textContent = `${currentIndex + 1} / ${frames.length}`;
    }
    if (topic) {
      topic.textContent = frame.topic;
    }
    if (details) {
      details.textContent = `${frame.timestamp} · ${frame.meta}`;
    }
    updateXaiOverlay(frame.xaiOverlay);

    frameButtons.forEach((button, buttonIndex) => {
      button.classList.toggle("active", thumbnailIndexes[buttonIndex] === currentIndex);
    });
  }

  function updateXaiOverlay(overlay) {
    const explanation = overlay?.explanation || "";
    if (!xaiOverlay || !explanation) {
      if (xaiOverlay) {
        xaiOverlay.hidden = true;
      }
      return;
    }

    xaiOverlay.hidden = false;
    if (xaiMode) {
      xaiMode.textContent = overlay.driving_mode_ko || overlay.event_label || "XAI";
    }
    if (xaiTopic) {
      const delta = Number.isFinite(Number(overlay.delta_ms)) ? ` · ${overlay.delta_ms}ms` : "";
      xaiTopic.textContent = `${overlay.source_topic || overlay.timestamp || ""}${delta}`;
    }
    if (xaiExplanation) {
      xaiExplanation.textContent = explanation;
    }
    if (xaiEvidence) {
      xaiEvidence.textContent = overlay.evidence ? `근거: ${overlay.evidence}` : "";
      xaiEvidence.hidden = !overlay.evidence;
    }
  }

  function stopPlayback() {
    isPlaying = false;
    window.clearTimeout(playbackTimer);
    playbackTimer = null;
    playButton.textContent = "재생";
    playButton.setAttribute("aria-pressed", "false");
  }

  function scheduleNextFrame() {
    window.clearTimeout(playbackTimer);
    const nextIndex = (currentIndex + 1) % frames.length;
    playbackTimer = window.setTimeout(() => {
      setFrame(nextIndex);
      if (isPlaying) {
        scheduleNextFrame();
      }
    }, frameDelay(frames, currentIndex, nextIndex));
  }

  function startPlayback() {
    if (frames.length <= 1) {
      return;
    }
    isPlaying = true;
    playButton.textContent = "일시정지";
    playButton.setAttribute("aria-pressed", "true");
    scheduleNextFrame();
  }

  playButton.addEventListener("click", () => {
    if (isPlaying) {
      stopPlayback();
      return;
    }
    startPlayback();
  });

  previousButton?.addEventListener("click", () => {
    stopPlayback();
    setFrame(currentIndex - 1);
  });

  nextButton?.addEventListener("click", () => {
    stopPlayback();
    setFrame(currentIndex + 1);
  });

  seeker?.addEventListener("input", () => {
    stopPlayback();
    setFrame(Number(seeker.value) - 1);
  });

  frameButtons.forEach((button, index) => {
    button.addEventListener("click", () => {
      stopPlayback();
      setFrame(thumbnailIndexes[index]);
    });
  });

  setFrame(0);
}

cameraPlayers.forEach(initCameraPlayer);
