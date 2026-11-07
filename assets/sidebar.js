(function () {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "sidebar-toggle";
  btn.setAttribute("aria-label", "Menu");
  btn.textContent = "Menu";
  document.body.appendChild(btn);
  btn.addEventListener("click", function () {
    var bar = document.getElementById("hermes-sidebar");
    if (bar) bar.classList.toggle("is-open");
  });
})();
