# GitHub Copilot repository instructions

Before modifying this repository, read `/AGENTS.md`.

For GitHub Actions, CI, test failures, hangs, queued/stale runs, or recovery work:

- read `/.github/GITHUB_AGENT.md`;
- search for the matching open `[GitHub Agent][AI Repair]` Issue;
- inspect the full referenced Actions Run, failed jobs/steps, logs, source commit, and current branch state before changing code;
- do not weaken tests merely to make CI pass;
- make repairs on a dedicated branch and use a PR instead of writing directly to `main`;
- run the original failing test, relevant regression tests, and GitHub Agent Policy Check;
- do not blindly replay Release / Deploy / Publish / Store Package workflows.

GitHub Agent itself does not call a coding-agent provider. Treat its AI Repair Brief as an evidence and handoff package, then perform the code reasoning from repository facts.
