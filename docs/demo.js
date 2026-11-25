"use strict";

const scenarios = {
  normal: {
    status: "Accepted",
    events: ["Payment and outbox entry are saved together.", "The worker submits the invoice using a stable reference.", "The provider response is recorded."],
    next: "No retry needed. Keep the provider response for audit and reconciliation."
  },
  offline: {
    status: "Pending send",
    events: ["Payment and outbox entry are saved together.", "The accounting service is unavailable before submission.", "The pending invoice remains visible outside the unavailable service."],
    next: "Retry with a delay, and alert the operator before the reporting deadline. Use a fallback only if it is configured and authorized."
  },
  unknown: {
    status: "Outcome unknown",
    events: ["Payment and outbox entry are saved together.", "Submission starts, but the response is lost.", "The system keeps the invoice reference and attempt record."],
    next: "Reconcile through a supported provider mechanism or ask an operator to check. Do not submit a second invoice just because the response timed out."
  },
  rejected: {
    status: "Rejected",
    events: ["Payment and outbox entry are saved together.", "The provider explicitly rejects the submitted data.", "The rejection and its reason are recorded."],
    next: "Tell the billing team what needs correction. Do not retry the same invalid payload."
  }
};

const group = document.querySelector("#scenarios");
const status = document.querySelector("#trace-status");
const events = document.querySelector("#trace-events");
const next = document.querySelector("#trace-next");

function showScenario(key) {
  const scenario = scenarios[key];
  if (!scenario) return;

  status.textContent = scenario.status;
  events.replaceChildren(...scenario.events.map((description) => {
    const step = document.createElement("li");
    step.textContent = description;
    return step;
  }));
  next.textContent = scenario.next;
}

group.addEventListener("change", (event) => {
  if (event.target.matches('input[name="scenario"]')) showScenario(event.target.value);
});

showScenario(group.querySelector(":checked").value);
document.querySelector(".walkthrough").hidden = false;
document.querySelector("#trace-fallback").hidden = true;
