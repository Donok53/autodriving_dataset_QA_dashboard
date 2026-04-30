const uploadForm = document.querySelector("#upload-form");
const progressPanel = document.querySelector("#analysis-progress-panel");
const uploadProgressBar = document.querySelector("#upload-progress-bar");
const uploadProgressText = document.querySelector("#upload-progress-text");
const analysisProgressBar = document.querySelector("#analysis-progress-bar");
const analysisProgressText = document.querySelector("#analysis-progress-text");
const progressError = document.querySelector("#progress-error");
const submitButton = uploadForm?.querySelector("button[type='submit']");

function setProgress(bar, text, percent, label) {
  const clampedPercent = Math.max(0, Math.min(Math.round(percent), 100));
  bar.style.width = `${clampedPercent}%`;
  text.textContent = `${label} (${clampedPercent}%)`;
}

function setProgressError(message) {
  progressError.textContent = message;
  progressError.hidden = false;
  if (submitButton) {
    submitButton.disabled = false;
  }
}

function resetProgressPanel() {
  progressPanel.hidden = false;
  progressError.hidden = true;
  setProgress(uploadProgressBar, uploadProgressText, 0, "업로드 준비 중");
  setProgress(analysisProgressBar, analysisProgressText, 0, "검사 대기 중");
}

async function pollAnalysisJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) {
    setProgressError("분석 상태를 확인할 수 없습니다.");
    return;
  }

  const job = await response.json();
  setProgress(analysisProgressBar, analysisProgressText, job.progress || 0, job.stage || "검사 중");

  if (job.status === "completed") {
    window.location.href = `/jobs/${jobId}`;
    return;
  }

  if (job.status === "failed") {
    setProgressError(job.error || "분석 중 오류가 발생했습니다.");
    return;
  }

  window.setTimeout(() => pollAnalysisJob(jobId), 1000);
}

function uploadWithProgress(form) {
  const request = new XMLHttpRequest();
  const fileInput = form.querySelector("input[type='file']");
  const file = fileInput?.files?.[0];

  if (!file) {
    setProgressError("업로드할 파일을 선택해주세요.");
    return;
  }

  resetProgressPanel();
  if (submitButton) {
    submitButton.disabled = true;
  }

  request.open("POST", "/api/upload/raw");
  request.setRequestHeader("Content-Type", "application/octet-stream");
  request.setRequestHeader("X-Filename", encodeURIComponent(file.name));

  request.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) {
      uploadProgressText.textContent = "업로드 중";
      return;
    }
    const percent = (event.loaded / event.total) * 100;
    setProgress(uploadProgressBar, uploadProgressText, percent, "업로드 중");
  });

  request.addEventListener("load", () => {
    if (request.status < 200 || request.status >= 300) {
      try {
        const payload = JSON.parse(request.responseText);
        setProgressError(payload.detail || "업로드에 실패했습니다.");
      } catch {
        setProgressError("업로드에 실패했습니다.");
      }
      return;
    }

    const job = JSON.parse(request.responseText);
    setProgress(uploadProgressBar, uploadProgressText, 100, "업로드 완료");
    setProgress(analysisProgressBar, analysisProgressText, job.progress || 10, job.stage || "검사 시작");
    pollAnalysisJob(job.job_id);
  });

  request.addEventListener("error", () => {
    setProgressError("네트워크 오류로 업로드에 실패했습니다.");
  });

  request.send(file);
}

if (uploadForm && progressPanel) {
  uploadForm.addEventListener("submit", (event) => {
    event.preventDefault();
    uploadWithProgress(uploadForm);
  });
}
