# Claude implements, Codex reviews

The [28-second GIF](feature-review.gif) shows the real interactive Claude terminal and the live Claudex conversation for its Codex reviewer.
Claude implements CSV export while Codex provides a fresh-eyes review. The opening shows the expanded `agents_launch` request,
pauses on the returned task handle for eight seconds, and shows Claude confirming that the review is running in the background.
Then the browser shows that same agent, with a follow-up queued and steered into its active turn.

The [longer feature walkthrough](https://github.com/danielasher115/claudex/releases/download/v0.1.0-alpha.1/feature-review-live.webm)
is hosted as a release asset, outside Git history. It covers an earlier complete run of the same feature.
In that run, Codex finds a formula-injection bypass involving a leading line feed;
Claude fixes it, adds regression coverage, and finishes with 51 passing tests.

## Capture

The normal Claude terminal and Chromium ran on a private Linux Xvfb display, connected to a dedicated local Claudex gateway and real Codex agent.
`scripts/record_screen.py` captured their actual pixels. The white pointer and click pulse are the only added visuals, following observed X11 input.
The GIF uses chronological excerpts resized to 900 pixels wide. The launch request, returned task handle, and background-review confirmation
are held for reading. Moving footage plays at normal speed, sampled at ten frames per second; waiting time is cut. Playback is not task duration.
No product UI, terminal output, or agent results are reconstructed. Source footage and private QA images remain outside the repository.

The expense tracker and its data were created for this demonstration. Account details and unrelated user conversations are excluded.
See [direct recording instructions](../development.md#direct-screen-recording).
