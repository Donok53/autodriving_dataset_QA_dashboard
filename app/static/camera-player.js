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
  const playButton = player.querySelector("[data-camera-play]");
  const previousButton = player.querySelector("[data-camera-prev]");
  const nextButton = player.querySelector("[data-camera-next]");
  const seeker = player.querySelector("[data-camera-seeker]");
  const status = player.querySelector("[data-camera-status]");
  const topic = player.querySelector("[data-camera-topic]");
  const details = player.querySelector("[data-camera-details]");
  const frameButtons = Array.from(player.querySelectorAll("[data-camera-frame]"));
  const frames = frameButtons.map((button) => {
    const thumb = button.querySelector("img");
    return {
      button,
      src: thumb?.getAttribute("src") || "",
      topic: button.dataset.frameTopic || "",
      timestamp: button.dataset.frameTimestamp || "",
      meta: button.dataset.frameMeta || "",
    };
  });

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

    frameButtons.forEach((button, buttonIndex) => {
      button.classList.toggle("active", buttonIndex === currentIndex);
    });
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
      setFrame(index);
    });
  });

  setFrame(0);
}

cameraPlayers.forEach(initCameraPlayer);
