# Licensing & Usage

## Quick Summary

| Use Case | Allowed? | License Needed? |
|----------|----------|-----------------|
| Personal learning and experimentation | Yes | No |
| Academic research | Yes | No |
| Development, testing, evaluation | Yes | No |
| Production use by small org (<500 employees and contractors), non-competing | Yes | No |
| Production use by large org (500+ employees and contractors) | Conditional | Commercial license |
| Offering SynthOrg as a hosted/managed service | Conditional | Commercial license |
| Reselling or embedding SynthOrg as your core product | Conditional | Commercial license |
| Contributing to SynthOrg | Yes | Sign the [CLA](https://github.com/Aureliolo/synthorg/blob/main/CLA.md) |

*"Conditional" uses require a commercial license — please [contact us](https://github.com/Aureliolo/synthorg/discussions) to discuss terms.*

---

## The License

SynthOrg is licensed under the [Business Source License 1.1](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) (BSL 1.1).

### What BSL 1.1 Means

BSL 1.1 is a "source available" license created by MariaDB. It is **not** an open-source license by the OSI definition, but it is close in spirit:

- **Source code is public** — you can read, fork, modify, and redistribute it
- **Non-production use is unrestricted** — learning, research, testing, evaluation, contributing
- **Production use is governed by the Additional Use Grant** — our grant is deliberately permissive (see below)
- **Every version automatically converts to Apache 2.0** — SynthOrg sets the Change Date to 3 years after each release (the BSL 1.1 terms also include a 4-year backstop, but our shorter Change Date always applies first)

### Our Additional Use Grant

The Additional Use Grant is the part that distinguishes one BSL project from another. Ours is designed to be as permissive as possible while protecting against one specific scenario: a well-funded company taking SynthOrg and offering it as a competing commercial service without contributing back.

You **can** use SynthOrg freely in production if both conditions are met:

1. **Not Competing Use** — you are not offering SynthOrg itself (or a product whose substantial value derives from SynthOrg's functionality) to third parties on a hosted, managed, or embedded basis
2. **Small Organization** — your organization (including affiliates) has fewer than 500 employees and individual contractors

If you meet both conditions, you need **no separate license** — just use it.

### What Requires a Commercial License

- **Competing Use by any organization** — offering SynthOrg as a hosted or managed service, regardless of size
- **Production use by organizations with 500+ employees** — even for internal use

!!! tip "Commercial licenses may be free"

    We are generally open to granting commercial licenses at no cost, especially for:

    - Organizations that contribute back to the project
    - Non-profits and educational institutions
    - Startups that grew past the 500-employee threshold while using SynthOrg
    - Companies using SynthOrg for internal tooling (not as a competing product)

    **Just ask.** Open an issue, start a discussion, or email us.

---

## Why BSL 1.1?

### The Problem with Permissive Licenses

Permissive licenses (MIT, Apache 2.0) are wonderful for adoption but create an asymmetry: a cloud provider can take the project, offer it as a managed service, capture most of the value, and contribute nothing back. This has happened repeatedly in the open-source ecosystem (Redis, Elasticsearch, MongoDB, Terraform, etc.).

### The Problem with Restrictive Licenses

Copyleft licenses (AGPL, SSPL) attempt to fix this but create friction for legitimate users. Many companies have blanket policies against AGPL software, even for internal use. This limits adoption and community growth.

### The BSL Middle Ground

BSL 1.1 threads the needle:

- **For most users, it behaves like Apache 2.0** — read, modify, use in production, no strings attached
- **It only restricts the specific behavior we want to prevent** — competing commercial use without engagement
- **It automatically becomes Apache 2.0** — every version converts on a fixed schedule, so there is zero long-term lock-in

This is the same approach used by CockroachDB, Sentry, MariaDB MaxScale, and others. It has a strong track record.

### Why Not AGPL?

AGPL would require anyone running SynthOrg as a service to release their modifications. While this sounds fair, in practice:

- Many companies have blanket "no AGPL" policies, even for internal use
- It creates legal uncertainty around what constitutes a "derivative work"
- It discourages adoption by exactly the users we want to attract (internal tool builders)

### Why the 500-Employee Threshold?

The threshold exists to distinguish between companies that can reasonably engage in a licensing conversation and those that cannot. A 10-person startup using SynthOrg to automate internal workflows should not need to negotiate a license. A 5,000-person enterprise can afford a conversation — and we will often grant a free license anyway.

The number 500 is a convention borrowed from other BSL projects and roughly aligns with the EU's definition of "large enterprise."

---

## Automatic Conversion to Apache 2.0

Every released version of SynthOrg converts to Apache 2.0 automatically.

SynthOrg sets the **Change Date** to **3 years after each release**. The BSL 1.1 license terms also include a built-in 4-year backstop (each version converts on its 4th anniversary regardless), but since our Change Date is shorter, it always takes effect first.

This means:

- No version is ever "locked" under BSL forever
- If SynthOrg is abandoned, all versions become Apache 2.0 on schedule
- You can plan around a guaranteed open-source conversion date

---

## Contributor License Agreement (CLA)

We require a [Contributor License Agreement](https://github.com/Aureliolo/synthorg/blob/main/CLA.md) before merging external contributions. The CLA:

- Grants SynthOrg a non-exclusive license to your contributions
- **Does not transfer ownership** — you retain full rights to your work
- Enables dual-licensing (BSL for the community, commercial licenses for enterprises)
- Is based on the Apache ICLA template, widely used in open source

### Why a CLA?

Without a CLA, every contributor retains exclusive copyright over their code. This makes it legally impossible to offer commercial licenses (even free ones), because we would need permission from every contributor individually. The CLA grants that permission upfront.

This is the same model used by Apache Software Foundation, Google, Meta, and many other projects.

### How to Sign

When you open your first pull request, a bot will comment with instructions. Reply with the specified text — no forms, no external services. Your signature is recorded in the repository.

---

## Feedback & Discussions

We are genuinely open to feedback on the licensing model. If you:

- Think the terms are too restrictive for your use case
- Have questions about whether your use requires a commercial license
- Want to discuss the rationale behind any decision
- Have suggestions for improving the terms

Please [open a discussion](https://github.com/Aureliolo/synthorg/discussions) or [create an issue](https://github.com/Aureliolo/synthorg/issues). We would rather have the conversation than lose a user.

---

## Frequently Asked Questions

### Can I use SynthOrg in my company's internal tools?

**Yes**, if your company has fewer than 500 employees (including contractors and affiliates). No license needed.

If your company has 500+ employees, contact us — we are likely to grant a free commercial license for internal use.

### Can I build a product that uses SynthOrg?

**Yes**, as long as SynthOrg is not the "substantial value" of your product. If you are building an application that happens to use SynthOrg for AI agent orchestration internally, that is fine. If your product is essentially "SynthOrg as a service," that is Competing Use.

### Can I fork SynthOrg and modify it?

**Yes.** The BSL explicitly permits copying, modifying, creating derivative works, and redistributing. The production use restrictions in the Additional Use Grant apply to the derivative work as well.

### What happens when the license converts to Apache 2.0?

The version becomes fully open source under Apache 2.0, with no restrictions whatsoever. You can use it for any purpose, including competing use, at any organization size.

### Is this "open source"?

Technically, no — BSL 1.1 is not OSI-approved. It is "source available." However, for most users, the practical experience is identical to open source: you can read, modify, and use the code freely. The restriction only applies to a narrow set of commercial scenarios.

### Can I use SynthOrg for a hackathon, course, or workshop?

**Yes** for learning, prototyping, and evaluation — these are non-production use and always permitted. If your hackathon project goes into production (e.g., deployed as a live service), the normal Additional Use Grant conditions apply.
