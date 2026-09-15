---
name: Security Vulnerability
about: Report a security concern confidentially
title: "[SECURITY] Confidential vulnerability report"
labels: ["security", "triage"]
assignees: []
---

## Vulnerability Type
[e.g., SQL Injection, XSS, CSRF, Authentication Bypass, Privilege Escalation]

## Impact Assessment
How severe is this issue? What data/functions could be compromised?

## Reproduction Steps
1. ...
2. ...
3. ...

## Proof of Concept
Minimal code snippet or curl command demonstrating exploit:

```bash
curl -X POST http://localhost:8000/api/v1/... \
  -d '{"malicious": "payload"}'
```

## Mitigation (if known)
Have you identified how to fix this? Or any workarounds?

---

**CONFIDENTIAL**: Please treat this as private. Do not publish details until
remediated.
