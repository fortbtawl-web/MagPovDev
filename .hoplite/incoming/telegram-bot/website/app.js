(function () {
  var cfg = window.SITE_CONFIG || {};
  var botUrl = "https://t.me/" + (cfg.botUsername || "");
  document.querySelectorAll("[data-bot]").forEach(function (a) { a.href = botUrl; a.target = "_blank"; a.rel = "noopener"; });
  document.querySelectorAll("[data-support]").forEach(function (a) { a.href = cfg.supportUrl || botUrl; a.target = "_blank"; a.rel = "noopener"; });

  var box = document.getElementById("plan-list");
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function fail() {
    box.textContent = "";
    var p = el("p", "muted", "Не удалось загрузить тарифы. Актуальные цены — в боте.");
    box.appendChild(p);
    var a = el("a", "btn", "Открыть бота"); a.href = botUrl; a.target = "_blank"; a.rel = "noopener";
    box.appendChild(a);
  }

  fetch((cfg.apiBase || "").replace(/\/$/, "") + "/api/plans", { headers: { Accept: "application/json" } })
    .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
    .then(function (data) {
      if (!data.plans || !data.plans.length) return fail();
      box.textContent = "";
      data.plans.forEach(function (p) {
        var card = el("article", "plan" + (p.is_lifetime ? " lifetime" : ""));
        if (p.is_lifetime) card.appendChild(el("span", "badge", "Навсегда"));
        card.appendChild(el("h3", null, p.title));
        card.appendChild(el("div", "price", p.price_label));
        card.appendChild(el("p", null, p.terms));
        if (p.description) card.appendChild(el("p", "muted", p.description));
        var a = el("a", "btn", "Купить в Telegram"); a.href = botUrl; a.target = "_blank"; a.rel = "noopener";
        card.appendChild(a);
        box.appendChild(card);
      });
      if (data.payments_available === false) {
        box.appendChild(el("p", "muted", "Оплата сейчас подключается — купить тариф пока нельзя."));
      }
    })
    .catch(fail);
})();
