# Contributing to Smoothie

Thanks for your interest in contributing! Smoothie is an AI-powered
animation add-on for Blender, and outside contributions — bug reports,
fixes, features, docs — are welcome.

## Reporting bugs and requesting features

Open a GitHub issue. For bugs, please include:

- Blender version
- Smoothie version
- Operating system
- A minimal reproduction, or at least a description of what you did and
  what happened
- Any relevant output from `logs/smoothie.log` or `logs/sidecar.log`

## Submitting code changes

1. Fork the repository and create a topic branch off `main`.
2. Keep changes focused — one logical change per pull request is easier to
   review than a sprawling refactor.
3. Match the existing code style. The project has no autoformatter
   configured, so mirror what's already there.
4. If you're touching the `executor`, `ai`, or `sandbox` code, run the
   unit tests under `tests/scripts/` first.
5. Open a pull request against `main` with a clear description of what the
   change does and why.

## Contributor License Agreement

Before any outside contribution can be merged, you'll need to sign
Smoothie's Contributor License Agreement ([CLA.md](CLA.md)).

In plain terms: **you keep the copyright on your contribution**, but you
grant the project a broad license to use and relicense it. This preserves
the project's ability to evolve over time — including the option to
re-license under alternative terms — without needing to track down every
past contributor for permission.

**How signing works in practice**: when you open your first pull request,
an automated check will post a comment with a link to sign the CLA. You
click the link, authenticate with your GitHub account, and agree to the
terms. That's it — your signature is recorded once and covers all future
pull requests from the same GitHub account. You don't need to do anything
in the PR itself, and you don't need to sign again for subsequent
contributions.

Please read [CLA.md](CLA.md) in full before signing. If anything in it is
unclear or doesn't fit your situation (for example, if your employer
claims rights to code you write), open an issue to discuss before
submitting a pull request, so we can work out how to proceed.

## Code of conduct

Be kind. Assume good faith. Disagreements are fine; personal attacks are
not.
