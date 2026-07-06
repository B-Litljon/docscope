// @ts-check
// Webview UI: lays out doc cards (newest on top), handles pin + open-source.
// No documentation logic lives here — cards arrive as pre-rendered HTML.
(function () {
  const vscode = acquireVsCodeApi();
  const cardsEl = document.getElementById("cards");
  const statusEl = document.getElementById("status");
  const emptyEl = document.getElementById("empty");
  const MAX_UNPINNED = 25;

  function refreshEmpty() {
    emptyEl.style.display = cardsEl.children.length === 0 ? "block" : "none";
  }

  function prune() {
    const unpinned = [...cardsEl.querySelectorAll(".card:not(.pinned)")];
    while (unpinned.length > MAX_UNPINNED) {
      const el = unpinned.shift();
      if (el) el.remove();
    }
  }

  function makeCard(card) {
    const el = document.createElement("div");
    el.className = "card" + (card.isError ? " error" : "");
    el.dataset.id = String(card.id);

    const header = document.createElement("div");
    header.className = "card-header";

    const title = document.createElement("div");
    title.className = "card-title";
    title.textContent = card.symbol;

    const meta = document.createElement("div");
    meta.className = "card-meta";
    if (card.version) {
      const badge = document.createElement("span");
      badge.className = "badge" + (card.exact ? " exact" : "");
      badge.textContent = card.version + (card.exact ? "" : " ~");
      meta.appendChild(badge);
    }
    if (card.tier) {
      const tier = document.createElement("span");
      tier.className = "tier";
      tier.textContent = card.tier;
      meta.appendChild(tier);
    }

    const actions = document.createElement("div");
    actions.className = "card-actions";

    const pin = document.createElement("button");
    pin.className = "icon-btn pin";
    pin.title = "Pin card";
    pin.textContent = "📌";
    pin.addEventListener("click", () => {
      el.classList.toggle("pinned");
      pin.classList.toggle("active");
    });
    actions.appendChild(pin);

    if (card.sourceUrl) {
      const src = document.createElement("button");
      src.className = "icon-btn src";
      src.title = "Open source docs";
      src.textContent = "↗";
      src.addEventListener("click", () =>
        vscode.postMessage({ type: "openSource", url: card.sourceUrl })
      );
      actions.appendChild(src);
    }

    header.appendChild(title);
    header.appendChild(meta);
    header.appendChild(actions);

    const body = document.createElement("div");
    body.className = "card-body";
    body.innerHTML = card.html;

    el.appendChild(header);
    el.appendChild(body);
    return el;
  }

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg.type === "card") {
      cardsEl.insertBefore(makeCard(msg.card), cardsEl.firstChild);
      prune();
      refreshEmpty();
    } else if (msg.type === "state") {
      statusEl.textContent = msg.state === "connected" ? "" : "⚠ daemon " + msg.state;
      statusEl.className = "status" + (msg.state === "connected" ? "" : " warn");
    }
  });

  refreshEmpty();
})();
