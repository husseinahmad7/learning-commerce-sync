"use strict";

const scenarios = {
  normal: {
    status: "Accepted",
    events: ["Payment and outbox entry are saved together.", "The worker records an ERP invoice using a stable reference.", "The separate mock tax authority accepts the report and returns a receipt."],
    next: "No retry needed. Keep the provider response for audit and reconciliation."
  },
  offline: {
    status: "Pending send",
    events: ["Payment and outbox entry are saved together.", "The accounting service is unavailable before submission.", "The invoice stays pending; the tax report waits, while the learner can still enroll."],
    next: "Retry with a delay, and alert the operator before the reporting deadline. Use a fallback only if it is configured and authorized."
  },
  unknown: {
    status: "Outcome unknown",
    events: ["Payment and outbox entry are saved together.", "The mock authority saves the report, but the reply is lost.", "The worker looks up the original result and records the original receipt without sending a second report."],
    next: "Reconcile through a supported provider mechanism or ask an operator to check. Do not submit a second invoice just because the response timed out."
  },
  rejected: {
    status: "Rejected",
    events: ["Payment and outbox entry are saved together.", "The mock authority explicitly rejects the report.", "The worker records the rejection and holds the report for correction; enrollment is unaffected."],
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
