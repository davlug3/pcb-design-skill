(() => {
  "use strict";

  const sheet = document.getElementById("sheet");
  const questionsEl = document.getElementById("questions");
  const loadingEl = document.getElementById("loading");
  const alertEl = document.getElementById("alert");
  const titleEl = document.getElementById("title");
  const introEl = document.getElementById("intro");
  const progressText = document.getElementById("progress-text");
  const progressBar = document.getElementById("progress-bar");
  const dockState = document.getElementById("dock-state");
  const dockHint = document.getElementById("dock-hint");
  const submitButton = document.getElementById("submit");
  const doneEl = document.getElementById("done");
  const doneSummary = document.getElementById("done-summary");
  const copyButton = document.getElementById("copy");
  const copiedEl = document.getElementById("copied");

  const state = {
    payload: null,
    answers: Object.create(null),
    submitting: false,
    lastAnswers: null,
  };

  const h = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };

  const truncate = (value, length) => {
    const text = String(value || "").trim();
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  };

  const monogram = (label, id) => {
    const source = String(label || id || "?");
    const packageMatch = source.match(/\b(?:0[24]02|0[36]03|0805|1206|1210|SOT-?\d+|SOIC-?\d+|TQFP-?\d+|QFN-?\d+|DIP-?\d+)\b/i);
    if (packageMatch) return packageMatch[0].replace("-", "");
    const words = source.match(/[A-Za-z0-9]+/g) || ["?"];
    return words.slice(0, 2).join("").slice(0, 5).toUpperCase();
  };

  function initQuestionState(question) {
    if (question.type === "multi") return { value: [], custom: "" };
    if (question.type === "text") return { value: "" };
    if (question.type === "confirm") return { value: null };
    return { value: null, custom: "" };
  }

  function optionById(question, id) {
    return (question.options || []).find((option) => option.id === id) || null;
  }

  function isAnswered(question) {
    const answer = state.answers[question.id];
    if (!answer) return false;
    switch (question.type) {
      case "footprint":
        return Boolean(answer.custom.trim() || answer.value);
      case "choice":
        return Boolean(answer.value);
      case "multi":
        return answer.value.length >= Math.max(1, question.min_selections || 0);
      case "text":
        return Boolean(answer.value.trim());
      case "confirm":
        return typeof answer.value === "boolean";
      default:
        return false;
    }
  }

  function answerLabel(question) {
    const answer = state.answers[question.id];
    if (!answer) return "";
    if (question.type === "footprint") {
      if (answer.custom.trim()) return answer.custom.trim();
      const option = optionById(question, answer.value);
      return option ? option.label : "";
    }
    if (question.type === "choice") {
      const option = optionById(question, answer.value);
      return option ? option.label : "";
    }
    if (question.type === "multi") {
      return answer.value
        .map((id) => optionById(question, id))
        .filter(Boolean)
        .map((option) => option.label)
        .join(", ");
    }
    if (question.type === "confirm") {
      return answer.value === true ? "Yes" : answer.value === false ? "No" : "";
    }
    return truncate(answer.value, 160);
  }

  function updateProgress() {
    if (!state.payload) return;
    const questions = state.payload.questions;
    const answered = questions.filter(isAnswered).length;
    const requiredLeft = questions.filter((question) => question.required && !isAnswered(question)).length;
    progressText.textContent = `${answered} of ${questions.length} answered`;
    progressBar.style.width = `${questions.length ? (answered / questions.length) * 100 : 0}%`;
    submitButton.disabled = requiredLeft > 0 || state.submitting;
    submitButton.textContent = state.submitting ? "Sending…" : state.payload.submit_label || "Send answers";
    if (state.submitting) {
      dockState.textContent = "Sending answers";
      dockHint.textContent = "Writing the answer file for the agent.";
    } else if (requiredLeft > 0) {
      dockState.textContent = `${requiredLeft} required ${requiredLeft === 1 ? "answer" : "answers"} left`;
      dockHint.textContent = "Required questions must be answered before sending.";
    } else {
      dockState.textContent = "Ready to send";
      dockHint.textContent = "Answers are written locally for the agent to read.";
    }
  }

  function makeVisual(option, question) {
    const visual = h("span", "option__visual");
    if (option.preview_svg) {
      const template = document.createElement("template");
      template.innerHTML = option.preview_svg;
      const svg = template.content.firstElementChild;
      if (svg && svg.tagName.toLowerCase() === "svg") {
        visual.appendChild(svg);
        return visual;
      }
    }
    if (option.image) {
      const img = document.createElement("img");
      img.src = option.image;
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      visual.appendChild(img);
      return visual;
    }
    if (option.swatch) {
      const swatch = h("span", "option__swatch");
      swatch.style.background = option.swatch;
      visual.appendChild(swatch);
      return visual;
    }
    visual.appendChild(h("span", "option__monogram", monogram(option.label, option.id)));
    return visual;
  }

  function optionMeta(option, question) {
    if (question.type !== "footprint") return "";
    const meta = option.meta || {};
    const bits = [];
    if (meta.pads) bits.push(`${meta.pads} ${meta.pads === 1 ? "pad" : "pads"}`);
    if (Array.isArray(meta.size_mm) && meta.size_mm.length === 2) {
      bits.push(`${meta.size_mm[0]} × ${meta.size_mm[1]} mm`);
    }
    if (meta.through_hole) bits.push("through-hole");
    else if (meta.pads) bits.push("SMD");
    return bits.join(" · ");
  }

  function syncSelection(question) {
    const section = document.querySelector(`[data-question="${question.id}"]`);
    if (!section) return;
    const answer = state.answers[question.id];
    const usingCustom = question.type === "footprint" && Boolean(answer.custom.trim());
    section.querySelectorAll(".option").forEach((label) => {
      const input = label.querySelector(".option__input");
      if (!input) return;
      let selected;
      if (usingCustom) {
        selected = false;
      } else if (input.type === "checkbox") {
        selected = answer.value.includes(input.value);
      } else if (question.type === "confirm") {
        selected = (answer.value === true && input.value === "yes") || (answer.value === false && input.value === "no");
      } else {
        selected = input.value === answer.value;
      }
      input.checked = selected;
      label.classList.toggle("is-selected", selected);
    });
  }

  function selectOption(question, value) {
    const answer = state.answers[question.id];
    if (question.type === "multi") {
      if (answer.value.includes(value)) {
        answer.value = answer.value.filter((item) => item !== value);
      } else {
        answer.value = [...answer.value, value];
      }
    } else {
      answer.value = value;
      if (question.type === "footprint") {
        answer.custom = "";
        const custom = document.getElementById(`custom-${question.id}`);
        if (custom) custom.value = "";
      }
    }
    syncSelection(question);
    updateProgress();
  }

  function renderRadio(question, option, { full = false, confirm = false } = {}) {
    const label = h(
      "label",
      `option${full ? "" : " option--plain"}${confirm ? " option--confirm" : ""}`,
    );
    label.dataset.optionId = option.id;
    const input = document.createElement("input");
    input.className = "option__input";
    input.type = "radio";
    input.name = `q_${question.id}`;
    input.value = option.id;
    if (question.required) input.required = true;
    if (!confirm) input.addEventListener("change", () => selectOption(question, option.id));
    label.appendChild(input);

    if (full) {
      label.appendChild(makeVisual(option, question));
    } else if (option.swatch || option.preview_svg || option.image) {
      label.appendChild(makeVisual(option, question));
    }

    const text = h("span", "option__text");
    text.appendChild(h("span", "option__label", option.label));
    if (option.description) {
      const desc = h("span", "option__desc", truncate(option.description, 160));
      desc.title = option.description;
      text.appendChild(desc);
    }
    const metaText = optionMeta(option, question);
    if (metaText) text.appendChild(h("span", "option__meta", metaText));
    if (option.error) text.appendChild(h("span", "option__flag", "Preview unavailable — still selectable"));
    label.appendChild(text);
    label.appendChild(h("span", "option__check"));
    return label;
  }

  function renderCheckbox(question, option) {
    const label = h("label", "option option--plain");
    label.dataset.optionId = option.id;
    const input = document.createElement("input");
    input.className = "option__input";
    input.type = "checkbox";
    input.name = `q_${question.id}`;
    input.value = option.id;
    input.addEventListener("change", () => selectOption(question, option.id));
    label.appendChild(input);
    if (option.swatch || option.preview_svg || option.image) {
      label.appendChild(makeVisual(option, question));
    }
    const text = h("span", "option__text");
    text.appendChild(h("span", "option__label", option.label));
    if (option.description) {
      const desc = h("span", "option__desc", truncate(option.description, 160));
      desc.title = option.description;
      text.appendChild(desc);
    }
    const metaText = optionMeta(option, question);
    if (metaText) text.appendChild(h("span", "option__meta", metaText));
    label.appendChild(text);
    label.appendChild(h("span", "option__check"));
    return label;
  }

  function renderFootprint(question, body, promptId) {
    const full = question.options.some((option) => option.preview_svg || option.image);
    const group = h("div", `options${full ? "" : " options--plain"}`);
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-labelledby", promptId);
    if (question.required) group.setAttribute("aria-required", "true");
    question.options.forEach((option) => group.appendChild(renderRadio(question, option, { full })));
    body.appendChild(group);

    if (question.allow_custom) {
      const custom = h("div", "custom");
      const label = h("label", "custom__label", question.custom_label || "Or enter a KiCad footprint ID");
      label.htmlFor = `custom-${question.id}`;
      const input = document.createElement("input");
      input.className = "custom__input";
      input.id = `custom-${question.id}`;
      input.type = "text";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.placeholder = question.custom_placeholder || "Library:Footprint";
      input.addEventListener("input", () => {
        const answer = state.answers[question.id];
        answer.custom = input.value;
        if (answer.custom.trim()) {
          group.querySelectorAll(".option__input").forEach((item) => {
            item.checked = false;
          });
        }
        syncSelection(question);
        updateProgress();
      });
      custom.appendChild(label);
      custom.appendChild(input);
      custom.appendChild(
        h(
          "span",
          "custom__hint",
          "Use the exact KiCad ID, for example Resistor_SMD:R_0805_2012Metric.",
        ),
      );
      body.appendChild(custom);
    }
  }

  function renderChoice(question, body, promptId) {
    const full = question.options.some((option) => option.preview_svg || option.image);
    const group = h("div", `options${full ? "" : " options--plain"}`);
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-labelledby", promptId);
    if (question.required) group.setAttribute("aria-required", "true");
    question.options.forEach((option) => group.appendChild(renderRadio(question, option, { full })));
    body.appendChild(group);
  }

  function renderMulti(question, body, promptId) {
    const group = h("div", "options options--plain");
    group.setAttribute("role", "group");
    group.setAttribute("aria-labelledby", promptId);
    question.options.forEach((option) => group.appendChild(renderCheckbox(question, option)));
    body.appendChild(group);
  }

  function renderText(question, body, promptId) {
    const textarea = document.createElement("textarea");
    textarea.className = "textarea";
    textarea.id = `text-${question.id}`;
    textarea.rows = 4;
    textarea.placeholder = question.placeholder || "Type your answer…";
    textarea.setAttribute("aria-labelledby", promptId);
    if (question.required) textarea.required = true;
    textarea.addEventListener("input", () => {
      state.answers[question.id].value = textarea.value;
      updateProgress();
    });
    body.appendChild(textarea);
  }

  function renderConfirm(question, body, promptId) {
    const group = h("div", "options options--plain");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-labelledby", promptId);
    if (question.required) group.setAttribute("aria-required", "true");
    const options = [
      { id: "yes", label: "Yes" },
      { id: "no", label: "No" },
    ];
    options.forEach((option) => {
      const label = renderRadio(question, option, { confirm: true });
      label.querySelector(".option__input").addEventListener("change", () => {
        state.answers[question.id].value = option.id === "yes";
        syncSelection(question);
        updateProgress();
      });
      group.appendChild(label);
    });
    body.appendChild(group);
  }

  function renderQuestion(question, index) {
    const section = h("section", "question");
    section.dataset.question = question.id;
    section.appendChild(h("div", "question__index", String(index + 1).padStart(2, "0")));

    const body = h("div", "question__body");
    const promptId = `prompt-${question.id}`;
    const prompt = h("h2", "question__prompt");
    prompt.id = promptId;
    prompt.appendChild(document.createTextNode(question.prompt));
    if (question.required) {
      const required = h("span", "question__req", " *");
      required.setAttribute("aria-hidden", "true");
      prompt.appendChild(required);
      prompt.appendChild(h("span", "sr-only", " required"));
    }
    body.appendChild(prompt);
    if (question.help) body.appendChild(h("p", "question__help", question.help));

    switch (question.type) {
      case "footprint":
        renderFootprint(question, body, promptId);
        break;
      case "choice":
        renderChoice(question, body, promptId);
        break;
      case "multi":
        renderMulti(question, body, promptId);
        break;
      case "text":
        renderText(question, body, promptId);
        break;
      case "confirm":
        renderConfirm(question, body, promptId);
        break;
      default:
        body.appendChild(h("p", "question__help", `Unsupported question type: ${question.type}`));
    }

    section.appendChild(body);
    return section;
  }

  function render() {
    const payload = state.payload;
    titleEl.textContent = payload.title;
    introEl.textContent = payload.intro || "";
    document.title = `${payload.title} · PCB design`;
    questionsEl.textContent = "";
    payload.questions.forEach((question, index) => {
      state.answers[question.id] = initQuestionState(question);
      questionsEl.appendChild(renderQuestion(question, index));
    });
    updateProgress();
  }

  function showError(message, details) {
    alertEl.textContent = "";
    alertEl.appendChild(document.createTextNode(message || "Something went wrong."));
    if (Array.isArray(details) && details.length) {
      const list = document.createElement("ul");
      details.forEach((detail) => {
        const item = document.createElement("li");
        const where = detail.question ? `${detail.question}: ` : "";
        item.textContent = `${where}${detail.message || detail}`;
        list.appendChild(item);
      });
      alertEl.appendChild(list);
    }
    alertEl.hidden = false;
    alertEl.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function buildAnswers() {
    const answers = {};
    state.payload.questions.forEach((question) => {
      const answer = state.answers[question.id];
      if (question.type === "footprint") {
        const custom = answer.custom.trim();
        answers[question.id] = custom ? { value: custom, custom } : { value: answer.value };
      } else if (question.type === "multi") {
        answers[question.id] = { value: answer.value };
      } else if (question.type === "text") {
        answers[question.id] = { value: answer.value };
      } else {
        answers[question.id] = { value: answer.value };
      }
    });
    return answers;
  }

  function showDone(answers, session) {
    state.lastAnswers = { session, answers };
    sheet.hidden = true;
    doneEl.hidden = false;
    doneSummary.textContent = "";
    state.payload.questions.forEach((question) => {
      const row = document.createElement("div");
      row.className = "done__row";
      const term = h("dt", "", question.prompt);
      const value = h("dd", "", answerLabel(question) || "—");
      row.appendChild(term);
      row.appendChild(value);
      doneSummary.appendChild(row);
    });
    doneEl.scrollIntoView({ behavior: "smooth", block: "start" });
    copyButton.focus({ preventScroll: true });
  }

  async function submit() {
    if (state.submitting) return;
    const requiredLeft = state.payload.questions.filter(
      (question) => question.required && !isAnswered(question),
    ).length;
    if (requiredLeft) {
      showError(`Please answer ${requiredLeft} more required ${requiredLeft === 1 ? "question" : "questions"}.`);
      return;
    }
    state.submitting = true;
    alertEl.hidden = true;
    updateProgress();
    const answers = buildAnswers();
    try {
      const response = await fetch("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        state.submitting = false;
        updateProgress();
        showError(data.error || `The server returned HTTP ${response.status}.`, data.errors);
        return;
      }
      showDone(answers, data.session);
    } catch (error) {
      state.submitting = false;
      updateProgress();
      showError("Could not reach the local question server. Is it still running?");
    }
  }

  async function load() {
    try {
      const response = await fetch("/api/payload", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.payload = await response.json();
      render();
    } catch (error) {
      loadingEl.hidden = true;
      titleEl.textContent = "Could not load the questions";
      introEl.textContent = "The local question server did not respond. Check that it is still running, then reload this page.";
      document.getElementById("dock").hidden = true;
      showError(String(error.message || error));
    }
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    try {
      return document.execCommand("copy");
    } finally {
      area.remove();
    }
  }

  submitButton.addEventListener("click", submit);
  copyButton.addEventListener("click", async () => {
    if (!state.lastAnswers) return;
    try {
      const copied = await copyText(JSON.stringify(state.lastAnswers, null, 2));
      copiedEl.textContent = copied ? "Copied" : "Copy failed";
      setTimeout(() => {
        copiedEl.textContent = "";
      }, 1800);
    } catch (error) {
      copiedEl.textContent = "Copy failed";
    }
  });

  load();
})();
