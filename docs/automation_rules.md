# Automation Rules — Background Monitoring (Lean SLA Model)

**Project:** ATOM (Test Machine Booking & Access Management)  
**Scope:** Formal specification for all time-based automation executed by the background monitoring system.  
**Rule set version:** `automation_rules_v1.0`  
**Out of scope:** Implementation code, schema redesign, access-window state machine.

---

## 1. Purpose

This document defines the **time-based rules** used by the background monitoring system to:
- identify overdue Access Requests awaiting approval
- trigger warnings and breaches against agreed thresholds
- automatically expire stale requests (optional but recommended)
- produce consistent, auditable system actions

This ruleset exists to ensure all automation is:
- deterministic
- testable
- auditable
- consistent across the UI and background jobs

---

## 2. Terminology and Assumptions

### 2.1 Time Source and Timezone
- All automation compares timestamps in **UTC**.
- Database timestamps are treated as UTC (even if displayed in local time).

### 2.2 “Now”
- “Now” (`now_utc`) is the timestamp captured at the start of each monitoring job run.
- All evaluations within that run use the same `now_utc`.

### 2.3 Entity in Scope: Access Request
An **Access Request** is a permission-gate record that represents a user request to gain authorised access (e.g., to a site / controlled resource), requiring an approval decision.

Access Requests are expected to have (at minimum):
- an identifier (`id`)
- a `status`
- a creation timestamp (`created_at`)
- optionally decision fields (`resolved_at`, `resolved_by`, `decision_note`)
- optionally a status/audit history record

---

## 3. Canonical Status Values

These are the **approved** status values that the system should use consistently in docs, code, and UI.

### 3.1 AccessRequest.status
| Status | Meaning |
|---|---|
| `PENDING` | Awaiting decision/approval |
| `APPROVED` | Access granted |
| `REJECTED` | Access denied |
| `REVOKED` | Access was granted then withdrawn |
| `EXPIRED` | Automatically closed due to exceeding time limits (system action) |

**Notes**
- Only `PENDING` is subject to SLA evaluation in this lean ruleset.
- `EXPIRED` is optional in implementation, but recommended for strong automation evidence.

---

## 4. SLA Thresholds (Lean)

These thresholds are intentionally simple and easy to demonstrate. Adjust only by updating this document and bumping the rule version.

### 4.1 Overdue Approval SLA
**Applies when:** `status == PENDING`

- **SLA start time:** `created_at`
- **Warning threshold:** `created_at + 8 hours`
- **Breach threshold:** `created_at + 48 hours`

### 4.2 Auto-expiry (Recommended)
**Applies when:** `status == PENDING`

- **Expiry threshold:** `created_at + 7 days`
- **Outcome:** transition to `EXPIRED`

---

## 5. Classification Rules

For a given Access Request in `PENDING`, compute elapsed time:

`age = now_utc - created_at`

Classify as:

1. **OK**
   - Condition: `age < 8 hours`
   - Meaning: No SLA action required.

2. **SLA_WARNING_APPROVAL**
   - Condition: `8 hours <= age < 48 hours`
   - Meaning: Approvals are overdue soon; prompt action.

3. **SLA_BREACH_APPROVAL**
   - Condition: `48 hours <= age < 7 days`
   - Meaning: SLA is breached; escalate.

4. **AUTO_EXPIRE**
   - Condition: `age >= 7 days`
   - Meaning: Request is stale; close automatically.

**Priority rule:** If multiple thresholds could apply, choose the most severe by time:
`AUTO_EXPIRE` > `SLA_BREACH_APPROVAL` > `SLA_WARNING_APPROVAL` > `OK`

---

## 6. Actions (Spec-Level)

The rule engine returns **structured actions**. The action handler applies them.

### 6.1 Action Types
| Action | Description |
|---|---|
| `NOTIFY_WARNING` | Queue a warning notification |
| `NOTIFY_BREACH` | Queue a breach/escalation notification |
| `STATUS_SET_EXPIRED` | Change status from `PENDING` to `EXPIRED` |
| `AUDIT_EVENT` | Write a structured audit record of automated action |

### 6.2 Action Rules
#### A) Warning
If classification is `SLA_WARNING_APPROVAL`:
- Queue `NOTIFY_WARNING`
- Write `AUDIT_EVENT` with reason code `SLA_WARNING_APPROVAL`

#### B) Breach
If classification is `SLA_BREACH_APPROVAL`:
- Queue `NOTIFY_BREACH`
- Write `AUDIT_EVENT` with reason code `SLA_BREACH_APPROVAL`

#### C) Auto-expire
If classification is `AUTO_EXPIRE`:
- Apply `STATUS_SET_EXPIRED`
- Queue `NOTIFY_BREACH` (or a dedicated “expired” message — implementation choice)
- Write `AUDIT_EVENT` with reason code `AUTO_EXPIRE`

---

## 7. Idempotency and De-duplication Rules

Automation must be safe to run repeatedly without spamming users.

### 7.1 Notification De-duplication
- A given request should receive **at most one** warning notification for `SLA_WARNING_APPROVAL`.
- A given request should receive **at most one** breach notification for `SLA_BREACH_APPROVAL`.
- Auto-expiry should occur **once**.

### 7.2 How to Achieve De-duplication (Implementation-Agnostic)
Any one of the following is acceptable:
- check audit/history records for existing reason codes before sending
- store a “last_notified_at + notification_type” marker
- store boolean markers (e.g., `warning_sent`, `breach_sent`) — only if absolutely necessary

This document requires the behaviour; it does not mandate a storage mechanism.

---

## 8. Audit Event Specification (System Actions)

All automated actions must write a structured audit event.

### 8.1 System Actor Definition
System-driven events must identify the actor as:
- `actor_type = SYSTEM`
- `actor_id = scheduler`
- If the schema uses `changed_by_user_id`, system actions MUST be represented as `NULL` (and treated as system in UI).

### 8.2 Minimum Audit Fields
| Field | Type | Example |
|---|---|---|
| `event_id` | string/uuid | `...` |
| `timestamp_utc` | ISO-8601 | `2026-03-02T15:10:00Z` |
| `actor_type` | enum | `SYSTEM` |
| `actor_id` | string | `scheduler` |
| `entity_type` | enum | `AccessRequest` |
| `entity_id` | int/string | `123` |
| `action` | enum | `SLA_WARNING_SENT`, `SLA_BREACH_SENT`, `STATUS_CHANGED` |
| `previous_status` | string | `PENDING` |
| `new_status` | string/null | `EXPIRED` |
| `reason_code` | enum | `SLA_WARNING_APPROVAL`, `SLA_BREACH_APPROVAL`, `AUTO_EXPIRE` |
| `rule_version` | string | `automation_rules_v1.0` |
| `details` | JSON | `{ "age_hours": 52, "warning_hours": 8, "breach_hours": 48 }` |

### 8.3 Audit Rules
- If no change occurs, do not log an event.
- If multiple actions occur in one run (e.g., expire + notify), either:
  - log one combined event with multiple action tags, or
  - log multiple events with the same `event_id` grouping key.

---

## 9. Acceptance Checklist (Task 19)

- [ ] `docs/automation_rules.md` created/updated and committed
- [ ] Thresholds explicitly defined: warning, breach, expiry
- [ ] Canonical status values agreed and documented
- [ ] Audit fields and system actor definition documented
- [ ] No implementation code included

---
