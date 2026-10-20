// Prep chat (F8): one form for everything. A suggested question fills the box and sends it;
// the question shows at once with a "reading" bubble; errors unlock the box instead of hanging.
// Delegated on document because HTMX replaces #chat-box after every answer.
(function () {
  function parts(el) {
    const box = el.closest("#chat-box");
    const form = box && box.querySelector(".chat-form");
    return { box, form, input: form && form.querySelector('input[name="message"]') };
  }

  document.addEventListener("click", (e) => {
    const button = e.target.closest("[data-suggest]");
    if (!button) return;
    const { form, input } = parts(button);
    if (!form || form.classList.contains("is-waiting")) return;
    input.value = button.dataset.suggest;
    form.requestSubmit();
  });

  // Runs after HTMX has collected the form values, so changing the box here can't change the request.
  document.addEventListener("htmx:beforeRequest", (e) => {
    const form = e.detail.elt;
    if (!(form instanceof HTMLElement) || !form.matches(".chat-form")) return;
    const { box, input } = parts(form);
    const text = input.value.trim();
    if (!text) { e.preventDefault(); input.focus(); return; }

    let thread = box.querySelector(".chat-thread");
    if (!thread) {
      thread = document.createElement("ol");
      thread.className = "chat-thread";
      box.querySelector(".chat-head").after(thread);
    }
    const question = document.createElement("li");
    question.className = "msg msg-user";
    question.textContent = text;
    const pending = document.createElement("li");
    pending.className = "msg msg-assistant msg-pending";
    pending.innerHTML = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Reading the data…</span>';
    thread.append(question, pending);

    const suggest = box.querySelector(".chat-suggest");
    if (suggest) suggest.remove();
    form.classList.add("is-waiting");
    input.readOnly = true; // readOnly, not disabled: the value has already been sent
    input.value = "";
    input.placeholder = "Waiting for the answer…";
    pending.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  function fail(e, message) {
    const form = e.detail.elt;
    if (!(form instanceof HTMLElement) || !form.matches(".chat-form")) return;
    const { box, input } = parts(form);
    const pending = box.querySelector(".msg-pending");
    if (pending) {
      pending.classList.remove("msg-pending");
      pending.classList.add("msg-error");
      pending.textContent = message;
    }
    form.classList.remove("is-waiting");
    input.readOnly = false;
    input.placeholder = "Try again…";
    input.focus();
  }
  document.addEventListener("htmx:responseError", (e) =>
    fail(e, `Couldn't get an answer (error ${e.detail.xhr.status}). Your question wasn't lost; ask again.`));
  document.addEventListener("htmx:sendError", (e) =>
    fail(e, "Couldn't reach the server. Check it's running, then ask again."));
  document.addEventListener("htmx:timeout", (e) => fail(e, "That took too long. Ask again, or narrow the question."));

  // After an answer swaps in: show the newest answer and put the cursor back in the box.
  document.addEventListener("htmx:afterSettle", () => {
    const box = document.getElementById("chat-box");
    if (!box || !box.querySelector(".chat-thread")) return;
    const answers = box.querySelectorAll(".msg-assistant");
    const last = answers[answers.length - 1];
    if (last) last.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const input = box.querySelector('.chat-form input[name="message"]');
    if (input) input.focus({ preventScroll: true });
  });
})();
