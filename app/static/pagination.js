function initializePagination(container) {
  const pageSize = Number.parseInt(container.dataset.pageSize, 10);
  const items = Array.from(container.querySelectorAll("[data-page-item]"));
  const controls = container.querySelector("[data-pagination-controls]");

  if (!controls || !Number.isFinite(pageSize) || pageSize <= 0 || items.length <= pageSize) {
    return;
  }

  const totalPages = Math.ceil(items.length / pageSize);
  let pageIndex = 0;

  const previousButton = document.createElement("button");
  previousButton.type = "button";
  previousButton.className = "pagination-button";
  previousButton.textContent = "이전";

  const pageStatus = document.createElement("span");
  pageStatus.className = "pagination-status";

  const nextButton = document.createElement("button");
  nextButton.type = "button";
  nextButton.className = "pagination-button";
  nextButton.textContent = "다음";

  controls.append(previousButton, pageStatus, nextButton);
  controls.hidden = false;

  function renderPage() {
    const start = pageIndex * pageSize;
    const end = start + pageSize;
    const visibleEnd = Math.min(end, items.length);

    items.forEach((item, index) => {
      item.hidden = index < start || index >= end;
    });

    previousButton.disabled = pageIndex === 0;
    nextButton.disabled = pageIndex === totalPages - 1;
    pageStatus.textContent = `${start + 1}-${visibleEnd} / ${items.length}`;
  }

  previousButton.addEventListener("click", () => {
    pageIndex = Math.max(pageIndex - 1, 0);
    renderPage();
  });

  nextButton.addEventListener("click", () => {
    pageIndex = Math.min(pageIndex + 1, totalPages - 1);
    renderPage();
  });

  renderPage();
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-paginated]").forEach(initializePagination);
});
